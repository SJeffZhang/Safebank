"""Trace-id plumbing, error handlers and the request-id idempotency helper."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from safebank_contracts.codes import Code

from .errors import GatewayError

MAX_TRACE_ID = 256


def canonical_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def digest_hex(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def trace_id(request: Request) -> str:
    incoming = request.headers.get("X-Request-ID", "")
    if incoming and len(incoming) <= MAX_TRACE_ID:
        return incoming
    return str(uuid4())


def error_response(
    trace_id: str, code: Code, status: int, message: str | None = None, details: Any = None
) -> JSONResponse:
    body = {
        "error": {
            "code": code.value,
            "message": message or code.value,
            "details": details if details is not None else {},
            "trace_id": trace_id,
        }
    }
    return JSONResponse(status_code=status, content=body)


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _trace(request: Request, call_next):
        request.state.trace_id = trace_id(request)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.trace_id
        return response

    @app.exception_handler(GatewayError)
    async def _gateway_error(request: Request, exc: GatewayError):
        return error_response(
            getattr(request.state, "trace_id", ""), exc.code, exc.status, exc.message, exc.details
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # Contract failures never expose field internals or foreign data.
        return error_response(
            getattr(request.state, "trace_id", ""),
            Code.CONTRACT_INVALID,
            422,
            "CONTRACT_INVALID",
        )


_PENDING = object()


class IdempotencyStore:
    """In-memory request-id idempotency (route + body scope).

    Same ``(scope, request_id)`` with an identical body returns the stored
    result; a different body raises 409 ``IDEMPOTENCY_CONFLICT``. W3 replaces
    the in-memory store with the database-backed ``plans``/``evaluations``
    tables; this utility pins the semantics and is safe for W0 tests.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def resolve(
        self, scope: str, request_id: str, body: Any, produce: Callable[[], Awaitable[Any]]
    ) -> Any:
        key = (scope, request_id)
        digest = digest_hex(body)
        async with self._lock:
            if key in self._entries:
                stored_digest, result = self._entries[key]
                if stored_digest != digest:
                    raise GatewayError(Code.IDEMPOTENCY_CONFLICT, 409)
                if result is _PENDING:
                    raise GatewayError(Code.RUN_IN_PROGRESS, 409)
                return result
            self._entries[key] = (digest, _PENDING)
        try:
            result = await produce()
        except BaseException:
            async with self._lock:
                self._entries.pop(key, None)
            raise
        async with self._lock:
            self._entries[key] = (digest, result)
        return result
