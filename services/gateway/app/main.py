"""Dual-app, single-process gateway entrypoint (PRD §3.3, §11.1).

``public_app`` binds :8200 and never registers ``/internal/*``; ``internal_app``
binds :8201 and only registers ``/internal/*`` (S service token). One asyncio
process runs two ``uvicorn.Server`` instances. Route modules are included here
once; W1–W5 edit only their own ``app/api/*`` / ``app/internal/*`` module.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
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
from .runtime import GatewayRuntime, gateway_runtime


def load_public_key(path: str) -> bytes:
    """Load the EdDSA public key; refuse to start if it is missing or unreadable."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise GatewayError(
            Code.FORBIDDEN, 500, message=f"cannot read JWT public key at {path}: {exc}"
        ) from exc
    if not data.strip():
        raise GatewayError(Code.FORBIDDEN, 500, message=f"JWT public key at {path} is empty")
    return data


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
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
    public_key: bytes | None = None,
    runtime: GatewayRuntime | None = None,
) -> FastAPI:
    if public_key is None and runtime is not None:
        public_key = runtime.jwt_public_key
    # The global dependency enforces the auth matrix on every route (open routes
    # return None; anything else must present a valid F/A/D credential + JWT).
    app = FastAPI(
        title="SafeBank Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(caller_dependency(env, public_key))],
    )
    if runtime is not None:
        runtime.install(app)
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
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
    public_key: bytes | None = None,
    runtime: GatewayRuntime | None = None,
) -> FastAPI:
    if public_key is None and runtime is not None:
        public_key = runtime.jwt_public_key
    app = FastAPI(
        title="SafeBank Gateway internal", docs_url=None, redoc_url=None, openapi_url=None
    )
    if runtime is not None:
        runtime.install(app)
    install_middleware(app)

    @app.get("/internal/health", dependencies=[Depends(caller_dependency(env, public_key))])
    async def internal_health():
        return {"service": "gateway-internal", "status": "ok"}

    app.include_router(scheduled.router, dependencies=[Depends(caller_dependency(env, public_key))])

    errors = coverage_errors(_routes(app), demo_mounted=False, internal=True)
    if errors:
        raise GatewayError(Code.FORBIDDEN, 500, message="; ".join(errors))
    return app


class GatewayServer(uvicorn.Server):
    """Uvicorn's installed _serve has no finally; own shutdown on every exit."""

    async def _serve(self, sockets=None) -> None:
        try:
            await super()._serve(sockets)
        except SystemExit as exc:
            # Bind/lifespan startup errors must flow through the supervisor;
            # SystemExit in a child asyncio task would terminate its event loop.
            raise RuntimeError("gateway listener startup failed") from exc
        finally:
            if hasattr(self, "lifespan"):
                self.servers = getattr(self, "servers", [])
                await self.shutdown(sockets)

    async def shutdown(self, sockets=None) -> None:
        task = getattr(self, "_gateway_shutdown", None)
        if task is None:
            task = asyncio.create_task(self._shutdown_owned(sockets))
            self._gateway_shutdown = task
        await asyncio.shield(task)

    async def _shutdown_owned(self, sockets) -> None:
        for listener in self.servers:
            listener.close()
        for sock in sockets or ():
            sock.close()
        for connection in list(self.server_state.connections):
            connection.shutdown()
        try:
            await asyncio.wait_for(
                self._wait_tasks_to_complete(), timeout=self.config.timeout_graceful_shutdown
            )
        except TimeoutError:
            for task in list(self.server_state.tasks):
                task.cancel()
        # Uvicorn cancels timed-out requests without awaiting them. Finish those
        # cancellations before ASGI lifespan shutdown or DB/L3 disposal.
        await asyncio.gather(*list(self.server_state.tasks), return_exceptions=True)
        for listener in self.servers:
            await listener.wait_closed()
        event = getattr(self.lifespan, "shutdown_event", None)
        if event is None or not event.is_set():
            await self.lifespan.shutdown()


async def _drain_servers(tasks: list[asyncio.Task]) -> None:
    """Join all shutdowns despite repeated cancellation of their supervisor."""
    joined = asyncio.gather(*tasks, return_exceptions=True)
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            # The original cancellation still propagates after serve's finally.
            # Additional cancellation must not release resources prematurely.
            continue
    joined.result()


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
    async with gateway_runtime(settings) as runtime:
        public = build_public_app(settings, runtime=runtime)
        internal = build_internal_app(settings, runtime=runtime)
        servers = [
            GatewayServer(
                uvicorn.Config(
                    public,
                    host=settings.public_bind,
                    port=8200,
                    log_level="warning",
                    timeout_graceful_shutdown=15,
                )
            ),
            GatewayServer(
                uvicorn.Config(
                    internal,
                    host=settings.internal_bind,
                    port=8201,
                    log_level="warning",
                    timeout_graceful_shutdown=15,
                )
            ),
        ]
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*done)
            await asyncio.gather(*pending)
        finally:
            # Do not cancel Uvicorn's serve tasks: let healthy listeners follow
            # their real shutdown path and failed listeners finish owned cleanup.
            for server in servers:
                server.should_exit = True
            await _drain_servers(tasks)


def run() -> None:
    asyncio.run(serve(from_env()))


if __name__ == "__main__":
    run()
