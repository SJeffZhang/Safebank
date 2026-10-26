"""Protocol-level tests for scripts/try_agent.py using httpx.MockTransport.

These tests verify the driver's wire behaviour — correct request sequencing,
header isolation, SSE parsing, error handling — without real backend stacks.
They do NOT import try_agent internals; they verify externally observable
behaviour via a mock transport.
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

# Import the module under test (only the public API surface).
import scripts.try_agent as mod

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_mock_200(url: str, body: dict | list) -> httpx.Response:
    """Build a synthetic 200 response for a matched URL pattern."""
    return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def _sse_bytes(*events: tuple[str, str]) -> bytes:
    """Encode SSE events as bytes (as they would arrive on the wire).

    Each tuple is (event_type, data_json_string).
    """
    parts = []
    for evt_type, data in events:
        parts.append(f"event: {evt_type}\ndata: {data}\n\n")
    return "".join(parts).encode("utf-8")


def _mock_transport_with_sse(
    responses: dict[tuple[str, str], httpx.Response],
    sse_bytes: bytes,
) -> httpx.MockTransport:
    """Build a MockTransport that returns specific responses per (method, url-prefix)
    and an SSE body for the stream endpoint.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        # Match by method + URL prefix.
        url = str(request.url)
        for (method, prefix), resp in responses.items():
            if request.method == method and url.startswith(prefix):
                # Shallow-clone so each call gets a fresh response.
                return httpx.Response(
                    resp.status_code,
                    headers=dict(resp.headers),
                    json=resp.json() if hasattr(resp, "json") and callable(resp.json) else None,
                    content=resp.content if hasattr(resp, "content") else None,
                    request=request,
                )
        return httpx.Response(404, json={"detail": "not found"}, request=request)

    return httpx.MockTransport(handler)


# ── Test data ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_token() -> str:
    """Fake app token for testing (not a real JWT)."""
    return os.environ.get("FRONTEND_SERVICE_TOKEN", "fake-frontend-token")


@pytest.fixture(autouse=True)
def _set_frontend_token(monkeypatch) -> None:
    """Ensure FRONTEND_SERVICE_TOKEN is set for all protocol tests."""
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
def approval_id() -> str:
    return "apv_test01"


# ── Tests: SSE parsing ─────────────────────────────────────────────────────


class TestSSEParse:
    def test_basic_updates(self):
        """A single updates event with __interrupt__ is parsed correctly."""
        data = json.dumps({"__interrupt__": [{"value": {"approval_id": "apv_01"}}]})
        resp = httpx.Response(
            200,
            content=_sse_bytes(("updates", data)),
            request=httpx.Request("POST", "http://agent:8100/threads/s_1/runs/stream"),
        )
        events = mod.parse_sse(resp)
        assert len(events) == 1
        assert events[0]["type"] == "updates"
        assert events[0]["body"]["__interrupt__"][0]["value"]["approval_id"] == "apv_01"

    def test_multi_event_stream(self):
        """Multiple SSE events in one response — each is a separate dict."""
        data1 = json.dumps({"__interrupt__": [{"value": {"approval_id": "apv_01"}}]})
        data2 = json.dumps({"decision": "REQUIRE_APPROVAL"})
        data3 = json.dumps([{"type": "ai", "content": "需要确认"}])
        resp = httpx.Response(
            200,
            content=_sse_bytes(
                ("updates", data1),
                ("values", data2),
                ("messages", data3),
            ),
            request=httpx.Request("POST", "http://agent:8100/threads/s_1/runs/stream"),
        )
        events = mod.parse_sse(resp)
        assert len(events) == 3
        assert events[0]["type"] == "updates"
        assert events[1]["type"] == "values"
        assert events[1]["body"]["decision"] == "REQUIRE_APPROVAL"
        assert events[2]["type"] == "messages"

    def test_multi_line_data(self):
        """Data spanning multiple lines is joined."""
        resp = httpx.Response(
            200,
            content=b'event: values\ndata: {\ndata: "key":\ndata:  1\ndata: }\n\n',
            request=httpx.Request("POST", "http://agent:8100/threads/s_1/runs/stream"),
        )
        events = mod.parse_sse(resp)
        assert len(events) == 1
        body = events[0]["body"]
        assert body["key"] == 1

    def test_error_event(self):
        """Error events are parsed and their type is error."""
        resp = httpx.Response(
            200,
            content=_sse_bytes(
                ("error", json.dumps({"message": "Something went wrong", "type": "RuntimeError"}))
            ),
            request=httpx.Request("POST", "http://agent:8100/threads/s_1/runs/stream"),
        )
        events = mod.parse_sse(resp)
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "Something went wrong" in events[0]["body"]["message"]

    def test_comment_lines_ignored(self):
        """SSE comment lines (starting with colon) are skipped."""
        resp = httpx.Response(
            200,
            content=b': keepalive\nevent: values\ndata: {"ok":true}\n\n',
            request=httpx.Request("POST", "http://agent:8100/threads/s_1/runs/stream"),
        )
        events = mod.parse_sse(resp)
        assert len(events) == 1
        assert events[0]["body"] == {"ok": True}

    def test_custom_cards_event(self):
        """Custom events with cards type are parsed."""
        data = json.dumps({"type": "cards", "cards": [{"card_id": "crd_01", "title": "限额"}]})
        resp = httpx.Response(
            200,
            content=_sse_bytes(("custom", data)),
            request=httpx.Request("POST", "http://agent:8100/threads/s_1/runs/stream"),
        )
        events = mod.parse_sse(resp)
        assert len(events) == 1
        assert events[0]["type"] == "custom"
        assert events[0]["body"]["type"] == "cards"

    def test_sse_does_not_200_alone_pass(self):
        """HTTP 200 alone without SSE events produces empty list."""
        resp = httpx.Response(
            200,
            content=b"",
            request=httpx.Request("POST", "http://agent:8100/threads/s_1/runs/stream"),
        )
        events = mod.parse_sse(resp)
        assert events == []

    def test_unknown_event_type_no_field(self):
        """Data without an explicit event field is 'unknown'."""
        resp = httpx.Response(
            200,
            content=b'data: {"x":1}\n\n',
            request=httpx.Request("POST", "http://agent:8100/threads/s_1/runs/stream"),
        )
        events = mod.parse_sse(resp)
        assert len(events) == 1
        assert events[0]["type"] == "unknown"


