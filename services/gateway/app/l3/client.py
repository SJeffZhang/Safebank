"""Async L3 client (PRD §7.8): GATEWAY_SERVICE_TOKEN on every request.

Transport failures are split so callers can decide recovery:
- :class:`L3NotDelivered` — the request certainly did not reach L3 (connect /
  pool timeout, connect error): safe to retry.
- :class:`L3OutcomeUnknown` — the request may have been delivered (read/write
  timeout, protocol error): must enter the UNKNOWN recovery path.
- :class:`L3BusinessError` — L3 answered with a non-2xx error body.

Timeouts default to prepare 3s, execute 10s, everything else 5s and can be
injected for tests.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_TIMEOUTS = {"prepare": 3.0, "execute": 10.0, "default": 5.0}


class L3Error(Exception):
    """Base for L3 transport failures."""


class L3NotDelivered(L3Error):
    """The request certainly did not reach L3."""


class L3OutcomeUnknown(L3Error):
    """The request may have been delivered; the outcome is unknown."""


class L3Unavailable(L3Error):
    """Any other transport failure."""


class L3BusinessError(L3Error):
    def __init__(self, code: str, status: int, details: Any = None) -> None:
        self.code = code
        self.status = status
        self.details = details or {}
        super().__init__(f"L3 returned {status} {code}")


class L3Client:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeouts: dict[str, float] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeouts = dict(DEFAULT_TIMEOUTS if timeouts is None else timeouts)
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport, headers={"X-Service-Token": token}
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        timeout: float,
        params: Any = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, path, json=json, params=params, timeout=timeout
            )
        except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError) as exc:
            raise L3NotDelivered(f"{method} {path} not delivered: {exc}") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            raise L3OutcomeUnknown(f"{method} {path} outcome unknown: {exc}") from exc
        except httpx.HTTPError as exc:
            raise L3Unavailable(f"{method} {path} failed: {exc}") from exc

        if response.status_code >= 400:
            raise self._business_error(response)
        return response.json()

    @staticmethod
    def _business_error(response: httpx.Response) -> L3BusinessError:
        try:
            body = response.json()
            error = body.get("error", {})
            return L3BusinessError(
                error.get("code", "DEPENDENCY_UNAVAILABLE"),
                response.status_code,
                error.get("details"),
            )
        except ValueError:
            return L3BusinessError("DEPENDENCY_UNAVAILABLE", response.status_code)

    async def snapshot(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/snapshot", json=body, timeout=self.timeouts["default"]
        )

    async def read(self, tool: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/internal/read/{tool}", json=body, timeout=self.timeouts["default"]
        )

    async def prepare(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/plans/prepare", json=body, timeout=self.timeouts["prepare"]
        )

    async def execute(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/plans/execute", json=body, timeout=self.timeouts["execute"]
        )

    async def execution(self, customer_id: str, plan_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/internal/executions/{plan_id}",
            params={"customer_id": customer_id},
            timeout=self.timeouts["default"],
        )

    async def reconcile(self, plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/executions/{plan_id}/reconcile",
            json=body,
            timeout=self.timeouts["default"],
        )

    async def outbox(self, after: int = 0, limit: int = 50) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/internal/outbox",
            params={"after": after, "limit": limit},
            timeout=self.timeouts["default"],
        )

    async def outbox_ack(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/outbox/ack", json=body, timeout=self.timeouts["default"]
        )

    async def event(self, event_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/internal/events/{event_id}", timeout=self.timeouts["default"]
        )

    async def event_execute(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/events/{event_id}/execute",
            json=body,
            timeout=self.timeouts["execute"],
        )

    async def event_block(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/events/{event_id}/block",
            json=body,
            timeout=self.timeouts["default"],
        )

    async def clock_advance(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", "/internal/clock/advance", json=body, timeout=self.timeouts["default"]
        )

    async def clock_advance_status(self, job_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/internal/clock/advances/{job_id}", timeout=self.timeouts["default"]
        )
