"""C1d SSE wire fix: ``values`` event must be structured JSON, not Pydantic repr.

Empirical defect (2026-10-26): ``services/agent/app/main.py:460-465`` calls
``json.dumps(final_state.values, default=str)`` on an ``AgentState`` TypedDict
that contains Pydantic models (``Decision``, ``ExecutionResult``, etc.).
``default=str`` produces Pydantic repr strings like
``Decision(plan_id='pln_test', decision=<DecisionValue.ALLOW: 'ALLOW'>)``,
making the values event unparseable for downstream consumers.

Tests in this file use real ``app_factory`` HTTP with fake ports (D9),
isolated tmpfs PG (``AGENT_DB_TESTS=1``), and parse live SSE events to verify
that ``values`` events contain proper JSON dicts with real enum values.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient
from safebank_contracts.plan import (
    STEP_MODELS,
    ArgSource,
    ExecutionResult,
    PlanDraft,
    StepResult,
    UtteranceSource,
)

from services.agent.app.graph import (
    Decision,
    DecisionValue,
    Ports,
    Understanding,
)
from services.agent.app.main import _install_test_ports, app_factory

pytestmark = pytest.mark.db

# ── JWT helpers (same pattern as test_service.py) ──────────────────────────


@pytest.fixture(scope="session")
def ed25519_keys():
    """Generate a test Ed25519 key pair and write public key to temp file."""
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key()
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    tmp = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
    tmp.write(pem)
    tmp.close()
    yield private, tmp.name
    if os.path.exists(tmp.name):
        os.unlink(tmp.name)


def make_token(
    private: ed25519.Ed25519PrivateKey,
    customer: str = "cus_a",
    session: str = "ses_ssw",
    *,
    exp_seconds: int = 600,
    scope: tuple[str, ...] = ("assistant",),
) -> str:
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": customer,
            "sid": session,
            "aud": "safebank-assistant",
            "scope": list(scope),
            "iat": now,
            "exp": now + exp_seconds,
        },
        private,
        algorithm="EdDSA",
    )


# ── Fake ports (same pattern as test_service.py) ───────────────────────────


def _make_test_ports(decision_value: str | DecisionValue = "ALLOW") -> Ports:
    if isinstance(decision_value, str):
        decision_value = DecisionValue(decision_value)
    dec = Decision(
        plan_id="pln_test",
        decision=decision_value,
        approval_id="apv_test" if decision_value == DecisionValue.REQUIRE_APPROVAL else None,
    )
    source = ArgSource(
        param="amount_cents",
        source=UtteranceSource(kind="utterance", ref="utt_test", span=(0, 4)),
    )
    StepCls = STEP_MODELS["transfer"]
    step = StepCls(
        step_id="s1",
        tool="transfer",
        depends_on=(),
        arg_sources=(source,),
        args={
            "from_account_id": "acc_test",
            "payee_id": "pye_test",
            "to_account_id": None,
            "amount_cents": 100,
            "memo": None,
        },
    )
    now = datetime.now(UTC)

    return Ports(
        understand=lambda s, c: Understanding(
            intents=("transfer",), needs_write=True, unclear=False, slots=()
        ),
        gather=lambda s, c: [],
        plan=lambda s, c: PlanDraft(
            request_id="req_test",
            session_id=c.session_id,
            utterance_ids=(getattr(s.get("utterances", [None])[0], "id", "utt_test"),),
            intents=("transfer",),
            steps=(step,),
            rationale="test",
        ),
        evaluate=lambda s, c: dec,
        execute=lambda s, c: ExecutionResult(
            plan_id=dec.plan_id,
            status="EXECUTED",
            steps=(
                StepResult(
                    step_id="s1",
                    tool="transfer",
                    status="EXECUTED",
                    created_ids={},
                    result={"amount_cents": 100},
                    effects=(),
                ),
            ),
            versions={},
            committed_at=now,
            error_code=None,
            replayed=False,
        ),
        respond=lambda s, c: "Done",
        verify_output=lambda s, c: "Done",
    )


# ── App fixture ───────────────────────────────────────────────────────────


def _setup_test_app(agent_pg, ed25519_keys, decision_value="ALLOW"):
    _private, public_key_path = ed25519_keys
    os.environ["JWT_PUBLIC_KEY_PATH"] = public_key_path
    os.environ["AGENT_DATABASE_URL"] = agent_pg.runtime_url
    app = app_factory()
    _install_test_ports(app, _make_test_ports(decision_value))
    return app


# ── SSE parser helpers ────────────────────────────────────────────────────


def _parse_sse_events(body: bytes) -> list[dict]:
    """Parse raw SSE bytes into a list of {event, data} dicts."""
    events = []
    current_event = None
    current_data = ""
    for line in body.decode("utf-8").split("\n"):
        if line.startswith("event: "):
            current_event = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            current_data = line[len("data: ") :].strip()
        elif line == "" and current_event is not None:
            try:
                data = json.loads(current_data) if current_data else None
            except json.JSONDecodeError:
                data = current_data
            events.append({"event": current_event, "data": data})
            current_event = None
            current_data = ""
    return events


def _stream_graph(  # noqa: D417
    client,
    token: str,
    thread_id: str,
    utterance_text: str = "给张三转 8 元",
    uid: str = "utt_s",
):
    """POST /threads/{tid}/runs/stream, collect SSE events, return {events, status}."""
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/threads", json={"thread_id": thread_id}, headers=headers)
    resp = client.post(
        f"/threads/{thread_id}/runs/stream",
        json={"input": {"utterances": [{"id": uid, "kind": "typed", "text": utterance_text}]}},
        headers=headers,
    )
    events = _parse_sse_events(resp.content)
    return {"events": events, "status": resp.status_code}


# ── Tests ─────────────────────────────────────────────────────────────────


class TestValuesEventNotRepr:
    """Verify that the ``values`` SSE event is proper JSON, not Pydantic repr."""

    def test_values_decision_is_dict_not_repr(self, agent_pg, ed25519_keys):
        """``values.decision`` must be a JSON dict, not a Pydantic repr string."""
        app = _setup_test_app(agent_pg, ed25519_keys, "ALLOW")
        private = ed25519_keys[0]
        tid = "ses_ssw_d1"
        token = make_token(private, session=tid)

        with TestClient(app) as tc:
            result = _stream_graph(tc, token, tid, "给张三转 8 元", "utt_d1")

        assert result["status"] == 200

        # Find the values event
        values_events = [e for e in result["events"] if e["event"] == "values"]
        assert len(values_events) == 1, f"expected exactly 1 values event, got {len(values_events)}"
        data = values_events[0]["data"]
        assert isinstance(data, dict), f"values data must be dict, got {type(data).__name__}"

        # decision must be a dict with enum values (NOT a repr string)
        assert "decision" in data, f"values keys: {sorted(data.keys())}"
        decision = data["decision"]
        assert isinstance(decision, dict), (
            f"decision must be dict, got {type(decision).__name__}: {decision!r}"
        )
        assert decision["decision"] == "ALLOW", (
            f"expected ALLOW, got {decision.get('decision')!r}; full: {decision}"
        )
        assert decision["plan_id"] == "pln_test"

    def test_values_execution_is_dict_with_status(self, agent_pg, ed25519_keys):
        """``values.execution`` must be a JSON dict with ``status`` as a string."""
        app = _setup_test_app(agent_pg, ed25519_keys, "ALLOW")
        private = ed25519_keys[0]
        tid = "ses_ssw_e1"
        token = make_token(private, session=tid)

        with TestClient(app) as tc:
            result = _stream_graph(tc, token, tid, "给张三转 8 元", "utt_e1")

        assert result["status"] == 200
        values_events = [e for e in result["events"] if e["event"] == "values"]
        assert len(values_events) == 1
        data = values_events[0]["data"]

        # execution must be a dict
        assert "execution" in data, f"values keys: {sorted(data.keys())}"
        execution = data["execution"]
        assert isinstance(execution, dict), (
            f"execution must be dict, got {type(execution).__name__}: {execution!r}"
        )
        assert execution["status"] == "EXECUTED"
        assert execution["plan_id"] == "pln_test"
        assert execution["replayed"] is False

    def test_values_require_approval_decision(self, agent_pg, ed25519_keys):
        """REQUIRE_APPROVAL decision must be a dict with approval_id."""
        app = _setup_test_app(agent_pg, ed25519_keys, "REQUIRE_APPROVAL")
        private = ed25519_keys[0]
        tid = "ses_ssw_ra1"
        token = make_token(private, session=tid)

        with TestClient(app) as tc:
            result = _stream_graph(tc, token, tid, "给张三转 800 元", "utt_ra1")

        assert result["status"] == 200
        values_events = [e for e in result["events"] if e["event"] == "values"]
        assert len(values_events) == 1
        data = values_events[0]["data"]

        decision = data["decision"]
        assert isinstance(decision, dict)
        assert decision["decision"] == "REQUIRE_APPROVAL"
        assert decision["approval_id"] is not None

    def test_values_is_valid_json_parseable(self, agent_pg, ed25519_keys):
        """The entire ``values`` event data must be parseable as JSON dict.

        No Pydantic repr fragments anywhere — the ``data`` field in SSE must
        be a complete, well-formed JSON object.
        """
        app = _setup_test_app(agent_pg, ed25519_keys, "ALLOW")
        private = ed25519_keys[0]
        tid = "ses_ssw_vj1"
        token = make_token(private, session=tid)

        with TestClient(app) as tc:
            result = _stream_graph(tc, token, tid, "查询余额", "utt_vj1")

        assert result["status"] == 200
        values_events = [e for e in result["events"] if e["event"] == "values"]
        assert len(values_events) == 1
        data = values_events[0]["data"]
        assert isinstance(data, dict)

        # Walk every value — no repr strings containing angle brackets
        def _check_no_repr(obj, path=""):
            if isinstance(obj, str):
                assert not obj.startswith("Decision("), f"repr string at {path}: {obj[:80]}..."
                assert not obj.startswith("ExecutionResult("), (
                    f"repr string at {path}: {obj[:80]}..."
                )
                assert not obj.startswith("<DecisionValue."), (
                    f"repr string at {path}: {obj[:80]}..."
                )
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    _check_no_repr(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _check_no_repr(v, f"{path}[{i}]")

        _check_no_repr(data, "values")

    def test_get_state_same_as_values_event(self, agent_pg, ed25519_keys):
        """GET /state returns the same decision shape as the SSE values event."""
        app = _setup_test_app(agent_pg, ed25519_keys, "ALLOW")
        private = ed25519_keys[0]
        tid = "ses_ssw_gs1"
        token = make_token(private, session=tid)
        headers = {"Authorization": f"Bearer {token}"}

        with TestClient(app) as tc:
            result = _stream_graph(tc, token, tid, "给张三转 8 元", "utt_gs1")

        assert result["status"] == 200
        values_events = [e for e in result["events"] if e["event"] == "values"]
        assert len(values_events) == 1
        sse_data = values_events[0]["data"]

        with TestClient(app) as tc:
            state_resp = tc.get(f"/threads/{tid}/state", headers=headers)
        assert state_resp.status_code == 200
        state_data = state_resp.json()

        # Both should have decision as dict with same plan_id and decision value
        sse_decision = sse_data["decision"]
        state_decision = state_data.get("values", {}).get("decision", {})
        assert sse_decision["plan_id"] == state_decision.get("plan_id")
        assert sse_decision["decision"] == state_decision.get("decision")


class TestStreamErrorLogged:
    """Internal exceptions stay in server logs; SSE uses the public error contract."""

    def test_stream_exception_is_logged_with_traceback(self, agent_pg, ed25519_keys, caplog):
        app = _setup_test_app(agent_pg, ed25519_keys, "ALLOW")
        ports = _make_test_ports("ALLOW")

        def boom(s, c):
            raise TypeError("string indices must be integers, not 'str'")

        _install_test_ports(app, dataclasses.replace(ports, plan=boom))
        private = ed25519_keys[0]
        tid = "ses_ssw_err"
        token = make_token(private, session=tid)

        with caplog.at_level(logging.ERROR, logger="services.agent.app.main"):
            with TestClient(app) as tc:
                result = _stream_graph(tc, token, tid, "给张三转 8 元", "utt_err")

        errors = [e for e in result["events"] if e["event"] == "error"]
        assert errors and errors[0]["data"]["code"] == "AGENT_REQUEST_FAILED"
        assert errors[0]["data"]["type"] == "AgentError"
        assert "TypeError" not in str(errors) and "string indices" not in str(errors)
        logged = [r for r in caplog.records if r.message == "run stream failed"]
        assert logged, "stream failure must be logged server-side"
        assert logged[0].exc_info is not None
        assert logged[0].exc_info[0] is TypeError
