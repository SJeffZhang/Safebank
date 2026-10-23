"""L1 gateway protocol client (C1c1): G20/G21/G2 reads and G4/G8 writes.

Only ever calls the configured L2 base URL with an A service token plus the runtime
assistant JWT taken fresh from ``AgentContext`` on every call (never cached, never from
state).  It never retries a request, never polls ``UNKNOWN``, never follows redirects and
never manufactures a success or an authorization verdict — that belongs to L2.
"""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.plan import ExecutionResult, PlanDraft
from safebank_contracts.tools import READ_ARGS
from safebank_contracts.views import READ_RESULTS

from services.agent.app.gateway.errors import (
    GatewayProtocolError,
    GatewayResponseError,
    GatewayTransportError,
)
from services.agent.app.gateway.wire import (
    GatewayClarification,
    GatewayDecision,
    GatewayReadEnvelope,
    GatewayUtterance,
    UnknownExecution,
)
from services.agent.app.graph.state import AgentContext

_MAX_UTTERANCE_IDS = 100

# L2 trace ids are generated ids like ``trc_<ulid>``; anything else is treated as unsafe
# server text and omitted rather than persisted or surfaced.
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("base_url is required")
        if not isinstance(service_token, str) or not service_token:
            raise ValueError("service_token is required")
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            raise ValueError("timeout must be a finite positive number") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout
        self._client = http_client or httpx.Client(follow_redirects=False)

    # -- read paths ---------------------------------------------------------

    def get_utterances(
        self, utterance_ids: tuple[str, ...], context: AgentContext
    ) -> tuple[GatewayUtterance, ...]:
        ids = self._validate_ids(utterance_ids)
        path = f"/sessions/{quote(context.session_id, safe='')}/utterances"
        status, payload = self._call("GET", path, context, params={"ids": ",".join(ids)})
        if status != 200:
            raise GatewayProtocolError(status)
        return self._utterances(payload, ids, status)

    def get_clarification(self, context: AgentContext) -> GatewayClarification | None:
        path = f"/sessions/{quote(context.session_id, safe='')}/clarifications/current"
        status, payload = self._call("GET", path, context)
        if status != 200:
            raise GatewayProtocolError(status)
        if not isinstance(payload, dict) or "clarification" not in payload:
            raise GatewayProtocolError(status)
        clarification = payload["clarification"]
        if clarification is None:
            return None
        try:
            return GatewayClarification.model_validate(clarification)
        except ValidationError:
            raise GatewayProtocolError(status) from None

    def read_tool(self, tool: str, args: dict, context: AgentContext) -> GatewayReadEnvelope:
        if tool not in READ_ARGS:
            raise ValueError("unknown or non-read tool")
        try:
            validated_args = READ_ARGS[tool].model_validate(args).model_dump(mode="json")
        except ValidationError:
            raise ValueError(f"invalid args for read tool {tool}") from None
        body = {"session_id": context.session_id, "args": validated_args}
        status, payload = self._call(
            "POST", f"/tools/read/{quote(tool, safe='')}", context, body=body
        )
        if status != 200:
            raise GatewayProtocolError(status)
        try:
            envelope = GatewayReadEnvelope.model_validate(payload)
            READ_RESULTS[tool].model_validate(envelope.data)
        except ValidationError:
            raise GatewayProtocolError(status) from None
        return envelope

    # -- write paths --------------------------------------------------------

    def evaluate(self, draft: PlanDraft, context: AgentContext) -> GatewayDecision:
        if not isinstance(draft, PlanDraft):
            try:
                draft = PlanDraft.model_validate(draft)
            except ValidationError:
                raise ValueError("invalid plan draft") from None
        if draft.session_id != context.session_id:
            raise ValueError("draft.session_id does not match context.session_id")
        status, payload = self._call(
            "POST", "/plans/evaluate", context, body=draft.model_dump(mode="json")
        )
        if status != 200:
            raise GatewayProtocolError(status)
        try:
            return GatewayDecision.model_validate(payload)
        except ValidationError:
            raise GatewayProtocolError(status) from None

    def execute(
        self, plan_id: str, request_id: str, context: AgentContext
    ) -> ExecutionResult | UnknownExecution:
        self._require_id(plan_id, "plan_id")
        self._require_id(request_id, "request_id")
        try:
            status, payload = self._call(
                "POST",
                f"/plans/{quote(plan_id, safe='')}/execute",
                context,
                body={"request_id": request_id},
            )
        except GatewayResponseError as exc:
            # A re-evaluated decision inside a 409 must stay bound to this plan; a
            # revocation-deferred STATE_CHANGED has no decision and stays routable by code.
            if exc.decision is not None and exc.decision.plan_id != plan_id:
                raise GatewayProtocolError(exc.status) from None
            raise
        if status == 202:
            try:
                unknown = UnknownExecution.model_validate(payload)
            except ValidationError:
                raise GatewayProtocolError(status) from None
            if unknown.plan_id != plan_id:
                raise GatewayProtocolError(status)
            return unknown
        if status == 200:
            try:
                result = ExecutionResult.model_validate(payload)
            except ValidationError:
                raise GatewayProtocolError(status) from None
            if result.plan_id != plan_id:
                raise GatewayProtocolError(status)
            return result
        raise GatewayProtocolError(status)

    # -- transport / parsing ------------------------------------------------

    def _headers(self, context: AgentContext) -> dict[str, str]:
        token = context.assistant_token
        if not isinstance(token, str) or not token:
            raise ValueError("assistant_token is required")
        if not isinstance(context.session_id, str) or not context.session_id:
            raise ValueError("session_id is required")
        return {
            "X-Service-Token": self._service_token,
            "Authorization": f"Bearer {token}",
        }

    def _call(
        self,
        method: str,
        path: str,
        context: AgentContext,
        *,
        body: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, Any]:
        url = f"{self._base_url}{path}"
        headers = self._headers(context)
        try:
            request = self._client.build_request(
                method, url, params=params, json=body, headers=headers
            )
            # Enforce a finite per-request timeout and disable redirects regardless of the
            # injected client's defaults: a shared follow_redirects=True client must never
            # forward X-Service-Token to a cross-host redirect target.
            request.extensions["timeout"] = httpx.Timeout(self._timeout)
            response = self._client.send(request, follow_redirects=False)
        except httpx.HTTPError:
            raise GatewayTransportError() from None
        status = response.status_code
        if 300 <= status < 400:
            raise GatewayProtocolError(status)
        try:
            payload = response.json()
        except ValueError:
            raise GatewayProtocolError(status) from None
        if status >= 400:
            raise self._error_from(status, payload)
        return status, payload

    def _error_from(self, status: int, payload: Any) -> GatewayResponseError:
        if not isinstance(payload, dict):
            raise GatewayProtocolError(status)
        error = payload.get("error")
        if not isinstance(error, dict):
            raise GatewayProtocolError(status)
        code_value = error.get("code")
        if not isinstance(code_value, str):
            raise GatewayProtocolError(status)
        try:
            code = Code(code_value)
        except ValueError:
            raise GatewayProtocolError(status) from None
        raw_details = error.get("details", {})
        if raw_details is None:
            raw_details = {}
        if not isinstance(raw_details, dict):
            raise GatewayProtocolError(status)
        # Keep only the route-relevant, wire-validated decision (from the model dump, not
        # a raw copy of the server dict).  Any other server-supplied ``details`` keys are
        # dropped so they cannot be persisted or logged as authoritative text.
        decision = None
        details: dict[str, Any] = {}
        if "decision" in raw_details:
            try:
                decision = GatewayDecision.model_validate(raw_details["decision"])
            except ValidationError:
                raise GatewayProtocolError(status) from None
            details = {"decision": decision.model_dump(mode="json")}
        trace_id = error.get("trace_id", "")
        if not isinstance(trace_id, str) or not _TRACE_ID_RE.fullmatch(trace_id):
            trace_id = ""
        return GatewayResponseError(
            status=status, code=code, details=details, trace_id=trace_id, decision=decision
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _require_id(value: str, name: str) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 256:
            raise ValueError(f"{name} must be a 1..256 character id")
        return value

    @staticmethod
    def _validate_ids(utterance_ids: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(utterance_ids, (tuple, list)) or not utterance_ids:
            raise ValueError("utterance_ids must be a non-empty sequence")
        ids = tuple(utterance_ids)
        if len(ids) > _MAX_UTTERANCE_IDS:
            raise ValueError(f"at most {_MAX_UTTERANCE_IDS} utterance ids")
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate utterance ids")
        for id_ in ids:
            if not isinstance(id_, str) or not id_ or "," in id_:
                raise ValueError("invalid utterance id")
        return ids

    @staticmethod
    def _utterances(
        payload: Any, ids: tuple[str, ...], status: int
    ) -> tuple[GatewayUtterance, ...]:
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise GatewayProtocolError(status)
        try:
            utterances = [GatewayUtterance.model_validate(item) for item in payload["items"]]
        except ValidationError:
            raise GatewayProtocolError(status) from None
        returned = [utterance.id for utterance in utterances]
        if len(set(returned)) != len(returned) or set(returned) != set(ids):
            raise GatewayProtocolError(status)
        by_id = {utterance.id: utterance for utterance in utterances}
        return tuple(by_id[id_] for id_ in ids)
