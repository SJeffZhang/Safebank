"""Scripted httpx transport for exercising the gateway client without a network.

Mirrors ``tests/agent/llm_mock.py``: each request is recorded (method, url, headers,
parsed JSON body) and the next queued response is played back.  Responses may be
``httpx.Response`` objects or callables; a callable receives the ``httpx.Request`` and
may raise an ``httpx`` transport error to simulate timeouts / dropped connections.
"""

from __future__ import annotations

import json

import httpx


class ScriptedGateway:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(request.content) if request.content else None,
            }
        )
        response = self.responses.pop(0)
        if callable(response):
            return response(request)
        return response

    @property
    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=False)


def json_response(status: int, body: dict | list | None) -> httpx.Response:
    return httpx.Response(status, json=body)


def error_response(
    status: int,
    code: str,
    *,
    details: dict | None = None,
    message: str = "",
    trace_id: str = "trc_1",
) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "trace_id": trace_id,
            }
        },
    )
