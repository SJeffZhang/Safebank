"""Startup configuration and launch guards (PRD §7.3, §10.1, §11.1)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from safebank_contracts.codes import Code

from .errors import GatewayError

DEFAULT_PROFILE = "B2"
PROFILES = frozenset({"B0", "B1", "B2", "B2_NO_PREFLIGHT", "B2_NO_WORKFLOW", "B2_NO_TAINT"})

TRUTHY = frozenset({"1", "true", "yes", "on"})


def flag(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in TRUTHY


@dataclass(frozen=True)
class Settings:
    demo_mode: bool
    eval_mode: bool
    profile: str
    demo_console_token: str | None
    public_bind: str
    internal_bind: str
    max_executions: int
    database_url: str | None
    l3_base_url: str
    service_tokens: dict[str, str]

    def validate(self) -> None:
        """Refuse to start on configuration combinations that weaken isolation."""
        if self.demo_mode and self.eval_mode:
            raise GatewayError(
                Code.FORBIDDEN,
                500,
                message="DEMO_MODE and EVAL_MODE are mutually exclusive",
            )
        if self.profile not in PROFILES:
            raise GatewayError(
                Code.FORBIDDEN,
                500,
                message=(
                    f"unknown GATEWAY_PROFILE {self.profile!r}; expected one of {sorted(PROFILES)}"
                ),
            )
        if self.profile != DEFAULT_PROFILE and not self.eval_mode:
            raise GatewayError(
                Code.FORBIDDEN,
                500,
                message="non-B2 GATEWAY_PROFILE requires EVAL_MODE=true",
            )
        if self.demo_console_token is not None and len(self.demo_console_token.encode()) < 32:
            raise GatewayError(
                Code.FORBIDDEN,
                500,
                message="DEMO_CONSOLE_TOKEN must be at least 32 bytes when present",
            )
        if self.max_executions < 1:
            raise GatewayError(Code.FORBIDDEN, 500, message="GATEWAY_MAX_EXECUTIONS must be >= 1")
        present = [value for value in self.service_tokens.values() if value]
        if len(set(present)) != len(present):
            raise GatewayError(
                Code.FORBIDDEN, 500, message="F/A/S/D service tokens must be pairwise distinct"
            )


def from_env(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    token = env.get("DEMO_CONSOLE_TOKEN") or None
    return Settings(
        demo_mode=flag(env.get("DEMO_MODE")),
        eval_mode=flag(env.get("EVAL_MODE")),
        profile=env.get("GATEWAY_PROFILE") or DEFAULT_PROFILE,
        demo_console_token=token,
        public_bind=env.get("GATEWAY_PUBLIC_BIND") or "127.0.0.1",
        internal_bind=env.get("GATEWAY_INTERNAL_BIND") or "127.0.0.1",
        max_executions=int(env.get("GATEWAY_MAX_EXECUTIONS") or "8"),
        database_url=env.get("GATEWAY_DATABASE_URL"),
        l3_base_url=env.get("BANK_BACKEND_URL") or "http://bank-backend:8300",
        service_tokens={
            "F": env.get("FRONTEND_SERVICE_TOKEN") or "",
            "A": env.get("AGENT_SERVICE_TOKEN") or "",
            "S": env.get("SCHEDULER_SERVICE_TOKEN") or "",
            "D": env.get("DEMO_CONSOLE_TOKEN") or "",
        },
    )
