"""Static authorization matrix (PRD §7.1, §9.5/§9.5a/§9.7).

Keys are ``(method, route path_format)`` where path parameters use ``{name}``
(Starlette style). A value is the tuple of allowed caller credentials; an empty
tuple means the route is open. W0 registers the full table; afterwards only the
conductor may change it.

The route modules (``app/api/**``, ``app/internal/**``) exist as placeholders so
that W1–W5 only edit their own module; ``main.py`` includes them once.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..schemas.enums import ServiceKind

APP_AUD = "gateway-app"
ASSISTANT_AUD = "safebank-assistant"
CONSOLE_AUD = "demo-console"

APP = frozenset({"app"})
APPROVE = frozenset({"approve"})
ASSISTANT = frozenset({"assistant"})
CONSOLE = frozenset({"console"})


@dataclass(frozen=True)
class CallerSpec:
    service: ServiceKind | None = None
    jwt_aud: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    require_sid: bool = False


F_SERVICE = CallerSpec(service=ServiceKind.F)
F_APP = CallerSpec(service=ServiceKind.F, jwt_aud=APP_AUD, scopes=APP)
F_APPROVE = CallerSpec(service=ServiceKind.F, jwt_aud=APP_AUD, scopes=APPROVE)
A_ASSISTANT = CallerSpec(
    service=ServiceKind.A, jwt_aud=ASSISTANT_AUD, scopes=ASSISTANT, require_sid=True
)
S_ONLY = CallerSpec(service=ServiceKind.S)
D_SECRET = CallerSpec(service=ServiceKind.D)
D_CONSOLE = CallerSpec(service=ServiceKind.D, jwt_aud=CONSOLE_AUD, scopes=CONSOLE)

Entry = tuple[CallerSpec, ...]

MATRIX: dict[tuple[str, str], Entry] = {
    ("GET", "/health"): (),
    ("GET", "/internal/health"): (S_ONLY,),
    ("POST", "/internal/scheduled/authorize"): (S_ONLY,),
    # G1 / G14 / G22 — W1 (identity/login)
    ("POST", "/auth/login"): (F_SERVICE,),
    ("POST", "/auth/logout"): (F_APP,),
    ("GET", "/auth/demo-customers"): (F_SERVICE,),
    # Sessions and utterances — W1
    ("POST", "/sessions"): (F_APP,),
    ("POST", "/sessions/{sid}/utterances"): (F_APP,),
    ("POST", "/sessions/{sid}/actions"): (F_APP,),
    ("GET", "/sessions/{sid}/utterances"): (A_ASSISTANT, F_APP),
    ("GET", "/sessions/{sid}/clarifications/current"): (A_ASSISTANT, F_APP),
    ("POST", "/sessions/{sid}/assistant-token"): (F_APP,),
    ("GET", "/sessions/{sid}/hints/{hint_id}"): (F_APP,),
    # Read proxy — W2
    ("POST", "/tools/read/{tool}"): (A_ASSISTANT, F_APP),
    # Plans / evaluation — W3
    ("POST", "/plans/evaluate"): (A_ASSISTANT, F_APP),
    # Execution and approvals — W4
    ("GET", "/plans/{plan_id}"): (A_ASSISTANT, F_APP),
    ("POST", "/plans/{plan_id}/execute"): (A_ASSISTANT, F_APP),
    ("GET", "/approvals"): (F_APP,),
    ("GET", "/approvals/{approval_id}"): (F_APPROVE,),
    ("POST", "/approvals/{approval_id}/confirm"): (F_APPROVE,),
    ("POST", "/approvals/{approval_id}/reject"): (F_APPROVE,),
    ("POST", "/approvals/{approval_id}/otp"): (F_APPROVE,),
    # Notifications and audit read — W5
    ("GET", "/plans/{plan_id}/audit"): (F_APP,),
    ("GET", "/notifications"): (F_APP,),
    ("POST", "/notifications/{notification_id}/read"): (F_APP,),
    ("GET", "/audit"): (F_APP,),
    # Demo / evaluation console — W5, only mounted in DEMO_MODE / EVAL_MODE
    ("POST", "/demo/auth/login"): (D_SECRET,),
    ("POST", "/demo/reset"): (D_CONSOLE,),
    ("POST", "/demo/scene/prepare"): (D_CONSOLE,),
    ("POST", "/demo/clock/advance"): (D_CONSOLE,),
    ("GET", "/demo/clock/advances/{job_id}"): (D_CONSOLE,),
    ("POST", "/demo/events"): (D_CONSOLE,),
    ("GET", "/demo/sms"): (D_CONSOLE,),
    ("POST", "/demo/attack/cases/{case_id}/start"): (D_CONSOLE,),
    ("POST", "/demo/attack/submit_plan"): (D_CONSOLE,),
    ("POST", "/demo/attack/plans/{plan_id}/execute"): (D_CONSOLE,),
    ("GET", "/demo/attack/plans/{plan_id}"): (D_CONSOLE,),
    ("POST", "/demo/eval/assert"): (D_CONSOLE,),
    ("GET", "/demo/plans/{plan_id}/comparison"): (D_CONSOLE,),
    ("GET", "/demo/reports/latest"): (D_CONSOLE,),
    ("POST", "/demo/auth/logout"): (D_CONSOLE,),
    ("GET", "/demo/plans"): (D_CONSOLE,),
    ("GET", "/demo/audit"): (D_CONSOLE,),
}


def _regex(path_format: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]*\})", path_format)
    pattern = "".join(
        "[^/]+" if p.startswith("{") and p.endswith("}") else re.escape(p) for p in parts
    )
    return re.compile("^" + pattern + "$")


def lookup(method: str, path: str) -> Entry | None:
    for (fmt_method, fmt_path), entry in MATRIX.items():
        if fmt_method == method.upper() and _regex(fmt_path).fullmatch(path):
            return entry
    return None


def match(method: str, path: str) -> Iterable[CallerSpec]:
    entry = lookup(method, path)
    return () if entry is None else entry


def coverage_errors(
    routes: Iterable[Any], *, demo_mounted: bool, internal: bool = False
) -> list[str]:
    """Assert the mounted routes and the matrix agree (both directions).

    ``routes`` is an iterable of ``(methods, path)`` pairs from a FastAPI app.
    ``internal=True`` asserts the internal app only carries ``/internal/*`` and
    expects only the internal matrix entries; the public app asserts the
    complement. Demo routes are only expected when ``demo_mounted`` is true.
    """
    errors: list[str] = []
    registered: set[tuple[str, str]] = set()
    for methods, path in routes:
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            registered.add((method, path))
    for method, path in registered:
        if lookup(method, path) is None:
            errors.append(f"route not in matrix: {method} {path}")
        if internal and not path.startswith("/internal/"):
            errors.append(f"internal app has a non-internal route: {method} {path}")
        if not internal and path.startswith("/internal/"):
            errors.append(f"public app has an internal route: {method} {path}")
    for method, path in MATRIX:
        if path.startswith("/demo/"):
            if demo_mounted and (method, path) not in registered:
                errors.append(f"matrix entry has no route: {method} {path}")
        elif path.startswith("/internal/"):
            if internal and (method, path) not in registered:
                errors.append(f"matrix entry has no route: {method} {path}")
        elif not internal and (method, path) not in registered:
            errors.append(f"matrix entry has no route: {method} {path}")
    return errors
