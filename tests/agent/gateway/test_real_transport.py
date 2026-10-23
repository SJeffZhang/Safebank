"""Real (non-Mock) transport regression for C1c1 timeout/redirect enforcement.

All 124 MockTransport tests never reach httpcore's connection pool, which is how the P0
regression (``request.extensions["timeout"]`` set to a ``httpx.Timeout`` object instead of
a dict) slipped through: every real request raised ``AttributeError``.  These tests use a
default ``httpx.Client`` (real HTTPTransport) against loopback only — a refused local
connection and local ``http.server`` listeners — no external network, no credentials read
from disk.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.agent.app.gateway import GatewayClient
from services.agent.app.gateway.errors import GatewayProtocolError, GatewayTransportError
from services.agent.app.graph.state import AgentContext

SERVICE_TOKEN = "TEST_AGENT_SERVICE_TOKEN"
SECRET = "TEST_ONLY_ASSISTANT_TOKEN_SECRET"


def ctx() -> AgentContext:
    return AgentContext(assistant_token=SECRET, customer_id="cus_a", session_id="ses_a")


def _valid_envelope() -> dict:
    return {
        "data": {
            "accounts": [],
            "daily_outflow_cents": 0,
            "daily_outflow_limit_cents": 0,
            "remaining_daily_outflow_cents": 0,
            "balance_history": [],
        },
        "source_ref": "src_1",
        "versions": {},
        "observed_at": "2026-10-24T00:00:00Z",
    }


class _RecordingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _respond(self) -> None:
        type(self).requests.append(
            {
                "method": self.command,
                "path": self.path,
                "service": self.headers.get("X-Service-Token"),
                "authorization": self.headers.get("Authorization"),
            }
        )
        status, headers, body = type(self).responses.pop(0)
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._respond()

    def log_message(self, *args) -> None:  # silence per-request stderr
        pass


def _start(responses: list) -> tuple[ThreadingHTTPServer, type]:
    handler = type("Handler", (_RecordingHandler,), {"responses": list(responses), "requests": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


def _stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def test_default_client_refused_connection_is_transport_error():
    # Loopback port 1 is closed; a real request must surface a safe transport error, not
    # an AttributeError from httpcore consuming a malformed timeout extension.
    client = GatewayClient("http://127.0.0.1:1", SERVICE_TOKEN, timeout=0.5)
    with pytest.raises(GatewayTransportError):
        client.get_utterances(("utt_a",), ctx())


def test_default_client_real_localhost_roundtrip():
    body = json.dumps(_valid_envelope()).encode()
    server, handler = _start(
        [(200, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))], body)]
    )
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        client = GatewayClient(base, SERVICE_TOKEN, timeout=2.0)
        envelope = client.read_tool("get_accounts", {"history_days": 30}, ctx())
        assert envelope.source_ref == "src_1"
        assert envelope.data["accounts"] == []
        assert len(handler.requests) == 1
        assert handler.requests[0]["path"] == "/tools/read/get_accounts"
        assert handler.requests[0]["service"] == SERVICE_TOKEN
        assert handler.requests[0]["authorization"] == f"Bearer {SECRET}"
    finally:
        _stop(server)


def test_default_client_real_cross_host_redirect_not_followed():
    target_server, target_handler = _start([])
    source_server, source_handler = _start(
        [
            (
                302,
                [
                    ("Location", f"http://127.0.0.1:{target_server.server_address[1]}/x"),
                    ("Content-Length", "0"),
                ],
                b"",
            )
        ]
    )
    try:
        base = f"http://127.0.0.1:{source_server.server_address[1]}"
        client = GatewayClient(base, SERVICE_TOKEN, timeout=2.0)
        with pytest.raises(GatewayProtocolError) as info:
            client.get_utterances(("utt_a",), ctx())
        assert info.value.status == 302
        assert len(source_handler.requests) == 1
        assert source_handler.requests[0]["service"] == SERVICE_TOKEN
        # The A service token must never be forwarded to the redirect target.
        assert target_handler.requests == []
    finally:
        _stop(source_server)
        _stop(target_server)
