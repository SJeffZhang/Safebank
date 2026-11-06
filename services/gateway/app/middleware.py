"""Trace-id plumbing and unified error handlers (PRD §9.1, §9.8).

Every HTTP failure — 4xx/5xx from handlers, framework 404/405, and unhandled
exceptions — returns ``{error:{code,message,details,trace_id}}``. Codes come
only from ``safebank_contracts.codes.Code``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from safebank_contracts.codes import Code
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import GatewayError

MAX_TRACE_ID = 256
# Requests are small JSON documents; anything larger is rejected before parsing.
MAX_BODY_BYTES = 1024 * 1024
# Real paths are far shorter (ids are ~30 chars). Idempotency keys store the
# concrete path in request_dedup.route VARCHAR(256), so cap the path below it.
MAX_PATH_CHARS = 200

logger = logging.getLogger("safebank.gateway")


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


def _has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


def _input_problem(path: str, query: bytes, body: bytes) -> str | None:
    """Return why the raw request can never be valid, or None.

    PostgreSQL text cannot hold NUL and every gateway input is UTF-8 JSON, so
    such requests are rejected here instead of failing deep inside a handler.
    """
    if "\x00" in path or b"\x00" in query or b"%00" in query:
        return "nul_byte"
    if len(path) > MAX_PATH_CHARS:
        return "path_too_long"
    if len(body) > MAX_BODY_BYTES:
        return "body_too_large"
    if not body:
        return None
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return "invalid_utf8"
    if "\x00" in decoded:
        return "nul_byte"
    if "\\u0000" in decoded.lower():
        try:
            if _has_nul(json.loads(decoded)):
                return "nul_byte"
        except RecursionError:
            return "too_deep"
        except ValueError:
            return None  # malformed JSON is reported by the route's own parsing
    return None


class InputGuard:
    """Pure ASGI guard that buffers the body once and replays it downstream."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size <= MAX_BODY_BYTES:
                chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks) if size <= MAX_BODY_BYTES else b"x" * (MAX_BODY_BYTES + 1)
        problem = _input_problem(scope.get("path", ""), scope.get("query_string", b""), body)
        if problem is not None:
            request = Request(scope)
            trace = getattr(request.state, "trace_id", None) or trace_id(request)
            response = error_response(
                trace, Code.CONTRACT_INVALID, 422, details={"reason": problem}
            )
            response.headers["X-Request-ID"] = trace
            await response(scope, receive, send)
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def install_middleware(app: FastAPI) -> None:
    # Added before the trace middleware, so it runs innermost of the two and
    # still sees every request before routing and authentication.
    app.add_middleware(InputGuard)

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
        # Never leak internals to the client; the server log keeps the full
        # traceback under the same trace id for correlation.
        logger.error(
            "unhandled exception trace_id=%s %s %s",
            getattr(request.state, "trace_id", ""),
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return error_response(
            getattr(request.state, "trace_id", ""),
            Code.DEPENDENCY_UNAVAILABLE,
            503,
        )
