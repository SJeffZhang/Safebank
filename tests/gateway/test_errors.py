import asyncio

import httpx
from fastapi import FastAPI
from safebank_contracts.codes import Code

from services.gateway.app.errors import GatewayError
from services.gateway.app.middleware import install_middleware


def _app():
    app = FastAPI(docs_url=None, redoc_url=None)
    install_middleware(app)

    @app.get("/boom")
    async def boom():
        raise GatewayError(Code.NOT_FOUND, 404, "missing thing", {"k": "v"})

    @app.get("/crash")
    async def crash():
        raise RuntimeError("boom")

    return app


def test_error_envelope_and_trace_id_echo():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="http://t"
        ) as client:
            resp = await client.get("/boom", headers={"X-Request-ID": "trace-abc"})
            assert resp.status_code == 404
            assert resp.headers["X-Request-ID"] == "trace-abc"
            assert resp.json() == {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "missing thing",
                    "details": {"k": "v"},
                    "trace_id": "trace-abc",
                }
            }

    asyncio.run(run())


def test_framework_404_uses_unified_body():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="http://t"
        ) as client:
            resp = await client.get("/nope")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "NOT_FOUND"

    asyncio.run(run())


def test_framework_405_collapses_to_not_found():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app()), base_url="http://t"
        ) as client:
            resp = await client.post("/boom")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "NOT_FOUND"

    asyncio.run(run())


def test_unhandled_exception_is_500():
    # FastAPI registers the Exception handler as the ServerErrorMiddleware
    # handler, which sends the 500 then re-raises for server logging. Drive the
    # ASGI app directly and capture the response before the re-raise.
    async def run():
        import json

        app = _app()
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/crash",
            "raw_path": b"/crash",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("t", 80),
            "scheme": "http",
            "http_version": "1.1",
        }
        try:
            await app(scope, receive, send)
        except RuntimeError:
            pass
        start = next(m for m in messages if m["type"] == "http.response.start")
        body = b"".join(m["body"] for m in messages if m["type"] == "http.response.body")
        assert start["status"] == 500
        assert json.loads(body)["error"]["code"] == "INTERNAL_ERROR"

    asyncio.run(run())
