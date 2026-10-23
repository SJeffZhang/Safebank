"""Unified error model (PRD §9.1): ``{error:{code,message,details,trace_id}}``.

Only codes from ``safebank_contracts.codes.Code`` may be used; adding a new code
requires a contract change that is out of scope for W0.
"""

from __future__ import annotations

from typing import Any

from safebank_contracts.codes import Code


class GatewayError(Exception):
    def __init__(
        self,
        code: Code,
        status: int = 400,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.message = message or code.value
        self.details = details or {}
        super().__init__(self.message)

    def body(self, trace_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
                "trace_id": trace_id,
            }
        }
