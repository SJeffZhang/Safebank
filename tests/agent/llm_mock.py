"""Scripted HTTP transport for exercising the DeepSeek adapters without a network."""

from __future__ import annotations

import json

import httpx


def tool_response(name: str, args: dict, content: str = "") -> httpx.Response:
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-v4-pro",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return _response(body)


def text_response(content: str) -> httpx.Response:
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-v4-pro",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return _response(body)


def _response(body: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json=body,
        request=httpx.Request("POST", "https://api.deepseek.com/beta/chat/completions"),
    )


class ScriptedLLM:
    """Records each request's final payload and plays back queued responses."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"url": str(request.url), "body": json.loads(request.content)})
        response = self.responses.pop(0)
        if callable(response):
            return response(request)
        return response

    @property
    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))
