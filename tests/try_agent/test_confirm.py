"""Protocol-level tests for scripts/try_agent.py --confirm flow (T2).

Tests verify the explicit human confirmation chain:
  G9 panel → user input → (reject?) → G6 confirm → L1 resume → G5 status

All tests use httpx.MockTransport.  No real backend stacks, no model calls.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

import scripts.try_agent as mod

# ── Reusable wire shapes ────────────────────────────────────────────────────

DECISION_REQUIRE_APPROVAL = {
    "plan_id": "pln_01",
    "decision": "REQUIRE_APPROVAL",
    "approval_id": "apv_01",
    "findings": [],
    "replan_hints": [],
}

DECISION_DENY = {
    "plan_id": "pln_01",
    "decision": "DENY",
    "approval_id": None,
    "findings": [],
    "replan_hints": [],
}


def _sse_bytes(*events: tuple[str, str]) -> bytes:
    parts = []
    for evt_type, data in events:
        parts.append(f"event: {evt_type}\ndata: {data}\n\n")
    return "".join(parts).encode("utf-8")


# ── Shared fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _set_frontend_token(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_SERVICE_TOKEN", "test-frontend-token")


@pytest.fixture
def app_token() -> str:
    return "fake-app.jwt.token"


@pytest.fixture
def assistant_token() -> str:
    return "fake-assistant.jwt.token"


@pytest.fixture
def session_id() -> str:
    return "ses_test01"


@pytest.fixture
def utterance_id() -> str:
    return "utt_test01"


@pytest.fixture
def plan_id() -> str:
    return "pln_test01"


@pytest.fixture
def approval_id() -> str:
    return "apv_test01"


# ── Full-flow handler (supports T2 endpoints) ──────────────────────────────


class _FullHandler:
    """Reusable MockTransport handler for G1→G15→G3→L1 thread→L1 SSE→G9→…

    Subclasses or fixture callers can override specific responses via
    constructor parameters.  Tracks called URLs for verification.
    """

    gateway_url: str = "http://gateway:8200"
    agent_url: str = "http://agent:8100"

    def __init__(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
        sse_bytes_val: bytes,
        panel: dict | None = None,
        *,
        g6_status: int = 200,
        g6_body: dict | None = None,
        g7_status: int = 200,
        g7_body: dict | None = None,
        g10_status: int = 200,
        g10_body: dict | None = None,
        g5_status: int = 200,
        g5_body: dict | None = None,
        resume_sse: bytes = b"",
    ):
        self.app_token = app_token
        self.assistant_token = assistant_token
        self.session_id = session_id
        self.utterance_id = utterance_id
        self.approval_id = approval_id
        self.sse_bytes_val = sse_bytes_val
        self._panel = panel or {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "risk": "YELLOW",
            "decision": "REQUIRE_APPROVAL",
            "status": "PENDING",
            "steps": [],
            "expires_at_wall": "2026-10-27T00:00:00+08:00",
            "view_hash": "abcdef01",
            "risk_ack_required": False,
            "otp_required": False,
            "findings": [],
            "disclosures": [],
            "evaluation_id": "evl_01",
            "session_id": session_id,
            "origin": "app_form",
            "preflight": {},
        }
        self.g6_status = g6_status
        self.g6_body = g6_body or {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "status": "CONFIRMED",
        }
        self.g7_status = g7_status
        self.g7_body = g7_body or {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "status": "REJECTED",
        }
        self.g10_status = g10_status
        self.g10_body = g10_body or {
            "sent": True,
            "expires_at_wall": "2026-10-27T00:05:00+08:00",
            "retry_after_seconds": 30,
        }
        self.g5_status = g5_status
        self.g5_body = g5_body or {
            "plan_id": "pln_01",
            "status": "EXECUTED",
            "decision": "ALLOW",
            "execution": {
                "plan_id": "pln_01",
                "status": "EXECUTED",
                "steps": [],
                "versions": {},
                "committed_at": "2026-10-27T00:00:00+08:00",
                "error_code": None,
                "replayed": False,
            },
        }
        self.resume_sse = resume_sse or _sse_bytes(
            (
                "values",
                json.dumps(
                    {
                        "decision": {
                            "plan_id": "pln_01",
                            "decision": "ALLOW",
                            "approval_id": None,
                            "findings": [],
                            "replan_hints": [],
                        },
                        "execution": {
                            "plan_id": "pln_01",
                            "status": "EXECUTED",
                            "steps": [],
                            "versions": {},
                            "committed_at": "2026-10-27T00:00:00+08:00",
                            "error_code": None,
                            "replayed": False,
                        },
                    }
                ),
            ),
            ("messages", json.dumps([{"type": "ai", "content": "转账已完成"}])),
        )
        self.called: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method
        self.called.append({"method": method, "url": url})
        g = self.gateway_url
        a = self.agent_url
        sid = self.session_id
        uid = self.utterance_id
        aid = self.approval_id

        if method == "POST" and url == f"{g}/auth/login":
            return httpx.Response(
                200,
                json={
                    "app_token": self.app_token,
                    "expires_at_wall": "2026-10-27T00:00:00+08:00",
                    "customer": {"customer_id": "cus_test", "display_name": "Test"},
                },
                request=request,
            )
        if method == "POST" and url == f"{g}/sessions":
            return httpx.Response(
                201,
                json={"session_id": sid, "created_at_wall": "2026-10-26T00:00:00+08:00"},
                request=request,
            )
        if method == "POST" and url == f"{g}/sessions/{sid}/utterances":
            return httpx.Response(
                201,
                json={
                    "utterance": {"id": uid, "kind": "typed", "text": "转800元给李四"},
                    "assistant_token": self.assistant_token,
                    "expires_at_wall": "2026-10-27T00:00:00+08:00",
                },
                request=request,
            )
        if method == "POST" and url == f"{a}/threads":
            return httpx.Response(200, json={"thread_id": sid}, request=request)
        if method == "POST" and url == f"{a}/threads/{sid}/runs/stream":
            return httpx.Response(
                200,
                content=self.sse_bytes_val,
                headers={"Content-Type": "text/event-stream"},
                request=request,
            )
        if method == "GET" and url == f"{g}/approvals/{aid}":
            return httpx.Response(200, json=self._panel, request=request)
        if method == "POST" and url == f"{g}/approvals/{aid}/confirm":
            return httpx.Response(self.g6_status, json=self.g6_body, request=request)
        if method == "POST" and url == f"{g}/approvals/{aid}/reject":
            return httpx.Response(self.g7_status, json=self.g7_body, request=request)
        if method == "POST" and url == f"{g}/approvals/{aid}/otp":
            return httpx.Response(self.g10_status, json=self.g10_body, request=request)
        # G5: GET /plans/{id}
        if method == "GET" and url.startswith(f"{g}/plans/"):
            return httpx.Response(self.g5_status, json=self.g5_body, request=request)
        # L1 resume: POST /threads/{sid}/runs/stream with command.resume
        # We need to differentiate from the initial stream.  The handler
        # uses the same URL; we switch based on whether it's the second call.
        # We track stream calls on a counter.
        if not hasattr(self, "_stream_count"):
            self._stream_count = 0
        if method == "POST" and url == f"{a}/threads/{sid}/runs/stream":
            self._stream_count += 1
            if self._stream_count == 1:
                # Initial stream (before approval)
                return httpx.Response(
                    200,
                    content=self.sse_bytes_val,
                    headers={"Content-Type": "text/event-stream"},
                    request=request,
                )
            else:
                # Resume stream (after confirmation)
                return httpx.Response(
                    200,
                    content=self.resume_sse,
                    headers={"Content-Type": "text/event-stream"},
                    request=request,
                )
        return httpx.Response(
            404,
            json={"code": "NOT_FOUND", "detail": "not found"},
            request=request,
        )


def _make_config(
    *,
    confirm: bool = False,
    customer_id: str = "cus_test",
    text: str = "转800元给李四",
    pin: str = "123456",
    out_path: str | None = None,
) -> mod.Config:
    return mod.Config(
        gateway_url="http://gateway:8200",
        agent_url="http://agent:8100",
        customer_id=customer_id,
        text=text,
        pin=pin,
        request_id=str(uuid.uuid4()),
        confirm=confirm,
        out_path=out_path,
    )


# ── Tests: --confirm flag off (T1 backward compatibility) ────────────────────


class TestConfirmFlagOff:
    """Without --confirm, behaviour is identical to T1."""

    def test_no_confirm_returns_pending_approval(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=False)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events)

        assert exit_code == mod.EXIT_PENDING_APPROVAL
        # No G6/G7/G10 calls should have been made.
        confirm_urls = [c["url"] for c in handler.called if "/confirm" in c["url"]]
        reject_urls = [c["url"] for c in handler.called if "/reject" in c["url"]]
        assert confirm_urls == []
        assert reject_urls == []


# ── Tests: user says no → reject ────────────────────────────────────────────


class TestUserRejects:
    """User types something other than 'yes' → G7 reject is called."""

    def test_user_types_no_calls_g7_reject(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events, input_fn=lambda _: "no")

        assert exit_code == mod.EXIT_REJECTED_BY_USER
        # G7 must have been called.
        reject_calls = [c for c in handler.called if "/reject" in c["url"]]
        assert len(reject_calls) == 1
        # G6 must NOT have been called.
        confirm_calls = [c for c in handler.called if "/confirm" in c["url"]]
        assert confirm_calls == []

    def test_user_types_empty_calls_g7_reject(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """Empty input → reject (not 'yes')."""
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events, input_fn=lambda _: "")

        assert exit_code == mod.EXIT_REJECTED_BY_USER


# ── Tests: user confirms (YELLOW, no risk_ack) ──────────────────────────────


class TestUserConfirmsYellow:
    """YELLOW risk: yes → G6 confirm (no risk_ack, no OTP) → L1 resume → G5."""

    def test_confirm_carries_view_hash_from_g9(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
        plan_id: str,
    ):
        """G6 request body includes the view_hash from the G9 panel."""
        g6_captured: list[bytes] = []

        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        base = _FullHandler(app_token, assistant_token, session_id, utterance_id, approval_id, sse)

        def wrapper(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/confirm" in str(request.url):
                g6_captured.append(request.read())
            return base(request)

        transport = httpx.MockTransport(wrapper)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            mod.run(config, client, events, input_fn=lambda _: "yes")

        assert len(g6_captured) == 1
        body = json.loads(g6_captured[0])
        assert body["view_hash"] == "abcdef01"

    def test_confirm_full_yellow_flow_returns_executed(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """Successful yellow confirm → G6 → L1 resume → G5=EXECUTED → EXIT_CONFIRMED."""
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events, input_fn=lambda _: "yes")

        assert exit_code == mod.EXIT_CONFIRMED
        # L1 must have been resumed (second stream call).
        stream_calls = [c for c in handler.called if "/runs/stream" in c["url"]]
        assert len(stream_calls) == 2

    def test_g5_failed_returns_exit_execution_failed(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """G5 status=FAILED → EXIT_EXECUTION_FAILED."""
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            g5_body={"plan_id": "pln_01", "status": "FAILED", "decision": "ALLOW"},
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events, input_fn=lambda _: "yes")

        assert exit_code == mod.EXIT_EXECUTION_FAILED

    def test_g5_unknown_returns_exit_execution_unknown(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """G5 status=UNKNOWN → EXIT_EXECUTION_UNKNOWN."""
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            g5_body={"plan_id": "pln_01", "status": "UNKNOWN", "decision": "ALLOW"},
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events, input_fn=lambda _: "yes")

        assert exit_code == mod.EXIT_EXECUTION_UNKNOWN

    def test_resume_sse_deny_returns_deny(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """After resume, L1 returns DENY → non-success exit (not EXIT_CONFIRMED)."""
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        deny_resume_sse = _sse_bytes(
            (
                "values",
                json.dumps({"decision": DECISION_DENY, "plan_id": "pln_01"}),
            ),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            resume_sse=deny_resume_sse,
            g5_body={"plan_id": "pln_01", "status": "DENIED", "decision": "DENY"},
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events, input_fn=lambda _: "yes")

        # After resume with DENY, G5 confirms non-EXECUTED status.
        # Must NOT be EXIT_CONFIRMED.
        assert exit_code == mod.EXIT_EXECUTION_UNKNOWN


# ── Tests: risk_ack required ────────────────────────────────────────────────


class TestRiskAck:
    """When risk_ack_required is true, the user must type a second 'yes'."""

    def test_risk_ack_sent_when_user_agrees(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """risk_ack_required=true, user inputs 'yes' → G6 body includes risk_ack:true."""
        g6_captured: list[bytes] = []

        panel_risk_ack = {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "risk": "YELLOW",
            "decision": "REQUIRE_APPROVAL",
            "status": "PENDING",
            "steps": [],
            "expires_at_wall": "2026-10-27T00:00:00+08:00",
            "view_hash": "abcdef01",
            "risk_ack_required": True,
            "otp_required": False,
            "findings": [],
            "disclosures": [],
            "evaluation_id": "evl_01",
            "session_id": session_id,
            "origin": "app_form",
            "preflight": {},
        }

        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        base = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            panel=panel_risk_ack,
        )

        def wrapper(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/confirm" in str(request.url):
                g6_captured.append(request.read())
            return base(request)

        transport = httpx.MockTransport(wrapper)
        config = _make_config(confirm=True)

        # input_fn: first call is "confirm yes", second is "risk_ack yes"
        responses = iter(["yes", "yes"])

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            mod.run(config, client, events, input_fn=lambda _: next(responses))

        assert len(g6_captured) == 1
        body = json.loads(g6_captured[0])
        assert body.get("risk_ack") is True

    def test_risk_ack_not_sent_without_user_input(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """risk_ack_required=true, user declines risk → G7 reject, no G6."""
        panel_risk_ack = {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "risk": "YELLOW",
            "decision": "REQUIRE_APPROVAL",
            "status": "PENDING",
            "steps": [],
            "expires_at_wall": "2026-10-27T00:00:00+08:00",
            "view_hash": "abcdef01",
            "risk_ack_required": True,
            "otp_required": False,
            "findings": [],
            "disclosures": [],
            "evaluation_id": "evl_01",
            "session_id": session_id,
            "origin": "app_form",
            "preflight": {},
        }

        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            panel=panel_risk_ack,
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        # First "yes" to confirm, then "no" to risk ack.
        responses = iter(["yes", "no"])

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events, input_fn=lambda _: next(responses))

        assert exit_code == mod.EXIT_REJECTED_BY_USER
        # G6 must NOT have been called.
        confirm_calls = [c for c in handler.called if "/confirm" in c["url"]]
        assert confirm_calls == []
        # G7 must have been called.
        reject_calls = [c for c in handler.called if "/reject" in c["url"]]
        assert len(reject_calls) == 1


# ── Tests: RED risk with OTP ────────────────────────────────────────────────


class TestRedRiskOTP:
    """RED risk: otp_required=true → G10 send OTP → getpass → G6 confirm with OTP."""

    def test_red_sends_otp_and_reads_via_getpass(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """RED panel → G10 called → getpass reads OTP → G6 includes OTP."""
        g6_captured: list[bytes] = []
        otp_captured: list[str] = []

        panel_red = {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "risk": "RED",
            "decision": "REQUIRE_APPROVAL",
            "status": "PENDING",
            "steps": [],
            "expires_at_wall": "2026-10-27T00:00:00+08:00",
            "view_hash": "red-hash-01",
            "risk_ack_required": False,
            "otp_required": True,
            "findings": [],
            "disclosures": [],
            "evaluation_id": "evl_01",
            "session_id": session_id,
            "origin": "app_form",
            "preflight": {},
        }

        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        base = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            panel=panel_red,
        )

        def wrapper(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/confirm" in str(request.url):
                g6_captured.append(request.read())
            return base(request)

        transport = httpx.MockTransport(wrapper)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            mod.run(
                config,
                client,
                events,
                input_fn=lambda _: "yes",
                getpass_fn=lambda _: (otp_captured.append("called") or "123456"),
            )

        assert len(g6_captured) == 1
        body = json.loads(g6_captured[0])
        assert body["view_hash"] == "red-hash-01"
        assert body["otp"] == "123456"
        # G10 must have been called.
        otp_calls = [c for c in base.called if "/otp" in c["url"]]
        assert len(otp_calls) == 1
        # getpass must have been called for OTP.
        assert len(otp_captured) == 1

    def test_otp_not_in_output(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """OTP value must never appear in SanitisedEvent detail or stderr."""
        panel_red = {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "risk": "RED",
            "decision": "REQUIRE_APPROVAL",
            "status": "PENDING",
            "steps": [],
            "expires_at_wall": "2026-10-27T00:00:00+08:00",
            "view_hash": "red-hash-01",
            "risk_ack_required": False,
            "otp_required": True,
            "findings": [],
            "disclosures": [],
            "evaluation_id": "evl_01",
            "session_id": session_id,
            "origin": "app_form",
            "preflight": {},
        }

        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            panel=panel_red,
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        events: list[mod.SanitisedEvent] = []
        with httpx.Client(transport=transport) as client:
            mod.run(
                config,
                client,
                events,
                input_fn=lambda _: "yes",
                getpass_fn=lambda _: "s3cret-otp",
            )

        serialised = json.dumps(
            [{"kind": e.kind, "detail": e.detail} for e in events],
            default=str,
        )
        assert "s3cret-otp" not in serialised, f"OTP leaked in events: {serialised[:200]}"

    def test_red_with_risk_ack_and_otp(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """RED + risk_ack_required: confirm yes, risk_ack yes, OTP getpass → G6 has both."""
        g6_captured: list[bytes] = []

        panel_red_full = {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "risk": "RED",
            "decision": "REQUIRE_APPROVAL",
            "status": "PENDING",
            "steps": [],
            "expires_at_wall": "2026-10-27T00:00:00+08:00",
            "view_hash": "red-hash-02",
            "risk_ack_required": True,
            "otp_required": True,
            "findings": [],
            "disclosures": [],
            "evaluation_id": "evl_01",
            "session_id": session_id,
            "origin": "app_form",
            "preflight": {},
        }

        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        base = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            panel=panel_red_full,
        )

        def wrapper(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and "/confirm" in str(request.url):
                g6_captured.append(request.read())
            return base(request)

        transport = httpx.MockTransport(wrapper)
        config = _make_config(confirm=True)

        # confirm yes, risk_ack yes
        responses = iter(["yes", "yes"])

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            mod.run(
                config,
                client,
                events,
                input_fn=lambda _: next(responses),
                getpass_fn=lambda _: "654321",
            )

        assert len(g6_captured) == 1
        body = json.loads(g6_captured[0])
        assert body.get("risk_ack") is True
        assert body.get("otp") == "654321"


# ── Tests: G5 state_changed from G8 ─────────────────────────────────────────


class TestStateChanged:
    """G8 returns 409 STATE_CHANGED → EXIT_STATE_CHANGED with details.decision."""

    def test_state_changed_from_g8_via_g5(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """G5 409 STATE_CHANGED → EXIT_STATE_CHANGED."""
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
            g5_status=409,
            g5_body={
                "code": "STATE_CHANGED",
                "detail": "plan state has changed",
                "decision": {
                    "plan_id": "pln_01",
                    "decision": "DENY",
                    "approval_id": None,
                    "findings": [],
                    "replan_hints": [],
                },
            },
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events, input_fn=lambda _: "yes")

        assert exit_code == mod.EXIT_STATE_CHANGED


# ── Tests: --confirm arg parsing ────────────────────────────────────────────


class TestConfirmArg:
    """--confirm flag in CLI argument parsing."""

    def test_confirm_flag_default_false(self):
        config = mod._parse_args(["--customer-id", "cus_1", "--text", "hi", "--pin", "1234"])
        assert config.confirm is False

    def test_confirm_flag_on(self):
        config = mod._parse_args(
            ["--customer-id", "cus_1", "--text", "hi", "--pin", "1234", "--confirm"],
        )
        assert config.confirm is True


# ── Tests: exit code labels ─────────────────────────────────────────────────


class TestExitLabels:
    """All exit codes have labels and are documented."""

    def test_new_exit_codes_have_labels(self):
        for code in (
            mod.EXIT_CONFIRMED,
            mod.EXIT_REJECTED_BY_USER,
            mod.EXIT_EXECUTION_FAILED,
            mod.EXIT_EXECUTION_UNKNOWN,
            mod.EXIT_STATE_CHANGED,
        ):
            assert code in mod.EXIT_LABELS, f"Exit code {code} missing label"
            assert isinstance(mod.EXIT_LABELS[code], str)


# ── Tests: no secrets ───────────────────────────────────────────────────────


class TestNoSecretsInConfirmFlow:
    """The confirm flow must never leak OTP, PIN, tokens in output."""

    def test_otp_not_in_events(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        sse = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": DECISION_REQUIRE_APPROVAL})),
        )
        handler = _FullHandler(
            app_token,
            assistant_token,
            session_id,
            utterance_id,
            approval_id,
            sse,
        )
        transport = httpx.MockTransport(handler)
        config = _make_config(confirm=True)

        events: list[mod.SanitisedEvent] = []
        with httpx.Client(transport=transport) as client:
            mod.run(config, client, events, input_fn=lambda _: "yes")

        # No synthetic token leaks.
        for e in events:
            serialised_detail = json.dumps(e.detail, default=str)
            assert "Bearer eyJ" not in serialised_detail
        # G6 confirm body fields must not be echo'd into events in plaintext.
        serialised = json.dumps([{"kind": e.kind, "detail": e.detail} for e in events], default=str)
        assert app_token not in serialised
        assert assistant_token not in serialised
