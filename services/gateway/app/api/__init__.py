"""Public route modules (PRD §9.5). W0 registers placeholders; W1–W5 fill them.

Each task owns one module and must not edit the others. All handlers return the
unified error body through :class:`GatewayError`; status transitions go through
``app.state``; request idempotency through ``app.idempotency``.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request
from safebank_contracts.codes import Code

from ..errors import GatewayError


def stub(message: str) -> Callable:
    async def _handler(request: Request) -> None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, message)

    return _handler
