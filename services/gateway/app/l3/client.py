"""Async L3 client (PRD §7.8): GATEWAY_SERVICE_TOKEN on every request.

Timeouts are per endpoint: prepare 3s, execute 10s, everything else 5s. A
timeout or dropped connection surfaces as :class:`L3Timeout` /
:class:`L3Unavailable` so callers can enter the UNKNOWN recovery path instead
of guessing a failure.
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT_PREPARE = 3.0
TIMEOUT_EXECUTE = 10.0
TIMEOUT_DEFAULT = 5.0


class L3Error(Exception):
    """Base for L3 transport failures."""


class L3Timeout(L3Error):
    pass


class L3Unavailable(L3Error):
    pass


class L3Client:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            headers={"X-Service-Token": token},
        )

    @property
    def timeouts(self) -> dict[str, float]:
        return {
            "prepare": TIMEOUT_PREPARE,
            "execute": TIMEOUT_EXECUTE,
            "default": TIMEOUT_DEFAULT,
        }

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, json: Any = None, timeout: float, params: Any = None
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method, path, json=json, params=params, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise L3Timeout(f"{method} {path} timed out after {timeout}s") from exc
        except httpx.HTTPError as exc:
            raise L3Unavailable(f"{method} {path} failed: {exc}") from exc

    async def snapshot(self, body: dict[str, Any]) -> httpx.Response:
        return await self._request("POST", "/internal/snapshot", json=body, timeout=TIMEOUT_DEFAULT)

    async def read(self, tool: str, body: dict[str, Any]) -> httpx.Response:
        return await self._request(
            "POST", f"/internal/read/{tool}", json=body, timeout=TIMEOUT_DEFAULT
        )

    async def prepare(self, body: dict[str, Any]) -> httpx.Response:
        return await self._request(
            "POST", "/internal/plans/prepare", json=body, timeout=TIMEOUT_PREPARE
        )

    async def execute(self, body: dict[str, Any]) -> httpx.Response:
        return await self._request(
            "POST", "/internal/plans/execute", json=body, timeout=TIMEOUT_EXECUTE
        )

    async def execution(self, customer_id: str, plan_id: str) -> httpx.Response:
        return await self._request(
            "GET",
            f"/internal/executions/{plan_id}",
            params={"customer_id": customer_id},
            timeout=TIMEOUT_DEFAULT,
        )

    async def reconcile(self, plan_id: str, body: dict[str, Any]) -> httpx.Response:
        return await self._request(
            "POST", f"/internal/executions/{plan_id}/reconcile", json=body, timeout=TIMEOUT_DEFAULT
        )

    async def outbox(self, after: int = 0, limit: int = 50) -> httpx.Response:
        return await self._request(
            "GET",
            "/internal/outbox",
            params={"after": after, "limit": limit},
            timeout=TIMEOUT_DEFAULT,
        )

    async def outbox_ack(self, body: dict[str, Any]) -> httpx.Response:
        return await self._request(
            "POST", "/internal/outbox/ack", json=body, timeout=TIMEOUT_DEFAULT
        )
