"""L3 HTTP surface, isolated on bank-net and authenticated on every route."""

import os
import secrets
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.plan import (
    Ack,
    Execute,
    ExecutionResult,
    InternalRead,
    Prepare,
    Reconcile,
    SnapshotRequest,
)
from safebank_contracts.protocol import AckResult, ExecutionStatus, OutboxPage, PrepareResult
from safebank_contracts.state import BankState
from safebank_contracts.tools import READ_ARGS
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError

from . import models as m
from .db import engine, ready
from .demo import register_demo_routes
from .errors import BankError
from .execution import ExecutionService, utcnow
from .reads import SUPPORTED_READS, read
from .state import load_bank_state, resolve_scope


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def create_app(
    db=None,
    *,
    service_token=None,
    fingerprint_key=None,
    demo_mode=None,
    admin_db=None,
):
    token = service_token or os.environ.get("GATEWAY_SERVICE_TOKEN")
    key = fingerprint_key or os.environ.get("FINGERPRINT_KEY")
    if not token or not key:
        raise ValueError("L3 service credentials are required")
    owned = db is None
    database = db if db is not None else engine(os.environ["BANK_DATABASE_URL"])
    if demo_mode is None:
        demo_mode = _env_flag("DEMO_MODE") or _env_flag("EVAL_MODE")
    admin_owned = False
    admin_database = None
    if demo_mode:
        admin_database = admin_db
        if admin_database is None:
            admin_owned = True
            admin_database = engine(
                os.environ.get("BANK_MIGRATION_DATABASE_URL") or os.environ["BANK_DATABASE_URL"]
            )

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned:
            await database.dispose()
        if admin_owned and admin_database is not None:
            await admin_database.dispose()

    async def authenticate(x_service_token: str | None = Header(default=None)):
        if not x_service_token or not secrets.compare_digest(token, x_service_token):
            raise BankError(Code.UNAUTHENTICATED, 401)

    app = FastAPI(
        title="SafeBank L3",
        version="1.0.0",
        lifespan=lifespan,
        dependencies=[Depends(authenticate)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    svc = ExecutionService(database, key)

    @app.middleware("http")
    async def trace(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request.state.trace_id = incoming if incoming and len(incoming) <= 256 else str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.trace_id
        return response

    def error(request, code, status):
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": code,
                    "details": {},
                    "trace_id": getattr(request.state, "trace_id", ""),
                }
            },
        )

    @app.exception_handler(BankError)
    async def bank_error(request, exc):
        return error(request, exc.code.value, exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return error(request, "CONTRACT_INVALID", 422)

    @app.exception_handler(SQLAlchemyError)
    async def dependency_failed(request, exc):
        # Do not leak query parameters or credentials in responses.
        return error(request, "DEPENDENCY_UNAVAILABLE", 503)

    @app.get("/health")
    async def health():
        return {"service": "bank-backend", "status": "ok", "stage": "P1-core"}

    @app.get("/readyz")
    async def readiness():
        try:
            valid = await ready(database)
        except (SQLAlchemyError, OSError):
            valid = False
        if not valid:
            raise BankError(Code.DEPENDENCY_UNAVAILABLE, 503)
        return {"ready": True}

    async def snapshot(customer_id, steps):
        async with database.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await svc.customer(conn, customer_id)
                clock = (await conn.execute(select(m.clock))).mappings().one()
                scope = await resolve_scope(conn, customer_id, steps, key)
                try:
                    return await load_bank_state(
                        conn, scope, clock["sim_now"], customer_id=customer_id
                    )
                except LookupError:
                    raise BankError(Code.NOT_FOUND, 404) from None

    @app.post("/internal/snapshot", response_model=BankState)
    async def get_snapshot(body: SnapshotRequest):
        if body.event_id is not None:
            raise BankError(Code.TOOL_NOT_ALLOWED, 403)
        return await snapshot(body.customer_id, body.steps)

    @app.post("/internal/read/{tool}")
    async def get_read(tool: str, body: InternalRead):
        if tool not in SUPPORTED_READS:
            raise BankError(Code.TOOL_NOT_ALLOWED, 403)
        try:
            args = READ_ARGS[tool].model_validate(body.args)
        except ValidationError:
            raise BankError(Code.CONTRACT_INVALID, 422) from None
        state = await snapshot(body.customer_id, ())
        return read(state, tool, args, body.customer_id)

    @app.post("/internal/plans/prepare", response_model=PrepareResult)
    async def prepare(body: Prepare):
        return await svc.prepare(body)

    @app.post("/internal/plans/execute", response_model=ExecutionResult)
    async def execute(body: Execute):
        return await svc.execute(body)

    @app.get("/internal/executions/{plan_id}", response_model=ExecutionStatus)
    async def query(plan_id: str, customer_id: str):
        return await svc.query(customer_id, plan_id)

    @app.post("/internal/executions/{plan_id}/reconcile", response_model=ExecutionStatus)
    async def reconcile(plan_id: str, body: Reconcile):
        return await svc.reconcile(plan_id, body)

    @app.get("/internal/outbox", response_model=OutboxPage)
    async def outbox(
        after: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)
    ):
        async with database.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(m.outbox, m.operation_events.c.commit_seq)
                        .join(
                            m.operation_events, m.operation_events.c.event_id == m.outbox.c.event_id
                        )
                        .where(
                            m.operation_events.c.commit_seq > after,
                            m.outbox.c.delivered_at_wall.is_(None),
                        )
                        .order_by(m.operation_events.c.commit_seq)
                        .limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
        items = [dict(r) for r in rows[:limit]]
        return {
            "items": items,
            "next_cursor": str(items[-1]["commit_seq"]) if len(rows) > limit else None,
        }

    @app.post("/internal/outbox/ack", response_model=AckResult)
    async def ack(body: Ack):
        async with database.begin() as conn:
            existing = set(
                (
                    await conn.execute(
                        select(m.outbox.c.event_id).where(m.outbox.c.event_id.in_(body.event_ids))
                    )
                ).scalars()
            )
            if existing != set(body.event_ids):
                raise BankError(Code.NOT_FOUND, 404)
            await conn.execute(
                update(m.outbox)
                .where(
                    m.outbox.c.event_id.in_(body.event_ids), m.outbox.c.delivered_at_wall.is_(None)
                )
                .values(delivered_at_wall=utcnow())
            )
        return {"acknowledged": len(existing)}

    if demo_mode:
        register_demo_routes(app, database, admin_database, key)

    return app


def factory():
    return create_app()
