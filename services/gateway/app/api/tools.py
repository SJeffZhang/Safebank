"""G2: caller-scoped L3 reads, version-pinned evidence and persistent read ledger."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from pydantic import ValidationError
from safebank_contracts.base import Contract
from safebank_contracts.codes import Code
from safebank_contracts.state import BankState
from safebank_contracts.tools import READ_ARGS, Id
from safebank_contracts.views import READ_RESULTS
from sqlalchemy import insert, select

from ..errors import GatewayError
from ..l3.client import L3BusinessError, L3Client, L3Error
from ..schemas.enums import ServiceKind
from ..schemas.read import ReadEnvelope
from ..tables import read_records, sessions
from ..taint.entities import entity_ids, owned
from ..taint.records import paths

router = APIRouter()
_TRUST = yaml.safe_load((Path(__file__).resolve().parents[2] / "policies/trust.yaml").read_text())


class ReadRequest(Contract):
    session_id: Id
    args: dict[str, Any]


def _source_id() -> str:
    # ULID: 48-bit wall-clock milliseconds followed by 80 cryptographic random bits.
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = ((time.time_ns() // 1_000_000) << 80) | secrets.randbits(80)
    return "src_" + "".join(alphabet[(value >> shift) & 31] for shift in range(125, -1, -5))


def untrusted_paths(tool: str, data: Any) -> tuple[str, ...]:
    return paths(data, frozenset(_TRUST[tool]))


async def fetch_read(
    l3: L3Client, customer_id: str, tool: str, args: dict, *, expected: BankState | None = None
) -> ReadEnvelope:
    """No database transaction spans these HTTP calls.

    L3 currently returns plain data. Two identical versioned snapshots bracket the
    read; a concurrent write invalidates the result instead of mislabelling it.
    """
    if tool not in READ_ARGS:
        raise GatewayError(Code.TOOL_NOT_ALLOWED, 403)
    try:
        args = READ_ARGS[tool].model_validate(args).model_dump(mode="json")
    except ValidationError as exc:
        raise GatewayError(Code.CONTRACT_INVALID, 422) from exc
    try:
        state = BankState.model_validate(
            await l3.snapshot({"customer_id": customer_id, "steps": [], "event_id": None})
        )
        if expected is not None and (
            dict(state.versions) != dict(expected.versions)
            or state.clock_version != expected.clock_version
        ):
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE, 503, message="consistent read unavailable"
            )
        for field, entity_id in entity_ids(args):
            if not owned(state, field, entity_id, customer_id):
                raise GatewayError(Code.NOT_FOUND, 404)
        # Products are public entities, but still must exist. A merchant filter
        # must name a merchant observed in this customer's state.
        for product in args.get("product_ids", []):
            if not owned(state, "product_id", product, customer_id):
                raise GatewayError(Code.NOT_FOUND, 404)
        merchant = args.get("merchant_id")
        if merchant and not any(
            t.merchant_id == merchant and t.customer_id == customer_id for t in state.transactions
        ):
            raise GatewayError(Code.NOT_FOUND, 404)
        # BankState v1 has no SplitView collection; a requested split cannot be
        # proven owned at this boundary until that shared gap is resolved.
        if tool == "get_split_status" and args.get("request_id"):
            raise GatewayError(Code.NOT_FOUND, 404)
        data = (
            READ_RESULTS[tool]
            .model_validate(await l3.read(tool, {"customer_id": customer_id, "args": args}))
            .model_dump(mode="json")
        )
        after = BankState.model_validate(
            await l3.snapshot({"customer_id": customer_id, "steps": [], "event_id": None})
        )
    except L3BusinessError as exc:
        try:
            code = Code(exc.code)
        except ValueError:
            code = Code.DEPENDENCY_UNAVAILABLE
        raise GatewayError(code, exc.status, details=exc.details) from exc
    except (L3Error, ValidationError) as exc:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc
    if dict(state.versions) != dict(after.versions) or state.clock_version != after.clock_version:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, message="consistent read unavailable")
    return ReadEnvelope(
        data=data,
        source_ref=_source_id(),
        untrusted_paths=untrusted_paths(tool, data),
        versions=dict(state.versions),
        observed_at=state.sim_now.isoformat(),
    )


async def _session(conn, sid: str, customer_id: str) -> None:
    row = (
        await conn.execute(
            select(sessions).where(
                sessions.c.id == sid,
                sessions.c.customer_id == customer_id,
                sessions.c.revoked_at_wall.is_(None),
            )
        )
    ).first()
    if row is None:
        raise GatewayError(Code.NOT_FOUND, 404)


@router.post("/tools/read/{tool}", response_model=ReadEnvelope)
async def read_tool(tool: str, body: ReadRequest, request: Request) -> ReadEnvelope:
    caller = request.state.caller
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    if caller.kind is ServiceKind.A and caller.sid != body.session_id:
        raise GatewayError(Code.NOT_FOUND, 404)
    db = getattr(request.app.state, "db", None)
    l3 = getattr(request.app.state, "l3", None)
    if db is None or l3 is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    async with db.connect() as conn:
        await _session(conn, body.session_id, caller.customer_id)
    envelope = await fetch_read(l3, caller.customer_id, tool, body.args)
    now = getattr(request.app.state, "wall_now", lambda: datetime.now(UTC))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("wall_now must carry timezone")
    async with db.begin() as conn:
        await _session(conn, body.session_id, caller.customer_id)
        await conn.execute(
            insert(read_records).values(
                id=envelope.source_ref,
                sid=body.session_id,
                tool=tool,
                canonical_args=READ_ARGS[tool].model_validate(body.args).model_dump(mode="json"),
                response=envelope.data,
                untrusted_paths=list(envelope.untrusted_paths),
                versions=envelope.versions,
                observed_at=datetime.fromisoformat(envelope.observed_at),
                created_at_wall=now,
            )
        )
    return envelope
