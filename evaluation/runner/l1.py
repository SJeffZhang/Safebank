"""E0b T5: drive the real L1 graph (fake LLM ports) through the real gateway G4.

``GatewayClient`` is a *sync* httpx client while ``EvalHarness.client`` is an
in-process *async* ASGI client.  ``RecordingBridgeTransport`` bridges the two:
each sync request is forwarded onto the harness's event loop via
``run_coroutine_threadsafe`` and the async response is read back into a sync
``httpx.Response``.  The test then runs ``graph.invoke`` with
``asyncio.to_thread`` so the main loop stays free to serve the forwarded calls.

Evaluate-only mode (``evaluate_only=True`` on the transport and
``build_evaluate_only_graph``) replaces the execute port with one that raises
``EvaluationComplete``.  The transport also rejects G8 requests as a
belt-and-suspenders guard.  ``run_corpus`` catches ``EvaluationComplete`` and
classifies the outcome from the recorded G4 response — the graph never reaches
L3.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from safebank_contracts.plan import PlanDraft

from services.agent.app.gateway.adapters import make_gateway_ports
from services.agent.app.gateway.client import GatewayClient
from services.agent.app.graph import Understanding, build_gateway_graph

A_SERVICE_TOKEN = "a-test"


class EvaluationComplete(Exception):
    """Raised by the evaluate-only execute port to signal completion at G4.

    ``run_corpus`` catches this and classifies the outcome from the transport's
    recorded ``/plans/evaluate`` response — the graph never reaches L3.
    """

    def __init__(self) -> None:
        super().__init__("evaluate-only: execution blocked at G4 boundary")


def _try_json(content: bytes):
    if not content:
        return None
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return None


class RecordingBridgeTransport(httpx.BaseTransport):
    """Sync transport forwarding to an async ASGI client on another loop.

    Records every (method, url, status, json) pair.  When *evaluate_only* is
    true, any POST to a ``/plans/*/execute`` URL raises ``EvaluationComplete``
    — a belt-and-suspenders guard that prevents G8 calls even if the graph
    somehow routes past the evaluate-only execute port.
    """

    def __init__(
        self,
        async_client: httpx.AsyncClient,
        loop: asyncio.AbstractEventLoop,
        *,
        evaluate_only: bool = False,
    ):
        self._client = async_client
        self._loop = loop
        self.requests: list[dict] = []
        self._evaluate_only = evaluate_only

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read()

        # Belt-and-suspenders: transport layer rejects G8 in evaluate-only mode
        if self._evaluate_only:
            if (
                request.method == "POST"
                and "/plans/" in str(request.url)
                and "/execute" in str(request.url)
            ):
                raise EvaluationComplete()

        # GatewayClient.evaluate sends the PlanDraft directly, while the real
        # gateway G4 handler expects ``{"plan_draft": ..., "request_id": ...}``.
        # Adapt that wire shape here (transport only, no services/** change).
        if request.method == "POST" and "/plans/evaluate" in str(request.url):
            draft = _try_json(body) or {}
            body = json.dumps(
                {"plan_draft": draft, "request_id": draft.get("request_id", "")}
            ).encode()
        async_req = httpx.Request(
            request.method,
            str(request.url),
            headers=request.headers,
            content=body,
        )

        async def _send() -> tuple[int, httpx.Headers, bytes]:
            resp = await self._client.send(async_req)
            content = await resp.aread()
            return resp.status_code, resp.headers, content

        future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
        status, headers, content = future.result()
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "status": status,
                "request": _try_json(body),
                "json": _try_json(content),
            }
        )
        return httpx.Response(status, headers=headers, content=content, request=request)


def _understand(state, context):
    return Understanding(intents=["freeze_card"], needs_write=True)


def _gather(state, context):
    return []


def _make_plan(utterance_id: str):
    def plan(state, context):
        return PlanDraft(
            request_id="req_e0b_t2b",
            session_id=state["session_id"],
            utterance_ids=(utterance_id,),
            intents=("freeze_card",),
            steps=[
                {
                    "step_id": "s1",
                    "tool": "freeze_card",
                    "args": {"card_id": "crd_zs_1"},
                    "depends_on": [],
                    "arg_sources": [
                        {
                            "param": "card_id",
                            "source": {
                                "kind": "utterance",
                                "ref": utterance_id,
                                "span": (0, 5),
                            },
                        }
                    ],
                }
            ],
            rationale="e0b t2b",
        )

    return plan


def _respond(state, context):
    return "已冻结"


def _verify(state, context):
    return state["reply_draft"]


def build_graph_with_ports(harness, *, loop, ports_kwargs):
    """Build a compiled L1 graph with custom ports, recording through a bridge transport.

    ``ports_kwargs`` must contain the five LLM port callables
    (understand / gather / plan / respond / verify_output) that are passed
    through to ``make_gateway_ports``.  evaluate and execute are assembled
    automatically from the real gateway client.
    """
    transport = RecordingBridgeTransport(harness.client, loop)
    http_client = httpx.Client(transport=transport, base_url="http://gateway")
    client = GatewayClient("http://gateway", A_SERVICE_TOKEN, http_client=http_client)
    ports = make_gateway_ports(client, **ports_kwargs)
    graph = build_gateway_graph(ports, InMemorySaver(), client)
    return graph, transport


def build_real_gateway_graph(harness, *, loop, utterance_id: str):
    """Build a compiled L1 graph whose GatewayClient reaches the real gateway."""
    return build_graph_with_ports(
        harness,
        loop=loop,
        ports_kwargs=dict(
            understand=_understand,
            gather=_gather,
            plan=_make_plan(utterance_id),
            respond=_respond,
            verify_output=_verify,
        ),
    )


# ── evaluate-only mode (E0b T5) ─────────────────────────────────────────


class _EvaluateOnlyExecute:
    """Execute port that raises ``EvaluationComplete`` to stop the graph at G4.

    Never contacts L2 or L3.  The graph's execute node propagates the exception
    up through ``graph.invoke``, where ``run_corpus`` catches it and classifies
    the outcome from the transport's recorded G4 response.
    """

    def __init__(self) -> None:
        self.called = False
        self.call_count = 0

    def __call__(self, state, context):
        self.called = True
        self.call_count += 1
        raise EvaluationComplete()


def build_evaluate_only_graph(harness, *, loop, client, ports_kwargs):
    """Build a compiled L1 graph whose execute port raises ``EvaluationComplete``.

    ``client`` is a ``GatewayClient`` sharing a ``RecordingBridgeTransport``
    with the caller (so transport logs capture both gather reads and the G4
    evaluate exchange).  The graph's evaluate port is the real gateway client;
    only the execute port is replaced.

    Returns ``(graph, eval_only_execute)`` where ``eval_only_execute`` is the
    ``_EvaluateOnlyExecute`` instance (for call-count assertions).
    """
    from services.agent.app.graph.ports import Ports

    gateway_ports = make_gateway_ports(client, **ports_kwargs)
    eval_only_exec = _EvaluateOnlyExecute()

    ports = Ports(
        understand=gateway_ports.understand,
        gather=gateway_ports.gather,
        plan=gateway_ports.plan,
        evaluate=gateway_ports.evaluate,
        execute=eval_only_exec,
        respond=gateway_ports.respond,
        verify_output=gateway_ports.verify_output,
    )
    graph = build_gateway_graph(ports, InMemorySaver(), client)
    return graph, eval_only_exec
