"""InputGuard unit tests (no database): rejections and intact body replay."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request

from services.gateway.app.middleware import MAX_BODY_BYTES, install_middleware


def _app() -> FastAPI:
    app = FastAPI()
    install_middleware(app)

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.body()
        return {"size": len(body), "head": body[:16].decode(), "tail": body[-16:].decode()}

    @app.get("/q")
    async def q():
        return {"ok": True}

    return app


def _call(method, url, content=None):
    async def run():
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            return await client.request(method, url, content=content)

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("method", "url", "content", "reason"),
    [
        ("POST", "/echo", b"x" * (MAX_BODY_BYTES + 1), "body_too_large"),
        ("GET", "/q?a=%00", None, "nul_byte"),
        ("GET", "/q?a=%2500ok", None, None),
        ("POST", "/echo", b'{"a": "\\ud800"}', "invalid_utf8"),
        ("POST", "/echo", b'{"a": "\\ud83d\\ude00"}', None),  # valid surrogate pair
        ("POST", "/echo", b'{"a": "\\\\u0000"}', None),  # escaped backslash, not NUL
        ("POST", "/echo", b'{"a": "\\u0000"}', "nul_byte"),
        ("POST", "/echo", b'{"a": "\xff"}', "invalid_utf8"),
        ("GET", "/q/" + "a" * 300, None, "path_too_long"),
    ],
)
def test_guard_decisions(method, url, content, reason):
    response = _call(method, url, content)
    if reason is None:
        assert response.status_code != 422 or "reason" not in response.text, response.text
    else:
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "CONTRACT_INVALID"
        assert error["details"] == {"reason": reason}
        assert error["trace_id"] == response.headers["X-Request-ID"]


def test_multi_chunk_body_is_replayed_intact():
    parts = [b"{" + b'"k": "', b"a" * 70000, b"b" * 70000, b'"}']

    async def stream():
        for part in parts:
            yield part

    async def run():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            return await client.post("/echo", content=stream())

    response = asyncio.run(run())
    assert response.status_code == 200, response.text
    whole = b"".join(parts)
    assert response.json() == {
        "size": len(whole),
        "head": whole[:16].decode(),
        "tail": whole[-16:].decode(),
    }
