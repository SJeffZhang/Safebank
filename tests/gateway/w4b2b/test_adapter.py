"""Pure-function tests for the W4b2b approval adapter (no DB).

Tests that the adapter correctly extracts keys/profile/wall_now from
``request.app.state`` and that the keys are instance-scoped (D0).
"""

from __future__ import annotations

import pytest


def test_imports():
    """Smoke test: adapter module is importable."""
    from services.gateway.app.approval.adapter import (  # noqa: F401
        approval_hmac_key,
        profile,
        wall_now,
    )


def test_approval_hmac_key_returns_bytes():
    """approval_hmac_key() returns the bytes stored on app.state."""
    from unittest.mock import MagicMock

    from services.gateway.app.approval.adapter import approval_hmac_key

    request = MagicMock()
    request.app.state.approval_hmac_key = b"test-key-32-bytes-long-at-least!!"
    result = approval_hmac_key(request)
    assert result == b"test-key-32-bytes-long-at-least!!"
    assert isinstance(result, bytes)


def test_profile_returns_string():
    """profile() returns the profile string from settings."""
    from unittest.mock import MagicMock

    from services.gateway.app.approval.adapter import profile

    request = MagicMock()
    request.app.state.settings.profile = "B2"
    result = profile(request)
    assert result == "B2"
    assert isinstance(result, str)


def test_wall_now_returns_callable():
    """wall_now() returns the callable from app.state."""
    from datetime import datetime, timezone, timedelta
    from unittest.mock import MagicMock

    from services.gateway.app.approval.adapter import wall_now

    fixed = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    request = MagicMock()
    request.app.state.wall_now = lambda: fixed
    fn = wall_now(request)
    assert callable(fn)
    result = fn()
    assert result == fixed
    assert result.tzinfo is not None


def test_two_requests_different_keys_return_different_values():
    """Two requests with different app.state return different key values (D0:
    instance-scoped, no global state leakage)."""
    from unittest.mock import MagicMock

    from services.gateway.app.approval.adapter import approval_hmac_key

    req1 = MagicMock()
    req1.app.state.approval_hmac_key = b"aaaa-bbbb-cccc-dddd-eeee-ffff-1111"
    req2 = MagicMock()
    req2.app.state.approval_hmac_key = b"zzzz-yyyy-xxxx-wwww-vvvv-uuuu-9999"

    assert approval_hmac_key(req1) != approval_hmac_key(req2)
    assert approval_hmac_key(req1) == b"aaaa-bbbb-cccc-dddd-eeee-ffff-1111"
    assert approval_hmac_key(req2) == b"zzzz-yyyy-xxxx-wwww-vvvv-uuuu-9999"