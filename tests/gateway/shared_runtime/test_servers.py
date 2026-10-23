"""Real installed Uvicorn listeners: no fake serve/finally cleanup behavior."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from services.gateway.app.config import from_env


@pytest.mark.parametrize(
    "scenario",
    [
        "clean",
        "runtime_failure",
        "startup_failure",
        "cancel",
        "cancel_twice",
        "timeout",
        "cancel_during_shutdown",
    ],
)
def test_real_listeners_and_requests_stop_before_runtime_disposal(monkeypatch, scenario):
    import services.gateway.app.main as main

    real_server = uvicorn.Server
    base_server = getattr(main, "GatewayServer", real_server)
    instances, lifespans, disposals = [], set(), []

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        completed = []

        @asynccontextmanager
        async def resources(settings):
            try:
                yield object()
            finally:
                disposals.append(
                    {
                        "listeners_closed": all(
                            not listener.is_serving()
                            for server in instances
                            for listener in getattr(server, "servers", [])
                        ),
                        "requests_done": all(not server.server_state.tasks for server in instances),
                        "lifespans_done": not lifespans,
                        "completed": completed == ["request"],
                    }
                )

        def app(name):
            @asynccontextmanager
            async def lifespan(app):
                lifespans.add(name)
                yield
                lifespans.remove(name)

            application = FastAPI(lifespan=lifespan)

            @application.get("/work")
            async def work():
                entered.set()
                await release.wait()
                completed.append("request")
                return {"ok": True}

            return application

        class ObservedServer(base_server):
            async def startup(self, sockets=None):
                await super().startup(sockets)
                if scenario == "startup_failure" and self is instances[0]:
                    while not all(server.started for server in instances):
                        await asyncio.sleep(0.001)
                    raise RuntimeError("startup boundary failure")

            async def main_loop(self):
                if scenario == "runtime_failure" and self is instances[0]:
                    await entered.wait()
                    raise RuntimeError("runtime boundary failure")
                await super().main_loop()

        def create(config):
            config.host, config.port = "127.0.0.1", 0
            config.timeout_graceful_shutdown = 0.01 if scenario == "timeout" else 1
            server = ObservedServer(config)
            instances.append(server)
            return server

        monkeypatch.setattr(main, "gateway_runtime", resources)
        monkeypatch.setattr(main, "build_public_app", lambda *a, **kw: app("public"))
        monkeypatch.setattr(main, "build_internal_app", lambda *a, **kw: app("internal"))
        monkeypatch.setattr(main, "GatewayServer", create, raising=False)
        monkeypatch.setattr(main.uvicorn, "Server", create)
        task = asyncio.create_task(main.serve(replace(from_env({}), internal_bind="127.0.0.2")))
        request = None
        try:
            if scenario == "startup_failure":
                release.set()
                with pytest.raises(RuntimeError):
                    await asyncio.wait_for(task, 3)
            else:
                async with asyncio.timeout(3):
                    while len(instances) != 2 or not all(server.started for server in instances):
                        await asyncio.sleep(0.001)
                port = instances[1].servers[0].sockets[0].getsockname()[1]
                async with httpx.AsyncClient() as client:
                    request = asyncio.create_task(client.get(f"http://127.0.0.1:{port}/work"))
                    await asyncio.wait_for(entered.wait(), 3)
                    if scenario in {"clean", "timeout", "cancel_during_shutdown"}:
                        instances[0].should_exit = True
                    elif scenario in {"cancel", "cancel_twice", "cancel_during_shutdown"}:
                        task.cancel()
                    if scenario == "cancel_during_shutdown":
                        while not getattr(instances[1], "_gateway_shutdown", None):
                            await asyncio.sleep(0.001)
                        task.cancel()
                    if scenario == "cancel_twice":
                        await asyncio.sleep(0.01)
                        task.cancel()
                    await asyncio.sleep(0.05)
                    if scenario != "timeout":
                        release.set()
                    if scenario in {"cancel", "cancel_twice", "cancel_during_shutdown"}:
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    elif scenario == "runtime_failure":
                        with pytest.raises(RuntimeError):
                            await task
                    else:
                        await task
                    # A dropped request is part of the bug; gather safely before assertions.
                    await asyncio.gather(request, return_exceptions=True)
        finally:
            release.set()
            for server in instances:
                server.should_exit = True
                if hasattr(server, "lifespan"):
                    await real_server.shutdown(server)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert len(disposals) == 1
        expected = {
            "listeners_closed": True,
            "requests_done": True,
            "lifespans_done": True,
            "completed": scenario not in {"startup_failure", "timeout"},
        }
        assert disposals[0] == expected

    asyncio.run(run())
