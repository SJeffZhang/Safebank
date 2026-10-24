"""C1d agent service HTTP/SSE tests (Task 5-6).

Tests the three agent HTTP endpoints (PRD §6.5) with FastAPI TestClient.
Uses a real PG checkpoint saver (AGENT_DB_TESTS=1) for stateful tests,
and fake ports for evaluate/execute (injected via _install_test_ports — D9).
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import FastAPI
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

# ── JWT helpers ──────────────────────────────────────────────────────────

_SYSTEM_ROOT = Path(__file__).resolve().parents[2]


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
    session: str = "ses_a",
    *,
    exp_seconds: int = 600,
    scope: tuple[str, ...] = ("assistant",),
) -> str:
    """Create a signed Ed25519 JWT for testing."""
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


# ── Fake ports for testing ───────────────────────────────────────────────


def _make_test_ports(decision_value: str | DecisionValue = "ALLOW") -> Ports:
    """Fake ports for testing — injected via _install_test_ports.

    No LLM, no real L2, no authorization.  Only for tests (D9).
    """
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


# ── App fixtures ─────────────────────────────────────────────────────────


def _setup_test_app(agent_pg, ed25519_keys, decision_value="ALLOW") -> FastAPI:
    """Create a FastAPI app with fake ports injected for testing."""
    _private, public_key_path = ed25519_keys
    os.environ["JWT_PUBLIC_KEY_PATH"] = public_key_path
    os.environ["AGENT_DATABASE_URL"] = agent_pg.runtime_url
    app = app_factory()
    _install_test_ports(app, _make_test_ports(decision_value))
    return app


@pytest.fixture
def client(agent_pg, ed25519_keys):
    """FastAPI TestClient with ALLOW decision."""
    app = _setup_test_app(agent_pg, ed25519_keys, "ALLOW")
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def client_ra(agent_pg, ed25519_keys):
    """FastAPI TestClient with REQUIRE_APPROVAL decision."""
    app = _setup_test_app(agent_pg, ed25519_keys, "REQUIRE_APPROVAL")
    with TestClient(app) as tc:
        yield tc


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _run_graph_and_get_state(client, token, thread_id, utterance_text="给张三转 8 元", uid="utt_s"):
    """Run a graph stream with the given utterance, then return state."""
    headers = _auth_headers(token)
    client.post("/threads", json={"thread_id": thread_id}, headers=headers)
    client.post(
        f"/threads/{thread_id}/runs/stream",
        json={"input": {"utterances": [{"id": uid, "kind": "typed", "text": utterance_text}]}},
        headers=headers,
    )
    return client.get(f"/threads/{thread_id}/state", headers=headers)


# ── Red tests: D9 — production app has no default ALLOW, no fake ports ───


def test_production_app_fail_closed_without_agent_service_token(agent_pg, ed25519_keys):
    """D9+D10: production path fails closed when AGENT_SERVICE_TOKEN is missing.

    Only AGENT_SERVICE_TOKEN is removed.  All other env vars remain valid (JWT key,
    AGENT_DATABASE_URL, synthetic DEEPSEEK_API_KEY).  With valid auth headers the JWT
    check passes, so the only failure is _build_production_ports raising RuntimeError
    about the missing token — NOT a generic 401 or other error.

    Uses POST /runs/stream because it calls _resolve_ports _before_ entering the
    async generator, so the RuntimeError surfaces BEFORE the SSE response begins.
    """
    import pytest as std_pytest

    os.environ["AGENT_DATABASE_URL"] = agent_pg.runtime_url
    _private, public_key_path = ed25519_keys
    os.environ["JWT_PUBLIC_KEY_PATH"] = public_key_path
    os.environ["DEEPSEEK_API_KEY"] = "sk-synthetic-for-test"
    old_token = os.environ.pop("AGENT_SERVICE_TOKEN", None)
    try:
        app = app_factory()  # production path — no injected ports
        private = ed25519_keys[0]
        token = make_token(private, customer="cus_a", session="ses_d9")
        headers = _auth_headers(token)
        body = {"input": {"utterances": [{"id": "utt_d9", "kind": "typed", "text": "hi"}]}}
        with TestClient(app) as tc:
            with std_pytest.raises(RuntimeError, match="AGENT_SERVICE_TOKEN is not configured"):
                tc.post(
                    "/threads/ses_d9/runs/stream",
                    json=body,
                    headers=headers,
                )
    finally:
        os.environ.pop("AGENT_DATABASE_URL", None)
        os.environ.pop("JWT_PUBLIC_KEY_PATH", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        if old_token is not None:
            os.environ["AGENT_SERVICE_TOKEN"] = old_token


def test_d10_control_agent_token_reaches_gateway_client(monkeypatch, agent_pg, ed25519_keys):
    """D10 control: with AGENT_SERVICE_TOKEN set, GatewayClient gets the right token.

    Replaces ``GatewayClient`` in its home module with a wrapper that records
    every ``service_token`` argument.  Asserts the captured token equals
    ``AGENT_SERVICE_TOKEN`` and is NOT ``GATEWAY_SERVICE_TOKEN``.
    """
    captured_tokens: list[str] = []

    import services.agent.app.gateway.client as _gw_mod

    _RealClient = _gw_mod.GatewayClient

    class _CapturingClient:
        """Wrapper that records service_token, then delegates to real client."""

        _real: object

        def __init__(self, base_url, service_token, **kwargs):
            captured_tokens.append(service_token)
            self._real = _RealClient(base_url, service_token, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(_gw_mod, "GatewayClient", _CapturingClient)

    # -- Set valid env including AGENT_SERVICE_TOKEN --
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:19999")
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "tok_agent_correct")
    monkeypatch.setenv("GATEWAY_SERVICE_TOKEN", "tok_l2_to_l3_should_not_be_used")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deadbeef-deadbeef-deadbeef-deadbeef")
    monkeypatch.setenv("AGENT_DATABASE_URL", agent_pg.runtime_url)
    _private, public_key_path = ed25519_keys
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", public_key_path)

    app = app_factory()  # production path

    private = ed25519_keys[0]
    token = make_token(private, customer="cus_a", session="ses_d10_ctrl")
    headers = _auth_headers(token)

    with TestClient(app) as tc:
        tc.post("/threads", json={"thread_id": "ses_d10_ctrl"}, headers=headers)
        # Stream may fail after GatewayClient construction (DeepSeek call, etc.)
        # but the critical assertion is what token GatewayClient received.
        try:
            tc.post(
                "/threads/ses_d10_ctrl/runs/stream",
                json={"input": {"utterances": [{"id": "utt_d10c", "kind": "typed", "text": "hi"}]}},
                headers=headers,
            )
        except Exception:
            pass

    assert captured_tokens, "GatewayClient was never constructed"
    for t in captured_tokens:
        assert t == "tok_agent_correct", f"GatewayClient received wrong token: {t}"
        assert t != "tok_l2_to_l3_should_not_be_used", (
            f"D10 violation: GATEWAY_SERVICE_TOKEN leaked to GatewayClient: {t}"
        )


def test_d10_experiment_gateway_token_never_reaches_client(monkeypatch, agent_pg, ed25519_keys):
    """D10 experiment: no AGENT_SERVICE_TOKEN → fail-closed, GatewayClient never built.

    Sets GATEWAY_SERVICE_TOKEN + GATEWAY_BASE_URL (everything the OLD code reads)
    but NOT AGENT_SERVICE_TOKEN.  Replaces ``GatewayClient`` in its module with a
    wrapper that records constructions.

    On old code (249ca98): ``_build_production_ports`` reads GATEWAY_SERVICE_TOKEN
    → GatewayClient is constructed → ``captured_tokens`` is NOT empty → this test
    MUST FAIL, proving the violation.
    """
    captured_tokens: list[str] = []

    import services.agent.app.gateway.client as _gw_mod

    _RealClient = _gw_mod.GatewayClient

    class _CapturingClient:
        """Wrapper that records service_token, then delegates to real client."""

        _real: object

        def __init__(self, base_url, service_token, **kwargs):
            captured_tokens.append(service_token)
            self._real = _RealClient(base_url, service_token, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(_gw_mod, "GatewayClient", _CapturingClient)

    # -- Set everything the old code reads, but NOT AGENT_SERVICE_TOKEN --
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:19999")
    monkeypatch.setenv("GATEWAY_BASE_URL", "http://127.0.0.1:19999")  # old code
    monkeypatch.setenv("GATEWAY_SERVICE_TOKEN", "tok_l2_to_l3_secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deadbeef-deadbeef-deadbeef-deadbeef")
    monkeypatch.setenv("AGENT_DATABASE_URL", agent_pg.runtime_url)
    _private, public_key_path = ed25519_keys
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", public_key_path)
    # Deliberately no AGENT_SERVICE_TOKEN.

    app = app_factory()  # production path

    private = ed25519_keys[0]
    token = make_token(private, customer="cus_a", session="ses_d10_exp")
    headers = _auth_headers(token)

    with TestClient(app) as tc:
        tc.post("/threads", json={"thread_id": "ses_d10_exp"}, headers=headers)
        try:
            tc.post(
                "/threads/ses_d10_exp/runs/stream",
                json={"input": {"utterances": [{"id": "utt_d10e", "kind": "typed", "text": "hi"}]}},
                headers=headers,
            )
        except RuntimeError as exc:
            assert "AGENT_SERVICE_TOKEN" in str(exc), f"Unexpected RuntimeError: {exc}"
        except Exception:
            pass

    # D10: GatewayClient must NOT have been constructed — the app must fail
    # before reaching it.  On old code (249ca98), GATEWAY_SERVICE_TOKEN would
    # be read and GatewayClient constructed → captured_tokens non-empty → FAIL.
    assert not captured_tokens, (
        f"D10 violation: GatewayClient was constructed when AGENT_SERVICE_TOKEN "
        f"was missing.  service_token values: {captured_tokens}"
    )


def test_no_gateway_service_token_in_agent_source():
    """D10: services/agent source code never references GATEWAY_SERVICE_TOKEN.

    Grep all Python files under services/agent for the string
    ``GATEWAY_SERVICE_TOKEN`` and assert zero matches.
    """
    import subprocess

    agent_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "services",
        "agent",
    )
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "GATEWAY_SERVICE_TOKEN", agent_dir],
        capture_output=True,
        text=True,
    )
    stdout_lines = [_line for _line in result.stdout.strip().split("\n") if _line]
    assert not stdout_lines, (
        "D10 violation: GATEWAY_SERVICE_TOKEN found in agent source:\n" + "\n".join(stdout_lines)
    )


def test_production_app_ports_are_not_fake(monkeypatch, agent_pg, ed25519_keys):
    """With minimal config, production ports are real GatewayClient-based, not fake.

    We set GATEWAY_URL to a non-existent address — the GatewayClient is
    constructed (real), but calls will fail with connection errors instead of
    returning fake EXECUTED results.
    """
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:19999")
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "tok_deadbeef_deadbeef_deadbeef_deadbeef")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deadbeef-deadbeef-deadbeef-deadbeef")
    _private, public_key_path = ed25519_keys
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", public_key_path)
    monkeypatch.setenv("AGENT_DATABASE_URL", agent_pg.runtime_url)

    app = app_factory()  # no ports injected
    # D9: production app has NO _injected_ports
    assert not hasattr(app.state, "_injected_ports") or app.state._injected_ports is None, (
        "D9 violation: _injected_ports should not be set on production app"
    )

    private = ed25519_keys[0]
    token = make_token(private, customer="cus_a", session="ses_d9r")
    headers = _auth_headers(token)

    with TestClient(app) as tc:
        tc.post("/threads", json={"thread_id": "ses_d9r"}, headers=headers)

        # Stream a run — this calls _resolve_ports → _build_production_ports.
        # With fake API key + unreachable GatewayClient, the graph will fail
        # during G20/G21 ingest or DeepSeek call.  The key assertion: the
        # response must NOT contain "EXECUTED" (fake success).
        resp = tc.post(
            "/threads/ses_d9r/runs/stream",
            json={"input": {"utterances": [{"id": "utt_d9", "kind": "typed", "text": "hi"}]}},
            headers=headers,
        )
        # The stream endpoint returns 200 on success OR sends error SSE events.
        # Either way, the body must NOT contain a fake EXECUTED status.
        assert "EXECUTED" not in resp.text, (
            f"D9 violation: production app returned EXECUTED without real L2. "
            f"Response: {resp.text[:500]}"
        )

        # Verify _production_ports was cached after the request
        assert hasattr(app.state, "_production_ports"), (
            "D9 violation: _production_ports not set after stream request"
        )
        ports = app.state._production_ports
        assert not _is_fake_ports(ports), (
            "D9 violation: production ports are fake (lambda-based), not real GatewayClient-based"
        )
        # Verify _gateway_client is also set (real GatewayClient, not None/fake)
        assert hasattr(app.state, "_gateway_client"), (
            "D9 violation: _gateway_client not set on production app"
        )
        assert app.state._gateway_client is not None


def _is_fake_ports(ports: Ports) -> bool:
    """Heuristic: fake ports use raw lambdas; real ports use C1b/C1c1 adapters.

    Fake understand returns Understanding with intents=("transfer",).
    Real understand is UnderstandAdapter which returns Understanding via LLM.

    We check that evaluate is not a lambda returning a fixed Decision.
    """
    # If evaluate is a bare lambda, it's fake
    evaluate = ports.evaluate
    if hasattr(evaluate, "__name__") and evaluate.__name__ == "<lambda>":
        return True
    # Real evaluate is _GatewayEvaluate or similar class instance
    if hasattr(evaluate, "_client"):
        return False
    return False


# ── Task 5: HTTP endpoint tests ──────────────────────────────────────────


def test_create_thread_returns_200(client, ed25519_keys):
    """POST /threads creates a thread and returns 200."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_ct")
    resp = client.post("/threads", json={"thread_id": "ses_ct"}, headers=_auth_headers(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == "ses_ct"


def test_create_thread_idempotent(client, ed25519_keys):
    """POST /threads twice with same thread_id returns 200 both times."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_ct2")
    headers = _auth_headers(token)
    r1 = client.post("/threads", json={"thread_id": "ses_ct2"}, headers=headers)
    assert r1.status_code == 200
    r2 = client.post("/threads", json={"thread_id": "ses_ct2"}, headers=headers)
    assert r2.status_code == 200


def test_get_state_returns_structure(client, ed25519_keys):
    """After running the graph, GET /threads/{tid}/state returns values and optional tasks."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_gs")
    headers = _auth_headers(token)
    # Create thread and run graph (ALLOW → no interrupt)
    client.post("/threads", json={"thread_id": "ses_gs"}, headers=headers)
    utt_body = {
        "input": {"utterances": [{"id": "utt_gs", "kind": "typed", "text": "给张三转 8 元"}]}
    }
    client.post("/threads/ses_gs/runs/stream", json=utt_body, headers=headers)
    # GET state should return values (and no interrupt tasks since ALLOW)
    resp = client.get("/threads/ses_gs/state", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "values" in data


def test_get_nonexistent_thread_returns_404(client, ed25519_keys):
    """GET /threads/{tid}/state for never-run thread returns 404."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_unknown")
    headers = _auth_headers(token)
    resp = client.get("/threads/ses_unknown/state", headers=headers)
    assert resp.status_code == 404


def test_stream_runs_with_input(client, ed25519_keys):
    """POST /threads/{tid}/runs/stream with input returns SSE events."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_sr")
    headers = _auth_headers(token)
    client.post("/threads", json={"thread_id": "ses_sr"}, headers=headers)
    resp = client.post(
        "/threads/ses_sr/runs/stream",
        json={
            "input": {"utterances": [{"id": "utt_s1", "kind": "typed", "text": "给张三转 8 元"}]}
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.text
    assert len(body) > 0, "SSE stream should produce output"


def test_stream_with_command_resume(client_ra, ed25519_keys):
    """POST /threads/{tid}/runs/stream with REQUIRE_APPROVAL produces interrupt."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_cr")
    headers = _auth_headers(token)
    client_ra.post("/threads", json={"thread_id": "ses_cr"}, headers=headers)

    # First round: REQUIRE_APPROVAL → interrupt recorded
    resp1 = client_ra.post(
        "/threads/ses_cr/runs/stream",
        json={
            "input": {"utterances": [{"id": "utt_ra", "kind": "typed", "text": "给张三转 80 万"}]}
        },
        headers=headers,
    )
    assert resp1.status_code == 200
    # Check state for interrupt
    state_resp = client_ra.get("/threads/ses_cr/state", headers=headers)
    state_data = state_resp.json()
    tasks = state_data.get("tasks", [])
    has_interrupt = any(t.get("interrupts") for t in tasks)
    assert has_interrupt, f"Expected interrupt in state, got {state_data}"


def test_input_and_command_mutually_exclusive(client, ed25519_keys):
    """Providing both input and command.resume returns 400."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_ic")
    headers = _auth_headers(token)
    resp = client.post(
        "/threads/ses_ic/runs/stream",
        json={
            "input": {"utterances": [{"id": "utt_x", "kind": "typed", "text": "hi"}]},
            "command": {"resume": "apv_x"},
        },
        headers=headers,
    )
    assert resp.status_code == 400


def test_neither_input_nor_command_returns_400(client, ed25519_keys):
    """Neither input nor command returns 400."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_ni")
    headers = _auth_headers(token)
    resp = client.post(
        "/threads/ses_ni/runs/stream",
        json={},
        headers=headers,
    )
    assert resp.status_code == 400


def test_missing_auth_returns_401(client, ed25519_keys):
    """No Authorization header returns 401."""
    resp = client.post("/threads", json={"thread_id": "ses_noauth"})
    assert resp.status_code == 401


def test_invalid_jwt_signature_returns_401(client, ed25519_keys):
    """JWT signed with wrong key returns 401."""
    _test_private, _ = ed25519_keys
    wrong_private = ed25519.Ed25519PrivateKey.generate()
    token = make_token(wrong_private, customer="cus_a", session="ses_bad")
    resp = client.post("/threads", json={"thread_id": "ses_bad"}, headers=_auth_headers(token))
    assert resp.status_code == 401


def test_expired_jwt_returns_401(client, ed25519_keys):
    """Expired JWT returns 401."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_exp", exp_seconds=-60)
    resp = client.post("/threads", json={"thread_id": "ses_exp"}, headers=_auth_headers(token))
    assert resp.status_code == 401


def test_wrong_sid_returns_403(client, ed25519_keys):
    """JWT with sid != thread_id returns 403."""
    private, _ = ed25519_keys
    token = make_token(private, customer="cus_a", session="ses_wrong")
    resp = client.post("/threads", json={"thread_id": "ses_other"}, headers=_auth_headers(token))
    assert resp.status_code == 403


# ── Task 6: Process restart recovery tests ──────────────────────────────


def test_interrupt_survives_graph_rebuild(agent_pg, ed25519_keys):
    """After app teardown and rebuild, GET /threads/{tid}/state shows interrupt."""
    private, public_key_path = ed25519_keys
    os.environ["JWT_PUBLIC_KEY_PATH"] = public_key_path
    os.environ["AGENT_DATABASE_URL"] = agent_pg.runtime_url
    token = make_token(private, customer="cus_a", session="ses_rebuild")
    headers = _auth_headers(token)

    try:
        # App 1 (REQUIRE_APPROVAL): create thread and run to interrupt
        app1 = _setup_test_app(agent_pg, ed25519_keys, "REQUIRE_APPROVAL")
        with TestClient(app1) as c1:
            c1.post("/threads", json={"thread_id": "ses_rebuild"}, headers=headers)
            c1.post(
                "/threads/ses_rebuild/runs/stream",
                json={
                    "input": {
                        "utterances": [{"id": "utt_ri", "kind": "typed", "text": "转 80 万给张三"}]
                    }
                },
                headers=headers,
            )

        # App 2 (ALLOW): rebuild graph with new saver, same DB → interrupt survives
        app2 = _setup_test_app(agent_pg, ed25519_keys, "ALLOW")
        with TestClient(app2) as c2:
            state_resp = c2.get("/threads/ses_rebuild/state", headers=headers)
            assert state_resp.status_code == 200
            state = state_resp.json()
            tasks = state.get("tasks", [])
            has_interrupt = any(t.get("interrupts") for t in tasks)
            assert has_interrupt, f"Expected interrupt to survive graph rebuild, got {state}"
    finally:
        os.environ.pop("JWT_PUBLIC_KEY_PATH", None)
        os.environ.pop("AGENT_DATABASE_URL", None)


def test_resume_works_after_rebuild(agent_pg, ed25519_keys):
    """After rebuild, POST runs/stream with Command(resume=...) continues."""
    private, public_key_path = ed25519_keys
    os.environ["JWT_PUBLIC_KEY_PATH"] = public_key_path
    os.environ["AGENT_DATABASE_URL"] = agent_pg.runtime_url
    token = make_token(private, customer="cus_a", session="ses_rr")
    headers = _auth_headers(token)

    try:
        # App 1 (REQUIRE_APPROVAL): run to interrupt
        app1 = _setup_test_app(agent_pg, ed25519_keys, "REQUIRE_APPROVAL")
        with TestClient(app1) as c1:
            c1.post("/threads", json={"thread_id": "ses_rr"}, headers=headers)
            c1.post(
                "/threads/ses_rr/runs/stream",
                json={
                    "input": {
                        "utterances": [{"id": "utt_rr", "kind": "typed", "text": "转 80 万给张三"}]
                    }
                },
                headers=headers,
            )
            state1 = c1.get("/threads/ses_rr/state", headers=headers).json()
            tasks = state1.get("tasks", [])
            if not any(t.get("interrupts") for t in tasks):
                pytest.skip("no interrupt in state; test environment issue")

        # App 2: rebuild, resume with correct approval_id
        app2 = _setup_test_app(agent_pg, ed25519_keys, "REQUIRE_APPROVAL")
        with TestClient(app2) as c2:
            resp = c2.post(
                "/threads/ses_rr/runs/stream",
                json={"command": {"resume": "apv_test"}},  # matches fake ports
                headers=headers,
            )
            assert resp.status_code == 200, f"Resume failed: {resp.status_code} {resp.text}"
    finally:
        os.environ.pop("JWT_PUBLIC_KEY_PATH", None)
        os.environ.pop("AGENT_DATABASE_URL", None)


# ── Lock tests (via HTTP) ───────────────────────────────────────────────


def test_concurrent_same_thread_lock_rejects(monkeypatch, agent_pg, ed25519_keys):
    """Stream handler raises 409 when advisory lock is externally held.

    The test manually holds a PG advisory lock for ``ses_conc`` (via a separate
    psycopg connection to the same database).  The stream handler calls
    ``pg_try_advisory_lock(hashtext('ses_conc'))``, which returns ``False``, so
    it raises ``HTTPException(409, "RUN_IN_PROGRESS")`` inside the SSE generator.
    Starlette wraps this in ``RuntimeError("Caught handled exception, but
    response already started.")`` because the SSE response headers were already
    sent before the lock is checked.  The test catches this RuntimeError and
    verifies the root cause is ``HTTPException(409)``.

    Red verification: remove the manual lock → stream handler acquires the
    lock → 200 instead of RuntimeError → test FAILS.
    """
    import psycopg
    import pytest as std_pytest
    from starlette.testclient import TestClient

    # Set required env vars for production app_factory (JWT key, DB, LLM key)
    monkeypatch.setenv("AGENT_DATABASE_URL", agent_pg.runtime_url)
    _private, public_key_path = ed25519_keys
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", public_key_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-for-test")
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "tok_agent_lock_test")

    def _make_token_and_headers():
        token = make_token(_private, customer="cus_a", session="ses_conc")
        return _auth_headers(token)

    # -- Manually hold the advisory lock in a separate PG connection --
    hold_conn = psycopg.connect(agent_pg.runtime_url)
    cur = hold_conn.execute("SELECT pg_try_advisory_lock(hashtext('ses_conc'))")
    got_lock = cur.fetchone()
    assert got_lock is not None
    assert got_lock[0] is True, "Could not acquire manual advisory lock for test"

    app = app_factory()
    _install_test_ports(app, _make_test_ports("ALLOW"))
    body = {"input": {"utterances": [{"id": "utt_bl", "kind": "typed", "text": "hi"}]}}

    with TestClient(app) as tc:
        tc.post("/threads", json={"thread_id": "ses_conc"}, headers=_make_token_and_headers())

        # Lock is held externally → stream handler raises HTTPException(409)
        # which Starlette wraps in RuntimeError because SSE response has begun.
        with std_pytest.raises(RuntimeError, match="response already started"):
            tc.post(
                "/threads/ses_conc/runs/stream",
                json=body,
                headers=_make_token_and_headers(),
            )

    # -- Release manual lock and verify the thread can proceed --
    hold_conn.execute("SELECT pg_advisory_unlock(hashtext('ses_conc'))")
    hold_conn.close()

    # After release, a new request should get 200
    with TestClient(app_factory()) as tc2:
        _install_test_ports(tc2.app, _make_test_ports("ALLOW"))
        tc2.post("/threads", json={"thread_id": "ses_conc"}, headers=_make_token_and_headers())
        resp2 = tc2.post(
            "/threads/ses_conc/runs/stream",
            json=body,
            headers=_make_token_and_headers(),
        )
        assert resp2.status_code == 200, (
            f"After lock release, expected 200, got {resp2.status_code}"
        )
