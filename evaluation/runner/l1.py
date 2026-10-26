"""E0b T2b: drive the real L1 graph (fake LLM ports) through the real gateway G4.

``GatewayClient`` is a *sync* httpx client while ``EvalHarness.client`` is an
in-process *async* ASGI client.  ``RecordingBridgeTransport`` bridges the two:
each sync request is forwarded onto the harness's event loop via
``run_coroutine_threadsafe`` and the async response is read back into a sync
``httpx.Response``.  The test then runs ``graph.invoke`` with
``asyncio.to_thread`` so the main loop stays free to serve the forwarded calls.

The sample is the typed utterance 「冻结我的卡」 against seed customer ``cus_zs``
(single card ``crd_zs_1``).  The fake plan proposes a one-step ``freeze_card``
with ``card_id`` sourced from the utterance span; whether G4 ALLOWs it or not,
the /plans/evaluate decision and plan row are what the test asserts on.
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


def _try_json(content: bytes):
    if not content:
        return None
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return None


class RecordingBridgeTransport(httpx.BaseTransport):
    """Sync transport forwarding to an async ASGI client on another loop.

    Records every (method, url, status, json) pair so the test can assert on
    the real G4 /plans/evaluate exchange.
    """

    def __init__(self, async_client: httpx.AsyncClient, loop: asyncio.AbstractEventLoop):
        self._client = async_client
        self._loop = loop
        self.requests: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
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


def build_real_gateway_graph(harness, *, loop, utterance_id: str):
    """Build a compiled L1 graph whose GatewayClient reaches the real gateway."""
    transport = RecordingBridgeTransport(harness.client, loop)
    http_client = httpx.Client(transport=transport, base_url="http://gateway")
    client = GatewayClient("http://gateway", A_SERVICE_TOKEN, http_client=http_client)
    ports = make_gateway_ports(
        client,
        understand=_understand,
        gather=_gather,
        plan=_make_plan(utterance_id),
        respond=_respond,
        verify_output=_verify,
    )
    graph = build_gateway_graph(ports, InMemorySaver(), client)
    return graph, transport
