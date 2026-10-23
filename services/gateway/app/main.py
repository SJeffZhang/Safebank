"""Dual-app, single-process gateway entrypoint (PRD §3.3, §11.1).

``public_app`` binds :8200 and never registers ``/internal/*``; ``internal_app``
binds :8201 and only registers ``/internal/*`` (S service token). One asyncio
process runs two ``uvicorn.Server`` instances. Route modules are included here
once; W1–W5 edit only their own ``app/api/*`` / ``app/internal/*`` module.
"""

from __future__ import annotations

import asyncio
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI
from safebank_contracts.codes import Code

from .api import approvals, auth, demo, execution, notify_audit, plans, sessions, tools
from .auth.deps import caller_dependency
from .auth.matrix import coverage_errors
from .config import Settings, from_env
from .errors import GatewayError
from .internal import scheduled
from .middleware import install_middleware


def _collect(routes: list) -> list:
    out: list = []
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            out.extend(_collect(original.routes))
        elif getattr(route, "routes", None) is not None:
            out.extend(_collect(route.routes))
        elif getattr(route, "methods", None):
            out.append(route)
    return out


def _routes(app: FastAPI) -> list[tuple[Any, str]]:
    return [(route.methods, route.path) for route in _collect(app.routes)]


def build_public_app(
    settings: Settings, *, env: dict[str, str] | None = None, public_key: bytes | None = None
) -> FastAPI:
    # The global dependency enforces the auth matrix on every route (open routes
    # return None; anything else must present a valid F/A/D credential + JWT).
    app = FastAPI(
        title="SafeBank Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(caller_dependency(env, public_key))],
    )
    install_middleware(app)

    @app.get("/health")
    async def health():
        return {"service": "gateway", "status": "ok", "profile": settings.profile}

    for router in (
        auth.router,
        sessions.router,
        tools.router,
        plans.router,
        execution.router,
        approvals.router,
        notify_audit.router,
    ):
        app.include_router(router)
    demo_mounted = settings.demo_mode or settings.eval_mode
    if demo_mounted:
        app.include_router(demo.router)

    errors = coverage_errors(_routes(app), demo_mounted=demo_mounted)
    if errors:
        raise GatewayError(Code.FORBIDDEN, 500, message="; ".join(errors))
    return app


def build_internal_app(
    settings: Settings, *, env: dict[str, str] | None = None, public_key: bytes | None = None
) -> FastAPI:
    app = FastAPI(
        title="SafeBank Gateway internal", docs_url=None, redoc_url=None, openapi_url=None
    )
    install_middleware(app)

    @app.get("/internal/health", dependencies=[Depends(caller_dependency(env, public_key))])
    async def internal_health():
        return {"service": "gateway-internal", "status": "ok"}

    app.include_router(scheduled.router, dependencies=[Depends(caller_dependency(env, public_key))])

    errors = coverage_errors(_routes(app), demo_mounted=False, internal=True)
    if errors:
        raise GatewayError(Code.FORBIDDEN, 500, message="; ".join(errors))
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
