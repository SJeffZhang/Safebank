"""C1d L1 Banking Agent service (PRD §6.5).

FastAPI app with three HTTP endpoints, async PG checkpoint saver, per-thread
advisory lock, and Ed25519 JWT verification.

Production path (no *ports* argument) assembles real C1b DeepSeek adapters +
C1c1 GatewayClient + C1c2 gateway graph.  Fake ports only allowed via test
injection — D9 (2026-10-24).
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import jwt as pyjwt
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from services.agent.app.graph import (
    AgentContext,
    Ports,
    UtteranceRef,
    build_gateway_graph,
    build_graph,
)

# ── Environment defaults ─────────────────────────────────────────────────

_DEFAULT_DB_URL = "postgresql://agent_app:@localhost:5432/agent"
_DEFAULT_JWT_KEY = ".secrets/jwt-public.pem"


# ── JWT identity ─────────────────────────────────────────────────────────


class AgentIdentity:
    """Verified identity extracted from assistant JWT (PRD §7.3)."""

    __slots__ = ("customer_id", "session_id", "assistant_token")

    def __init__(self, customer_id: str, session_id: str, assistant_token: str) -> None:
        self.customer_id = customer_id
        self.session_id = session_id
        self.assistant_token = assistant_token


# ── JWT verification ─────────────────────────────────────────────────────


def _verify_token(raw_token: str) -> tuple[str, str]:
    """Verify Ed25519 JWT and return (customer_id, session_id).

    Raises HTTPException(401) on any failure.
    L1 does NOT make authorization decisions — only identity extraction (D4).
    """
    key_path = os.environ.get("JWT_PUBLIC_KEY_PATH", _DEFAULT_JWT_KEY)
    try:
        pem = Path(key_path).read_bytes()
        public_key = serialization.load_pem_public_key(pem)
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"JWT key error: {exc}") from exc

    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise HTTPException(status_code=500, detail="JWT public key is not Ed25519")

    try:
        claims = pyjwt.decode(
            raw_token,
            public_key,
            algorithms=["EdDSA"],
            options={"verify_aud": False, "require": ["sub", "sid", "exp"]},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except pyjwt.InvalidSignatureError as exc:
        raise HTTPException(status_code=401, detail="Invalid signature") from exc
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    sub = claims.get("sub")
    sid = claims.get("sid")
    if not isinstance(sub, str) or not isinstance(sid, str):
        raise HTTPException(status_code=401, detail="Missing sub or sid in token")
    return sub, sid


async def jwt_identity(request: Request) -> AgentIdentity:
    """FastAPI dependency: extract identity from Authorization header.

    Raises 401 if header missing/invalid, 500 for JWT key errors.
    """
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization required")
    raw_token = authorization[7:]
    if not raw_token:
        raise HTTPException(status_code=401, detail="Empty token")
    sub, sid = _verify_token(raw_token)
    return AgentIdentity(customer_id=sub, session_id=sid, assistant_token=raw_token)


# ── Thread advisory lock ─────────────────────────────────────────────────


async def _try_thread_lock(db_url: str, thread_id: str) -> psycopg.AsyncConnection:
    """Acquire pg_try_advisory_lock for *thread_id*.

    Returns open connection (lock held until close()).  Raises 409 if
    another stream is active on this thread.
    """
    conn = await psycopg.AsyncConnection.connect(db_url)
    try:
        cur = await conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (thread_id,))
        row = await cur.fetchone()
        if not row or not row[0]:
            await conn.close()
            raise HTTPException(status_code=409, detail="RUN_IN_PROGRESS")
    except HTTPException:
        raise
    except Exception:
        await conn.close()
        raise
    return conn


# ── Production port helpers ──────────────────────────────────────────────


def _build_production_ports() -> tuple[Ports, object]:
    """Assemble real C1b+C1c1+C1c2 ports and gateway client (D9, D10).

    Returns (ports, gateway_client). The client is stored on app.state for
    use by build_gateway_graph.

    D10: agent uses AGENT_SERVICE_TOKEN (A identity) to call L2, never
    the L2→L3 token (PRD §3.2 / §11.1).  L2 address
    from GATEWAY_URL (default http://gateway:8200, PRD §11.1).
    """
    # -- Gateway client --
    gw_url = os.environ.get("GATEWAY_URL", "http://gateway:8200")
    gw_token = os.environ.get("AGENT_SERVICE_TOKEN", "")
    if not gw_token:
        raise RuntimeError("AGENT_SERVICE_TOKEN is not configured")

    from services.agent.app.gateway.client import GatewayClient

    gateway_client = GatewayClient(base_url=gw_url, service_token=gw_token)

    # -- DeepSeek LLM --
    from services.agent.app.llm.client import make_llm

    try:
        llm = make_llm()
    except RuntimeError:
        raise  # "DEEPSEEK_API_KEY is not set; no API calls made"

    # -- C1b adapters --
    from services.agent.app.llm.gather import GatherAdapter
    from services.agent.app.llm.plan import PlanAdapter
    from services.agent.app.llm.understand import UnderstandAdapter

    understand = UnderstandAdapter(llm)
    gather = GatherAdapter(llm, read_executor=_dummy_read_executor)
    plan = PlanAdapter(llm)

    # -- respond / verify_output (respond: C1e RespondAdapter, verify: C1e later) --
    from services.agent.app.llm.respond import RespondAdapter

    respond = RespondAdapter(llm)
    verify_output = _make_production_verify()

    # -- Wire gateway client into evaluate/execute slots --
    from services.agent.app.gateway.adapters import make_gateway_ports

    ports = make_gateway_ports(
        gateway_client,
        understand=understand,
        gather=gather,
        plan=plan,
        respond=respond,
        verify_output=verify_output,
    )

    return ports, gateway_client


def _dummy_read_executor(_tool: str, _args: dict):
    """Placeholder — the gateway graph replaces this with a real executor per invocation."""
    raise RuntimeError("gather read executor not wired — graph should replace it")


def _make_production_respond():
    """Production respond port — uses reply_draft from state (C1e will add LLM)."""

    def respond(state, _context) -> str:
        reply = state.get("reply_draft", "") if hasattr(state, "get") else ""
        if reply:
            return reply
        return "您的请求已收到。"

    return respond


def _make_production_verify():
    """Production verify_output port — returns reply_draft (C1e will add LLM)."""

    def verify_output(state, _context) -> str:
        if hasattr(state, "get"):
            return state.get("reply_draft", "") or "Done"
        return "Done"

    return verify_output


# ── SSE helpers ──────────────────────────────────────────────────────────


def _serialize_interrupts(tasks: tuple) -> list[dict]:
    """Extract interrupt data from LangGraph tasks."""
    result = []
    for i, task in enumerate(tasks or ()):
        interrupts = []
        if hasattr(task, "interrupts"):
            for intr in getattr(task, "interrupts", ()) or ():
                interrupts.append(
                    {
                        "value": intr.value,
                        "id": getattr(intr, "id", f"intr_{i}"),
                    }
                )
        if interrupts:
            result.append(
                {
                    "id": getattr(task, "id", f"task_{i}"),
                    "interrupts": interrupts,
                }
            )
    return result


# ── App factory ──────────────────────────────────────────────────────────


def app_factory(
    *,
    ports: Ports | None = None,
) -> FastAPI:
    """Create a FastAPI app with real PG checkpoint saver.

    Parameters:
        ports: Injected ports for testing.  If ``None`` (production), the factory
               assembles real C1b DeepSeek adapters + C1c1 GatewayClient + C1c2
               gateway graph.  Must NOT be ``None`` in deployed environments (D9).
    """
    _ports = ports  # mutable for lifespan closure

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_url = os.environ.get("AGENT_DATABASE_URL", _DEFAULT_DB_URL)
        if not db_url:
            app.state.checkpointer = None
            yield
            return
        app.state.db_url = db_url
        async with AsyncPostgresSaver.from_conn_string(db_url) as saver:
            await saver.setup()
            app.state.checkpointer = saver
            yield

    app = FastAPI(title="SafeBank L1 Agent", lifespan=lifespan)

    # ── Health ────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # ── POST /threads ─────────────────────────────────────────────────

    @app.post("/threads")
    async def create_thread(
        request: Request,
        body: dict,
        identity: AgentIdentity = Depends(jwt_identity),  # noqa: B008
    ):
        """Create or verify a thread. Idempotent — 200 whether exists or new.

        The thread_id must match the JWT session_id.
        A checkpoint is not created here; it will be created when
        POST /threads/{id}/runs/stream is first called.
        """
        thread_id = body.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise HTTPException(status_code=422, detail="thread_id is required")
        if thread_id != identity.session_id:
            raise HTTPException(
                status_code=403,
                detail="thread_id must match JWT session_id",
            )
        saver = request.app.state.checkpointer
        if saver is None:
            raise HTTPException(status_code=503, detail="No database configured")
        return {"thread_id": thread_id}

    # ── GET /threads/{thread_id}/state ────────────────────────────────

    @app.get("/threads/{thread_id}/state")
    async def get_thread_state(
        thread_id: str,
        request: Request,
        identity: AgentIdentity = Depends(jwt_identity),  # noqa: B008
    ):
        """Return checkpoint state from PG."""
        if thread_id != identity.session_id:
            raise HTTPException(status_code=403)
        saver = request.app.state.checkpointer
        if saver is None:
            raise HTTPException(status_code=503)
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        # Check whether a checkpoint actually exists — aget_state returns a
        # default empty snapshot for non-existent threads, so we must verify.
        saved = await saver.aget_tuple(config)
        if saved is None:
            raise HTTPException(status_code=404, detail="Thread not found")

        resolved_ports = _resolve_ports(request, saver)
        graph = build_graph(resolved_ports, saver)
        state = await graph.aget_state(config)
        if state is None or state.values is None:
            raise HTTPException(status_code=404, detail="Thread not found")

        values = state.values or {}
        result: dict = {"values": values}

        tasks = getattr(state, "tasks", None) or ()
        serialized_tasks = _serialize_interrupts(tasks)
        if serialized_tasks:
            result["tasks"] = serialized_tasks

        return result

    # ── POST /threads/{thread_id}/runs/stream ─────────────────────────

    @app.post("/threads/{thread_id}/runs/stream")
    async def stream_run(
        thread_id: str,
        body: dict,
        request: Request,
        identity: AgentIdentity = Depends(jwt_identity),  # noqa: B008
    ):
        """Start or resume a run, returning SSE events.

        Body: ``{"input": {"utterances": [...]}}`` for new input, or
              ``{"command": {"resume": "apv_..."}}`` to resume after interrupt.
        The two are mutually exclusive.

        Uses ``graph.ainvoke()`` instead of ``astream()`` because the latter
        does not properly save interrupt checkpoints to PostgreSQL in
        langgraph 1.2.16.  SSE events are yielded from the ainvoke result.
        """
        if thread_id != identity.session_id:
            raise HTTPException(status_code=403)

        has_input = "input" in body and body["input"] is not None
        has_command = "command" in body and body.get("command") is not None
        if has_input and has_command:
            raise HTTPException(
                status_code=400,
                detail="input and command are mutually exclusive",
            )
        if not has_input and not has_command:
            raise HTTPException(
                status_code=400,
                detail="either input or command.resume is required",
            )

        saver = request.app.state.checkpointer
        if saver is None:
            raise HTTPException(status_code=503, detail="No database configured")

        db_url = request.app.state.db_url
        context = AgentContext(
            assistant_token=identity.assistant_token,
            customer_id=identity.customer_id,
            session_id=identity.session_id,
        )
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        resolved_ports = _resolve_ports(request, saver)
        gw_client = getattr(request.app.state, "_gateway_client", None)
        if gw_client is not None:
            # Production: gateway graph with G20/G21 ingest
            graph: CompiledStateGraph = build_gateway_graph(
                resolved_ports, saver, gw_client, gateway_read_client=gw_client
            )
        else:
            # Test injection: basic graph
            graph = build_graph(resolved_ports, saver)

        async def _stream() -> AsyncIterator[str]:
            lock_conn = await _try_thread_lock(db_url, thread_id)
            try:
                if has_input:
                    raw_utts = body["input"]["utterances"]
                    utterances = [
                        UtteranceRef(
                            id=u.get("id", f"utt_{i}"),
                            kind=u.get("kind", "typed"),
                            text=u.get("text", ""),
                        )
                        for i, u in enumerate(raw_utts)
                    ]
                    if not utterances:
                        raise HTTPException(status_code=400, detail="utterances must not be empty")
                    # Use ainvoke — it properly saves interrupt checkpoints.
                    await graph.ainvoke(
                        {"utterances": utterances},
                        config=config,
                        context=context,
                    )
                else:
                    resume_val = body["command"]["resume"]
                    await graph.ainvoke(
                        Command(resume=resume_val),
                        config=config,
                        context=context,
                    )

                # After ainvoke completes (or interrupts), read the state.
                final_state = await graph.aget_state(config)

                # Check for interrupts
                tasks = getattr(final_state, "tasks", None) or ()
                serialized_tasks = _serialize_interrupts(tasks)
                if serialized_tasks:
                    interrupt_events = [
                        {"value": t["interrupts"][0]["value"], "id": t["interrupts"][0]["id"]}
                        for t in serialized_tasks
                    ]
                    yield (
                        "event: updates\ndata: "
                        + json.dumps({"__interrupt__": interrupt_events}, default=str)
                        + "\n\n"
                    )

                # Final values
                if final_state and final_state.values:
                    yield (
                        "event: values\ndata: "
                        + json.dumps(final_state.values, default=str)
                        + "\n\n"
                    )

                reply_text = (
                    final_state.values.get("reply_draft", "")
                    if final_state and final_state.values
                    else ""
                )
                yield (
                    "event: messages\ndata: "
                    + json.dumps([{"type": "ai", "content": reply_text}])
                    + "\n\n"
                )
            except HTTPException:
                raise
            except Exception as exc:
                yield (
                    "event: error\ndata: "
                    + json.dumps(
                        {
                            "message": str(exc),
                            "type": type(exc).__name__,
                        }
                    )
                    + "\n\n"
                )
            finally:
                await lock_conn.close()

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _resolve_ports(request: Request, saver) -> Ports:
    """Resolve ports from *request.app.state* if injected, else build production ports.

    Production ports are cached on ``app.state._production_ports`` after first
    assembly so they are built exactly once (not on every request).
    """
    injected = getattr(request.app.state, "_injected_ports", None)
    if injected is not None:
        return injected
    cached = getattr(request.app.state, "_production_ports", None)
    if cached is None:
        ports_obj, gw_client = _build_production_ports()
        request.app.state._production_ports = ports_obj
        request.app.state._gateway_client = gw_client
    return request.app.state._production_ports


# ── Test helper: install injected ports on an app ───────────────────────


def _install_test_ports(app: FastAPI, ports: Ports) -> None:
    """Store *ports* on app state so the next request uses them.

    This is the ONLY way fake ports can enter the request path (D9).
    """
    app.state._injected_ports = ports
