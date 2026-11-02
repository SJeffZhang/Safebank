"""HTTP/SSE nullable references reach the real G20-backed compiled graph.

Only PostgreSQL lock/checkpoint storage and external HTTP providers are replaced.
The route, JWT verification, graph, G20/G21/G4 client, and LLM adapters are real.
Scripted gateway decisions do not prove live L2 authorization or bank writes.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from services.agent.app import main
from services.agent.app.gateway.adapters import make_gateway_ports
from services.agent.app.gateway.client import GatewayClient
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.gather import GatherAdapter
from services.agent.app.llm.plan import PlanAdapter
from services.agent.app.llm.understand import UnderstandAdapter
from tests.agent.gateway.read_cases import READ_CASES
from tests.agent.gateway.transport import ScriptedGateway, error_response, json_response
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response

SID = "ses_nullable_entry"
UTT = "utt_nullable_entry"
PARENT = "utt_nullable_parent"
CANARY = "PRIVATE_ENTRY_ERROR_CANARY"


def _item(kind: str, *, product: bool = False) -> dict:
    scope = {
        "action_kinds": ["FUND_SUBSCRIBE" if product else "TRANSFER"],
        "from_account_id": "acc_ls_chk" if product else "acc_zs_chk",
        "transfers": []
        if product
        else [
            {
                "target_fingerprint": "fp_ww",
                "amount_cents": 20000,
                "from_account_id": "acc_zs_chk",
            }
        ],
        "period": None,
        "channel": None,
        "fields": {
            "account_id": "acc_ls_chk",
            "product_id": "prd_sbond",
            "derived_template": "available_less_7d_charges",
            "robust_products": ["prd_mixed", "prd_sbond", "prd_stable"],
        }
        if product
        else {},
    }
    return {
        "id": UTT,
        "kind": kind,
        "text": "给王五转200元" if kind == "typed" else None,
        "scope": scope,
        "parse_status": "SCOPED",
        "echoes_untrusted": False,
        "parent_ids": [] if kind == "typed" else [PARENT],
        "clarification_id": None,
        "created_at_wall": "2026-11-01T10:00:00Z",
        "expires_at_wall": "2026-11-01T10:10:00Z",
    }


def _strict_plan(product: bool) -> dict:
    args = (
        {"account_id": "acc_ls_chk", "product_id": "prd_sbond", "amount_cents": 1095500}
        if product
        else {
            "from_account_id": "acc_zs_chk",
            "payee_id": {"kind": "id", "id": "pye_zs_ww"},
            "to_account_id": {"kind": "none"},
            "amount_cents": 20000,
            "memo": "__NULL__",
        }
    )
    return {
        "intents": ["subscribe_product" if product else "transfer"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "subscribe_product" if product else "transfer",
                "args": args,
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "account_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_nullable_get_accounts",
                            "pointer": "/accounts/0/id",
                        },
                    },
                    {
                        "param": "product_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_nullable_get_product",
                            "pointer": "/id",
                        },
                    },
                    {
                        "param": "amount_cents",
                        "source": {
                            "kind": "derived",
                            "template": "available_less_7d_charges",
                            "inputs": [
                                {
                                    "kind": "read",
                                    "ref": "src_nullable_get_accounts",
                                    "pointer": "/accounts/0/id",
                                },
                                {
                                    "kind": "read",
                                    "ref": "src_nullable_upcoming_charges",
                                    "pointer": "/by_account/0/reserve_cents",
                                },
                            ],
                        },
                    },
                ]
                if product
                else [],
            }
        ],
        "rationale": "fixed provider fixture; L2 still determines authorization",
    }


def _approval() -> dict:
    return {
        "plan_id": "pln_nullable",
        "evaluation_id": "evl_nullable",
        "revision": 1,
        "decision": "REQUIRE_APPROVAL",
        "risk": "RED",
        "approval_id": "apv_nullable",
        "findings": [],
        "replan_hints": [],
        "preflight": {
            "executable": True,
            "steps": [],
            "balances": [],
            "holdings": [],
            "scheduled_reserve_cents": 0,
        },
        "versions": {},
        "clock_version": 0,
        "policy_version": "test",
        "shadow_b1": None,
    }


def _events(response) -> list[tuple[str, dict | list]]:
    parsed = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) == 2:
            parsed.append((lines[0].removeprefix("event: "), json.loads(lines[1][6:])))
    return parsed


@dataclass
class Entry:
    client: TestClient
    headers: dict
    gateway: ScriptedGateway
    provider: ScriptedLLM
    item: dict

    def run(self, kind, text, *, sid=SID):
        return self.client.post(
            f"/threads/{sid}/runs/stream",
            json={"input": {"utterances": [{"id": UTT, "kind": kind, "text": text}]}},
            headers=self.headers,
        )


@pytest.fixture
def entry_factory(monkeypatch, tmp_path, caplog):
    # Validation diagnostics stay server-side; never include them in red-run output.
    caplog.set_level(logging.CRITICAL, logger="services.agent.app.main")
    private = ed25519.Ed25519PrivateKey.generate()
    public_path = tmp_path / "jwt-public.pem"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(public_path))

    class Lock:
        async def close(self):
            pass

    async def memory_lock(*args):
        return Lock()

    monkeypatch.setattr(main, "_try_thread_lock", memory_lock)
    opened = []

    def create(
        kind="action", *, product=False, ingest_error=None, evaluate_error=None, expired=False
    ):
        item = _item(kind, product=product)
        tools = (
            ["get_profile", "get_accounts", "upcoming_charges", "get_risk_profile", "get_product"]
            if product
            else ["get_accounts"]
        )
        reads = []
        for tool in tools:
            data = copy.deepcopy(READ_CASES[tool][1])
            if product and tool == "get_accounts":
                data["accounts"] = [
                    {
                        "id": "acc_ls_chk",
                        "type": "CHECKING",
                        "display_name": "活期",
                        "account_no_masked": "**** 0001",
                        "currency": "CNY",
                        "balance_cents": 1100000,
                        "available_cents": 1100000,
                        "pending_debit_cents": 0,
                        "pending_credit_cents": 0,
                        "current_card_id": None,
                        "single_limit_cents": None,
                        "daily_limit_cents": None,
                    }
                ]
            if tool == "get_profile":
                data.update(customer_id="cus_nullable", sim_now="2026-11-01T10:00:00Z")
            if tool == "upcoming_charges":
                data["by_account"] = [
                    {
                        "account_id": "acc_ls_chk",
                        "reserve_cents": 4500,
                        "max_outflow_cents": 1095500,
                    }
                ]
                data["charges_total_cents"] = data["reserve_total_cents"] = 4500
            if tool == "get_risk_profile":
                data.update(
                    assessment_id="ras_nullable",
                    level=3,
                    is_valid=True,
                    assessed_at="2026-10-15T01:00:00Z",
                    valid_until="2027-10-15",
                )
            if tool == "get_product":
                data.update(id="prd_sbond", risk_level=2, risk_label="稳健")
            reads.append(
                json_response(
                    200,
                    {
                        "data": data,
                        "source_ref": "src_nullable_" + tool,
                        "untrusted_paths": [],
                        "versions": {},
                        "observed_at": "2026-11-01T10:00:00Z",
                    },
                )
            )
        gateway = ScriptedGateway(
            [ingest_error]
            if ingest_error is not None
            else [
                json_response(200, {"items": [item]}),
                json_response(200, {"clarification": None}),
                *reads,
                evaluate_error if evaluate_error is not None else json_response(200, _approval()),
            ]
        )
        gw_client = GatewayClient(
            "http://gateway", "test-service-token", http_client=gateway.client
        )
        provider = ScriptedLLM(
            [
                tool_response(
                    "report_understanding",
                    {
                        "intents": ["subscribe_product" if product else "transfer"],
                        "slots": [],
                        "needs_write": True,
                    },
                ),
                *(
                    []
                    if product
                    else [
                        tool_response("get_accounts", {"history_days": 30}),
                        text_response("enough"),
                    ]
                ),
                tool_response("submit_action_plan", _strict_plan(product)),
            ]
        )
        llm = make_llm(api_key="test-provider-key", http_client=provider.client)

        def unused_executor(*args):
            raise AssertionError("compiled gateway graph must bind the gateway read executor")

        ports = make_gateway_ports(
            gw_client,
            understand=UnderstandAdapter(llm),
            gather=GatherAdapter(llm, unused_executor, fetch_business_date=product),
            plan=PlanAdapter(llm),
            respond=lambda s, c: "请按网关提示重新发起。",
            verify_output=lambda s, c: s["reply_draft"],
        )
        app = main.app_factory()
        app.state.checkpointer = InMemorySaver()
        app.state.db_url = "unused-in-memory-checkpoint"
        app.state._gateway_client = gw_client
        main._install_test_ports(app, ports)
        # No lifespan: the only store is InMemorySaver; never open PostgreSQL.
        client = TestClient(app)
        opened.append(client)
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": "cus_nullable",
                "sid": SID,
                "iat": now - 20,
                "exp": now - 10 if expired else now + 600,
                "scope": ["assistant"],
            },
            private,
            algorithm="EdDSA",
        )
        return Entry(client, {"Authorization": "Bearer " + token}, gateway, provider, item)

    yield create
    for client in opened:
        client.close()


@pytest.mark.parametrize(
    ("kind", "product"), [("action", False), ("action", True), ("hint", False)]
)
def test_null_action_and_hint_reach_authoritative_ingest_and_approval(entry_factory, kind, product):
    entry = entry_factory(kind, product=product)
    response = entry.run(kind, None)
    assert response.status_code == 200
    events = _events(response)
    assert not [payload for event, payload in events if event == "error"]
    values = next(payload for event, payload in events if event == "values")
    assert values["utterances"] == [{"id": UTT, "kind": kind, "text": ""}]
    assert values["parent_ids"] == [PARENT]
    assert values["parse_status"] == "SCOPED"
    assert values["hints"] == [{"id": UTT, "kind": kind, "scope": entry.item["scope"]}]
    assert values["decision"]["decision"] == "REQUIRE_APPROVAL"
    assert values["execution"] is None
    interrupt = next(payload for event, payload in events if event == "updates")
    assert interrupt["__interrupt__"][0]["value"] == {"approval_id": "apv_nullable"}
    expected_methods = (
        ["GET", "GET", "POST", "POST", "POST", "POST", "POST", "POST"]
        if product
        else ["GET", "GET", "POST", "POST"]
    )
    assert [r["method"] for r in entry.gateway.requests] == expected_methods
    evaluated = entry.gateway.requests[-1]
    assert evaluated["url"].endswith("/plans/evaluate")
    assert evaluated["body"]["utterance_ids"] == [UTT]
    assert evaluated["body"]["session_id"] == SID
    assert len(entry.provider.requests) == (2 if product else 4)
    assert entry.headers["Authorization"][7:] not in response.text


def test_typed_text_is_replaced_only_by_authoritative_g20_text(entry_factory):
    entry = entry_factory("typed")
    response = entry.run("typed", "CALLER_TEXT_IS_NOT_AUTHORITY")
    events = _events(response)
    assert not [payload for event, payload in events if event == "error"]
    values = next(payload for event, payload in events if event == "values")
    assert values["utterances"] == [{"id": UTT, "kind": "typed", "text": "给王五转200元"}]
    assert values["hints"] == []
    assert values["decision"]["decision"] == "REQUIRE_APPROVAL"
    assert values["execution"] is None
    assert "CALLER_TEXT_IS_NOT_AUTHORITY" not in response.text


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("typed", None),
        ("typed", False),
        ("typed", 0),
        ("typed", []),
        ("typed", {"private": CANARY}),
        ("action", 0),
        ("hint", False),
        ("unknown-kind", None),
    ],
)
def test_invalid_text_and_kind_are_still_rejected_before_ingest(entry_factory, kind, text):
    entry = entry_factory()
    response = entry.run(kind, text)
    assert response.status_code == 200
    events = _events(response)
    assert [event for event, payload in events] == ["error"]
    assert events[0][1]["code"] == "MODEL_OUTPUT_INVALID"
    assert entry.gateway.requests == []
    assert entry.provider.requests == []
    assert all(
        private not in response.text
        for private in [CANARY, "ValidationError", "input_value", "errors.pydantic.dev"]
    )


@pytest.mark.parametrize(
    ("status", "code"), [(422, "UTTERANCE_EXPIRED"), (403, "INTENT_SCOPE_EXCEEDED")]
)
def test_nullable_entry_keeps_gateway_ingest_rejections_private(entry_factory, status, code):
    entry = entry_factory(
        ingest_error=error_response(status, code, message=CANARY, details={"private": CANARY})
    )
    response = entry.run("action", None)
    events = _events(response)
    assert [event for event, payload in events] == ["error"]
    assert events[0][1]["code"] == "AGENT_REQUEST_FAILED"
    assert len(entry.gateway.requests) == 1
    assert entry.provider.requests == []
    assert CANARY not in response.text
    assert entry.headers["Authorization"][7:] not in response.text


def test_nullable_entry_does_not_override_scope_denial_at_evaluate(entry_factory):
    entry = entry_factory(
        evaluate_error=error_response(422, "INTENT_SCOPE_EXCEEDED", message=CANARY)
    )
    response = entry.run("action", None)
    events = _events(response)
    assert not [payload for event, payload in events if event == "error"]
    values = next(payload for event, payload in events if event == "values")
    assert values["route_error"]["code"] == "INTENT_SCOPE_EXCEEDED"
    assert values["decision"] is None
    assert values["execution"] is None
    assert not [payload for event, payload in events if event == "updates"]
    assert len(entry.gateway.requests) == 4
    assert CANARY not in response.text


@pytest.mark.parametrize(
    ("expired", "sid", "expected"), [(True, SID, 401), (False, "ses_other", 403)]
)
def test_nullable_input_cannot_bypass_expired_identity_or_session_binding(
    entry_factory, expired, sid, expected
):
    entry = entry_factory(expired=expired)
    response = entry.run("action", None, sid=sid)
    assert response.status_code == expected
    assert entry.gateway.requests == []
    assert entry.provider.requests == []
