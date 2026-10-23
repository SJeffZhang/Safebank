"""Gateway client error taxonomy for C1c2 routing (PRD §6.1.1).

Three fixed categories, none of which manufactures a success or a fund-failure verdict:

- ``RESPONSE`` — a well-formed L2 error ``{error:{code,message,details,trace_id}}`` whose
  ``code`` is a known frozen ``Code``; carries ``status`` / ``code`` / ``details`` (with
  the wire-validated ``decision`` for ``STATE_CHANGED`` / ``POLICY_CHANGED``).
- ``PROTOCOL`` — a response arrived but is not a valid gateway payload (non-JSON, 302,
  unknown code, malformed success/error shape).
- ``TRANSPORT`` — no usable HTTP response (timeout, connection, DNS).  On ``execute`` this
  category marks *outcome uncertainty*: the write may or may not have committed.

No credential, raw ``httpx`` exception (request/headers) or arbitrary server ``message`` is
ever echoed: ``__str__`` returns only the fixed category / status / code.
"""

from __future__ import annotations

from enum import StrEnum

from safebank_contracts.codes import Code

from services.agent.app.gateway.wire import GatewayDecision


class GatewayErrorCategory(StrEnum):
    RESPONSE = "RESPONSE"
    PROTOCOL = "PROTOCOL"
    TRANSPORT = "TRANSPORT"


class GatewayClientError(Exception):
    category: GatewayErrorCategory


class GatewayTransportError(GatewayClientError):
    category = GatewayErrorCategory.TRANSPORT

    def __init__(self) -> None:
        super().__init__("gateway transport error")


class GatewayProtocolError(GatewayClientError):
    category = GatewayErrorCategory.PROTOCOL

    def __init__(self, status: int | None = None) -> None:
        self.status = status
        super().__init__(f"gateway protocol error (status={status})")


class GatewayResponseError(GatewayClientError):
    category = GatewayErrorCategory.RESPONSE

    def __init__(
        self,
        *,
        status: int,
        code: Code,
        details: dict,
        trace_id: str,
        decision: GatewayDecision | None,
    ) -> None:
        self.status = status
        self.code = code
        self.details = details
        self.trace_id = trace_id
        self.decision = decision
        super().__init__(f"gateway error {status} {code.value}")
