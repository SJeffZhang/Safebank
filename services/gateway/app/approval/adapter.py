"""Instance-scoped approval helpers without global state (W4b2b, D0).

Each endpoint extracts the HMAC key from ``request.app.state``; the key belongs
to the app that is serving the request. Two apps with different runtimes use
different keys — the endpoints never rely on a global ``configure()`` call.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import Request


def approval_hmac_key(request: Request) -> bytes:
    """HMAC key for approval binding verification (from runtime snapshot)."""
    return request.app.state.approval_hmac_key


def profile(request: Request) -> str:
    """Gateway profile string (B0/B1/B2/...) for binding reconstruction."""
    return request.app.state.settings.profile


def wall_now(request: Request) -> Callable[[], datetime]:
    """Return the app's wall-clock callable."""
    return request.app.state.wall_now
