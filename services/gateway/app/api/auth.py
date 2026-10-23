"""G1 / G14 / G22: browser login, revocation and synthetic customer labels."""

from fastapi import APIRouter, Request
from safebank_contracts.codes import Code
from sqlalchemy import select, update

from .. import locks
from .. import tables as t
from ..audit.chain import append_tx
from ..auth.pin import CUSTOMER_LABELS, authenticate
from ..auth.tokens import check_revocation, issue_app
from ..errors import GatewayError
from ..idempotency import request_idempotent
from ..utterance.lifecycle import cancel_session
from ..utterance.models import Login, RequestId
from ..utterance.runtime import db, private_key, wall_now
from ..utterance.service import new_id

router = APIRouter()


@router.post("/auth/login")
async def login(request: Request, body: Login):
    database = db(request)
    now = wall_now(request)
    hashes = getattr(request.app.state, "pin_hashes", None)
    if hashes is None:
        raise GatewayError(
            Code.DEPENDENCY_UNAVAILABLE, 503, message="PIN configuration unavailable"
        )
    # Throttle errors commit before raising; wrong PINs must not roll back counters.
    async with database.begin() as conn:
        code = await authenticate(
            conn,
            body.customer_id,
            body.pin,
            request.client.host if request.client else "unknown",
            now,
            hashes,
        )
        if code is None:
            token, expiry = await issue_app(conn, private_key(request), body.customer_id, now)
    if code is not None:
        raise GatewayError(code, 429 if code is Code.RATE_LIMITED else 401)
    await append_tx(
        database,
        customer_id=body.customer_id,
        event_type="AUTH_LOGIN",
        payload={},
        event_id=new_id("evt"),
        now_wall=now,
    )
    return {
        "app_token": token,
        "expires_at_wall": expiry.isoformat(),
        "customer": {
            "customer_id": body.customer_id,
            "display_name": CUSTOMER_LABELS[body.customer_id],
        },
    }


@router.post("/auth/logout")
async def logout(request: Request, body: RequestId):
    caller = request.state.caller
    now = wall_now(request)
    async with locks.customer_lock([caller.customer_id]) as conn:
        async with conn.begin():
            await check_revocation(conn, caller, now=now)

            async def produce():
                sessions = (
                    (
                        await conn.execute(
                            select(t.sessions.c.id).where(
                                t.sessions.c.customer_id == caller.customer_id,
                                t.sessions.c.revoked_at_wall.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for sid in sessions:
                    await cancel_session(conn, sid, now)
                await conn.execute(
                    update(t.sessions)
                    .where(
                        t.sessions.c.customer_id == caller.customer_id,
                        t.sessions.c.revoked_at_wall.is_(None),
                    )
                    .values(revoked_at_wall=now)
                )
                await conn.execute(
                    update(t.auth_sessions)
                    .where(
                        t.auth_sessions.c.customer_id == caller.customer_id,
                        t.auth_sessions.c.revoked_at_wall.is_(None),
                    )
                    .values(revoked_at_wall=now)
                )
                return 200, {"revoked": True}

            _, result, replayed = await request_idempotent(
                conn,
                customer_id=caller.customer_id,
                route="/auth/logout",
                request_id=body.request_id,
                body=body.model_dump(mode="json"),
                produce=produce,
            )
    if not replayed:
        await append_tx(
            db(request),
            customer_id=caller.customer_id,
            event_type="AUTH_LOGOUT",
            payload={},
            event_id=new_id("evt"),
            now_wall=now,
        )
    return result


@router.get("/auth/demo-customers")
async def demo_customers(request: Request):
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    if not (settings.demo_mode or settings.eval_mode):
        raise GatewayError(Code.NOT_FOUND, 404)
    return {
        "items": [
            {"customer_id": id_, "display_name": name} for id_, name in CUSTOMER_LABELS.items()
        ]
    }
