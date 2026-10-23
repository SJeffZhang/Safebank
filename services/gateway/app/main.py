"""Dual-app, single-process gateway entrypoint (PRD §3.3, §11.1).

``public_app`` binds :8200 and never registers ``/internal/*``; ``internal_app``
binds :8201 and only registers ``/internal/*`` (S service token). One asyncio
process runs two ``uvicorn.Server`` instances on the configured bind addresses.
"""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import Depends, FastAPI
from safebank_contracts.codes import Code

from .auth.deps import service_token_dependency
from .config import Settings, from_env
from .errors import GatewayError
from .middleware import install_middleware


def build_public_app(settings: Settings, *, env: dict[str, str] | None = None) -> FastAPI:
    app = FastAPI(title="SafeBank Gateway", docs_url=None, redoc_url=None, openapi_url=None)
    install_middleware(app)

    @app.get("/health")
    async def health():
        return {"service": "gateway", "status": "ok", "profile": settings.profile}

    return app


def build_internal_app(settings: Settings, *, env: dict[str, str] | None = None) -> FastAPI:
    app = FastAPI(
        title="SafeBank Gateway internal", docs_url=None, redoc_url=None, openapi_url=None
    )
    install_middleware(app)
    require_scheduler = service_token_dependency("S", env)

    @app.get("/internal/health", dependencies=[Depends(require_scheduler)])
    async def internal_health():
        return {"service": "gateway-internal", "status": "ok"}

    @app.post("/internal/scheduled/authorize", dependencies=[Depends(require_scheduler)])
    async def scheduled_authorize():
        # S1 authorization logic is delivered in W5; W0 registers the route so
        # the auth matrix entry has a real target.
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "S1 scheduled authorize lands in W5")

    return app


async def serve(settings: Settings) -> None:
    settings.validate()
    if settings.public_bind == settings.internal_bind:
        raise GatewayError(
            Code.FORBIDDEN,
            500,
            message="GATEWAY_PUBLIC_BIND and GATEWAY_INTERNAL_BIND must be distinct interfaces",
        )
    if settings.public_bind in ("0.0.0.0", "::") or settings.internal_bind in ("0.0.0.0", "::"):
        raise GatewayError(
            Code.FORBIDDEN,
            500,
            message="gateway must bind its named network interfaces, not 0.0.0.0",
        )
    public = build_public_app(settings)
    internal = build_internal_app(settings)
    servers = [
        uvicorn.Server(
            uvicorn.Config(public, host=settings.public_bind, port=8200, log_level="warning")
        ),
        uvicorn.Server(
            uvicorn.Config(internal, host=settings.internal_bind, port=8201, log_level="warning")
        ),
    ]
    await asyncio.gather(*(server.serve() for server in servers))


def run() -> None:
    asyncio.run(serve(from_env()))


if __name__ == "__main__":
    run()
