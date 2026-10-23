"""Trace-id plumbing and unified error handlers (PRD §9.1, §9.8).

Every HTTP failure — 4xx/5xx from handlers, framework 404/405, and unhandled
exceptions — returns ``{error:{code,message,details,trace_id}}``. Codes come
only from ``safebank_contracts.codes.Code``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from safebank_contracts.codes import Code
from starlette.exceptions import HTTPException as StarletteHTTPException

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
        # Field errors without echoing the rejected values (PRD §9.1).
        details = [
            {"loc": list(err.get("loc", ())), "type": err.get("type"), "msg": err.get("msg")}
            for err in exc.errors()
        ]
        return error_response(
            getattr(request.state, "trace_id", ""), Code.CONTRACT_INVALID, 422, details=details
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        # 404 and 405 collapse to NOT_FOUND so an unregistered route is not
        # distinguishable from an existing route with the wrong method.
        return error_response(
            getattr(request.state, "trace_id", ""),
            Code.NOT_FOUND,
            404,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Never leak internals; depend on the trace id for correlation.
        return error_response(
            getattr(request.state, "trace_id", ""),
            Code.DEPENDENCY_UNAVAILABLE,
            503,
        )