# ── Tests: full flow via MockTransport ──────────────────────────────────────


class TestFullFlowApproval:
    """End-to-end test: login → session → utterance → SSE → G9 panel."""

    def _build_responses(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
        gateway_url: str = "http://gateway:8200",
        agent_url: str = "http://agent:8100",
    ) -> tuple[httpx.MockTransport, bytes]:
        """Return a MockTransport and the SSE bytes it will return."""
        sse_data = _sse_bytes(
            (
                "updates",
                json.dumps({"__interrupt__": [{"value": {"approval_id": approval_id}}]}),
            ),
            ("values", json.dumps({"decision": "REQUIRE_APPROVAL"})),
            ("messages", json.dumps([{"type": "ai", "content": "请确认转账"}])),
        )

        panel = {
            "approval_id": approval_id,
            "plan_id": "pln_01",
            "risk": "YELLOW",
            "decision": "REQUIRE_APPROVAL",
            "steps": [],
            "status": "PENDING",
            "expires_at_wall": "2026-10-27T00:00:00+08:00",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            # G1 login
            if method == "POST" and url == f"{gateway_url}/auth/login":
                return httpx.Response(
                    200,
                    json={
                        "app_token": app_token,
                        "expires_at_wall": "2026-10-27T00:00:00+08:00",
                        "customer": {"customer_id": "cus_test", "display_name": "Test"},
                    },
                    request=request,
                )
            # G15 create session
            if method == "POST" and url == f"{gateway_url}/sessions":
                return httpx.Response(
                    201,
                    json={"session_id": session_id, "created_at_wall": "2026-10-26T00:00:00+08:00"},
                    request=request,
                )
            # G3 register utterance
            if method == "POST" and url == f"{gateway_url}/sessions/{session_id}/utterances":
                return httpx.Response(
                    201,
                    json={
                        "utterance": {"id": utterance_id, "kind": "typed", "text": "转800元给李四"},
                        "assistant_token": assistant_token,
                        "expires_at_wall": "2026-10-27T00:00:00+08:00",
                    },
                    request=request,
                )
            # L1 create thread
            if method == "POST" and url == f"{agent_url}/threads":
                return httpx.Response(200, json={"thread_id": session_id}, request=request)
            # L1 SSE stream
            if method == "POST" and url == f"{agent_url}/threads/{session_id}/runs/stream":
                return httpx.Response(
                    200,
                    content=sse_data,
                    headers={"Content-Type": "text/event-stream"},
                    request=request,
                )
            # G9 approval panel
            if method == "GET" and url == f"{gateway_url}/approvals/{approval_id}":
                return httpx.Response(200, json=panel, request=request)
            return httpx.Response(404, json={"detail": "not found"}, request=request)

        return httpx.MockTransport(handler), sse_data

    def test_pending_approval_flow(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """Full flow reaches G9 and returns PENDING_APPROVAL."""
        transport, _ = self._build_responses(
            app_token, assistant_token, session_id, utterance_id, approval_id
        )
        config = mod.Config(
            gateway_url="http://gateway:8200",
            agent_url="http://agent:8100",
            customer_id="cus_test",
            text="转800元给李四",
            pin="123456",
            request_id=str(uuid.uuid4()),
            out_path=None,
        )

        with httpx.Client(transport=transport) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events)

        assert exit_code == mod.EXIT_PENDING_APPROVAL
        # Verify G9 was called (the result event for approval_panel).
        result_kinds = [e.kind for e in events]
        assert "result" in result_kinds
        panel_events = [
            e for e in events if e.kind == "result" and e.detail.get("event") == "approval_panel"
        ]
        assert len(panel_events) == 1
        assert panel_events[0].detail["panel"]["approval_id"] == approval_id

    def test_app_token_sent_to_gateway_not_agent(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        approval_id: str,
    ):
        """The app token (for gateway/BFF) is never sent to L1 agent."""
        transport, _ = self._build_responses(
            app_token, assistant_token, session_id, utterance_id, approval_id
        )

        # Wrap the transport to capture headers sent to agent.
        sent_to_agent: list[dict] = []

        def capturing_handler(request: httpx.Request) -> httpx.Response:
            if "agent:8100" in str(request.url):
                sent_to_agent.append(dict(request.headers))
            return transport.handle_request(request)

        wrapped = httpx.MockTransport(capturing_handler)
        config = mod.Config(
            gateway_url="http://gateway:8200",
            agent_url="http://agent:8100",
            customer_id="cus_test",
            text="转800元给李四",
            pin="123456",
            request_id=str(uuid.uuid4()),
        )

        with httpx.Client(transport=wrapped) as client:
            mod.run(config, client, [])

        # Every agent request must use assistant_token, never app_token.
        for hdrs in sent_to_agent:
            auth = hdrs.get("authorization", "")
            assert assistant_token in auth, f"Agent request had wrong token: {auth}"
            assert app_token not in auth, f"Agent request leaked app token: {auth}"

    def test_missing_frontend_token_fail_closed(self, monkeypatch):
        """Without FRONTEND_SERVICE_TOKEN, run raises KeyError (fail-closed)."""
        monkeypatch.delenv("FRONTEND_SERVICE_TOKEN", raising=False)
        config = mod.Config(
            gateway_url="http://gateway:8200",
            agent_url="http://agent:8100",
            customer_id="cus_test",
            text="hi",
            pin="123456",
        )
        with httpx.Client() as client:
            with pytest.raises(KeyError):
                mod.run(config, client, [])


class TestFullFlowNoApproval:
    """End-to-end test where L1 returns non-approval decisions."""

    def _build_no_approval_responses(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
        decision: str,
    ) -> httpx.MockTransport:
        sse_data = _sse_bytes(
            ("values", json.dumps({"decision": decision})),
            ("messages", json.dumps([{"type": "ai", "content": "已完成"}])),
        )

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method
            g = "http://gateway:8200"
            a = "http://agent:8100"
            if method == "POST" and url == f"{g}/auth/login":
                return httpx.Response(
                    200,
                    json={"app_token": app_token, "customer": {"customer_id": "cus_test"}},
                    request=request,
                )
            if method == "POST" and url == f"{g}/sessions":
                return httpx.Response(201, json={"session_id": session_id}, request=request)
            if method == "POST" and url == f"{g}/sessions/{session_id}/utterances":
                return httpx.Response(
                    201,
                    json={
                        "utterance": {"id": utterance_id, "text": "hi"},
                        "assistant_token": assistant_token,
                    },
                    request=request,
                )
            if method == "POST" and url == f"{a}/threads":
                return httpx.Response(200, json={"thread_id": session_id}, request=request)
            if method == "POST" and url == f"{a}/threads/{session_id}/runs/stream":
                return httpx.Response(200, content=sse_data, request=request)
            return httpx.Response(404, json={"detail": "not found"}, request=request)

        return httpx.MockTransport(handler)

    @pytest.mark.parametrize(
        "decision,expected_exit",
        [
            ("ALLOW", mod.EXIT_COMPLETED),
            ("COMPLETED", mod.EXIT_COMPLETED),
            ("CLARIFY", mod.EXIT_CLARIFY),
            ("REPLAN", mod.EXIT_REPLAN),
            ("DENY", mod.EXIT_DENY),
        ],
    )
    def test_terminal_decisions(
        self,
        decision: str,
        expected_exit: int,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
    ):
        transport = self._build_no_approval_responses(
            app_token, assistant_token, session_id, utterance_id, decision
        )
        config = mod.Config(
            gateway_url="http://gateway:8200",
            agent_url="http://agent:8100",
            customer_id="cus_test",
            text="hi",
            pin="123456",
            request_id=str(uuid.uuid4()),
        )
        with httpx.Client(transport=transport) as client:
            exit_code = mod.run(config, client, [])
        assert exit_code == expected_exit

    def test_no_g6_g8_called_without_approval(
        self,
        app_token: str,
        assistant_token: str,
        session_id: str,
        utterance_id: str,
    ):
        """When L1 returns CLARIFY, no G6/G8 calls are made."""
        called_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            called_urls.append(str(request.url))
            url = str(request.url)
            method = request.method
            g = "http://gateway:8200"
            a = "http://agent:8100"
            if method == "POST" and url == f"{g}/auth/login":
                return httpx.Response(
                    200,
                    json={"app_token": app_token, "customer": {"customer_id": "x"}},
                    request=request,
                )
            if method == "POST" and url == f"{g}/sessions":
                return httpx.Response(201, json={"session_id": session_id}, request=request)
            if method == "POST" and url == f"{g}/sessions/{session_id}/utterances":
                return httpx.Response(
                    201,
                    json={"utterance": {"id": utterance_id}, "assistant_token": assistant_token},
                    request=request,
                )
            if method == "POST" and url == f"{a}/threads":
                return httpx.Response(200, json={"thread_id": session_id}, request=request)
            if method == "POST" and url == f"{a}/threads/{session_id}/runs/stream":
                sse = _sse_bytes(
                    ("values", json.dumps({"decision": "CLARIFY"})),
                    ("messages", json.dumps([{"type": "ai", "content": "请补充"}])),
                )
                return httpx.Response(200, content=sse, request=request)
            return httpx.Response(404, json={"detail": "not found"}, request=request)

        config = mod.Config(
            gateway_url="http://gateway:8200",
            agent_url="http://agent:8100",
            customer_id="cus_test",
            text="转账",
            pin="123456",
            request_id=str(uuid.uuid4()),
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            exit_code = mod.run(config, client, [])
        assert exit_code == mod.EXIT_CLARIFY
        # No confirm, reject, execute calls.
        for u in called_urls:
            assert "/approvals/" not in u or "/approvals" not in u
            assert "/confirm" not in u
            assert "/execute" not in u


class TestErrorHandling:
    """HTTP errors and SSE error events are handled correctly."""

    def test_g1_unauthorized(self, fake_token: str):
        """G1 returning 401 yields HTTP_FAILURE."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "Bad PIN"}, request=request)

        config = mod.Config(
            gateway_url="http://gateway:8200",
            agent_url="http://agent:8100",
            customer_id="cus_test",
            text="hi",
            pin="wrong",
            request_id=str(uuid.uuid4()),
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            exit_code = mod.run(config, client, [])
        assert exit_code == mod.EXIT_HTTP_FAILURE

    def test_sse_error_event_yields_unknown(
        self, app_token: str, assistant_token: str, session_id: str, utterance_id: str
    ):
        """L1 SSE error event → EXIT_UNKNOWN."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method
            g = "http://gateway:8200"
            a = "http://agent:8100"
            if method == "POST" and url == f"{g}/auth/login":
                return httpx.Response(
                    200,
                    json={"app_token": app_token, "customer": {"customer_id": "x"}},
                    request=request,
                )
            if method == "POST" and url == f"{g}/sessions":
                return httpx.Response(201, json={"session_id": session_id}, request=request)
            if method == "POST" and url == f"{g}/sessions/{session_id}/utterances":
                return httpx.Response(
                    201,
                    json={"utterance": {"id": utterance_id}, "assistant_token": assistant_token},
                    request=request,
                )
            if method == "POST" and url == f"{a}/threads":
                return httpx.Response(200, json={"thread_id": session_id}, request=request)
            if method == "POST" and url == f"{a}/threads/{session_id}/runs/stream":
                sse = _sse_bytes(
                    ("error", json.dumps({"message": "LLM failure", "type": "RuntimeError"}))
                )
                return httpx.Response(200, content=sse, request=request)
            return httpx.Response(404, json={"detail": "not found"}, request=request)

        config = mod.Config(
            gateway_url="http://gateway:8200",
            agent_url="http://agent:8100",
            customer_id="cus_test",
            text="hi",
            pin="123456",
            request_id=str(uuid.uuid4()),
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            exit_code = mod.run(config, client, [])
        assert exit_code == mod.EXIT_UNKNOWN

    def test_no_secret_in_output(
        self, app_token: str, assistant_token: str, session_id: str, utterance_id: str
    ):
        """Event trace must not contain synthetic secrets."""
        self.maxDiff = None  # noqa

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method
            g = "http://gateway:8200"
            a = "http://agent:8100"
            if method == "POST" and url == f"{g}/auth/login":
                return httpx.Response(
                    200,
                    json={"app_token": app_token, "customer": {"customer_id": "x"}},
                    request=request,
                )
            if method == "POST" and url == f"{g}/sessions":
                return httpx.Response(201, json={"session_id": session_id}, request=request)
            if method == "POST" and url == f"{g}/sessions/{session_id}/utterances":
                return httpx.Response(
                    201,
                    json={"utterance": {"id": utterance_id}, "assistant_token": assistant_token},
                    request=request,
                )
            if method == "POST" and url == f"{a}/threads":
                return httpx.Response(200, json={"thread_id": session_id}, request=request)
            if method == "POST" and url == f"{a}/threads/{session_id}/runs/stream":
                sse = _sse_bytes(("values", json.dumps({"decision": "ALLOW"})))
                return httpx.Response(200, content=sse, request=request)
            return httpx.Response(404, json={"detail": "not found"}, request=request)

        config = mod.Config(
            gateway_url="http://gateway:8200",
            agent_url="http://agent:8100",
            customer_id="cus_test",
            text="hi",
            pin="123456",
            request_id=str(uuid.uuid4()),
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            events: list[mod.SanitisedEvent] = []
            exit_code = mod.run(config, client, events)

        assert exit_code == mod.EXIT_COMPLETED
        serialised = json.dumps(
            [{"kind": e.kind, "detail": e.detail} for e in events],
            default=str,
        )
        assert "Bearer eyJ" not in serialised, f"JWT leaked: {serialised[:200]}"
        assert app_token not in serialised, f"app_token leaked: {serialised[:200]}"
        assert assistant_token not in serialised, f"assistant_token leaked: {serialised[:200]}"


class TestCLI:
    """CLI argument parsing and --help."""

    def test_help(self):
        with pytest.raises(SystemExit) as exc:
            mod._parse_args(["--help"])
        assert exc.value.code == 0

    def test_required_args(self):
        """--customer-id and --text are required."""
        with pytest.raises(SystemExit):
            mod._parse_args([])

    def test_pin_from_arg(self):
        config = mod._parse_args(["--customer-id", "cus_1", "--text", "hi", "--pin", "1234"])
        assert config.customer_id == "cus_1"
        assert config.text == "hi"
        assert config.pin == "1234"

    def test_default_urls(self):
        config = mod._parse_args(["--customer-id", "cus_1", "--text", "hi", "--pin", "1234"])
        assert config.gateway_url == "http://gateway:8200"
        assert config.agent_url == "http://agent:8100"

    def test_custom_urls(self):
        config = mod._parse_args(
            [
                "--customer-id",
                "cus_1",
                "--text",
                "hi",
                "--pin",
                "1234",
                "--gateway-url",
                "http://localhost:8200",
                "--agent-url",
                "http://localhost:8100",
            ]
        )
        assert config.gateway_url == "http://localhost:8200"
        assert config.agent_url == "http://localhost:8100"


class TestSanitise:
    """Output sanitisation guards."""

    def test_ensure_no_secrets_blocks_jwt(self):
        with pytest.raises(RuntimeError, match="SECRET_LEAK"):
            mod._ensure_no_secrets('data: {"token": "Bearer eyJhbGci..."}')

    def test_ensure_no_secrets_blocks_service_token(self):
        with pytest.raises(RuntimeError, match="SECRET_LEAK"):
            mod._ensure_no_secrets("error: FRONTEND_SERVICE_TOKEN missing")

    def test_sanitise_headers_masks_auth(self):
        result = mod._sanitise_headers(
            {
                "Authorization": "Bearer secret123",
                "X-Service-Token": "tok123",
                "Content-Type": "application/json",
            }
        )
        assert result["Authorization"] == "***REDACTED***"
        assert result["X-Service-Token"] == "***REDACTED***"
        assert result["Content-Type"] == "application/json"
