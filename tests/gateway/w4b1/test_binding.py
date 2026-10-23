"""W4b1 Task 2: explicit configure and HMAC binding (no DB)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.approval import service as approval_service
from services.gateway.app.approval.planview import build_view
from services.gateway.app.approval.service import (
    ApprovalBinding,
    binding_mac,
    configure,
    verify_binding,
)
from services.gateway.app.errors import GatewayError
from tests.gateway.w4b1.conftest import (
    HMAC_KEY,
    WALL,
    make_ctx,
    transfer_step,
)


def _binding(**overrides):
    fields = dict(
        customer_id="cus_a",
        sid="sid_1",
        plan_id="pln_1",
        plan_hash="a" * 64,
        evaluation_id="evl_1",
        versions={"cus_a": 1},
        clock_version=1,
        policy_version="sha256:test",
        profile="B2",
        view_hash="b" * 64,
        expires_at_wall=WALL + timedelta(minutes=5),
    )
    fields.update(overrides)
    return ApprovalBinding(**fields)


def test_configure_rejects_short_key():
    with pytest.raises(ValueError):
        configure(b"short", profile="B2", wall_now=lambda: WALL)


def test_configure_rejects_non_bytes_key():
    with pytest.raises(ValueError):
        configure("x" * 40, profile="B2", wall_now=lambda: WALL)


def test_configure_rejects_invalid_profile():
    with pytest.raises(ValueError):
        configure(HMAC_KEY, profile="B3", wall_now=lambda: WALL)


def test_configure_rejects_naive_wall():
    with pytest.raises(ValueError):
        configure(HMAC_KEY, profile="B2", wall_now=lambda: datetime(2026, 10, 15, 9, 0, 0))


def test_configure_rejects_non_callable_wall():
    with pytest.raises(ValueError):
        configure(HMAC_KEY, profile="B2", wall_now=None)


def test_binding_roundtrip_and_verify(approval_config):
    binding = _binding()
    mac = binding_mac(HMAC_KEY, binding)
    assert len(mac) == 64
    assert verify_binding(HMAC_KEY, binding, mac) is True
    assert verify_binding(HMAC_KEY, binding, "0" * 64) is False


def test_each_binding_field_is_covered(approval_config):
    base = _binding()
    mac = binding_mac(HMAC_KEY, base)
    tampered = [
        _binding(customer_id="cus_b"),
        _binding(sid="sid_2"),
        _binding(plan_id="pln_2"),
        _binding(plan_hash="c" * 64),
        _binding(evaluation_id="evl_2"),
        _binding(versions={"cus_a": 2}),
        _binding(clock_version=2),
        _binding(policy_version="sha256:other"),
        _binding(profile="B1"),
        _binding(view_hash="d" * 64),
        _binding(expires_at_wall=WALL + timedelta(minutes=4)),
    ]
    for binding in tampered:
        assert verify_binding(HMAC_KEY, binding, mac) is False, binding


def test_binding_requires_aware_expiry():
    with pytest.raises(ValueError):
        _binding(expires_at_wall=datetime(2026, 10, 15, 9, 5, 0))


def test_binding_requires_nonempty_strings():
    with pytest.raises(ValueError):
        _binding(plan_id="")


def test_binding_mac_normalizes_timezone_offset(approval_config):
    # A different tz offset for the same instant must produce the same MAC.
    east = _binding(expires_at_wall=WALL + timedelta(minutes=5))
    west = _binding(expires_at_wall=(WALL + timedelta(minutes=5)).astimezone(UTC))
    assert binding_mac(HMAC_KEY, east) == binding_mac(HMAC_KEY, west)


def test_create_without_config_fails_closed(approval_config):
    ctx = make_ctx((transfer_step(),))
    view, _ = build_view(ctx)
    plan = {
        "id": "pln_1",
        "customer_id": "cus_a",
        "sid": "sid_1",
        "origin": "agent",
        "plan_hash": "a" * 64,
    }
    evaluation = {
        "id": "evl_1",
        "decision": {"decision": "REQUIRE_APPROVAL"},
        "risk": "YELLOW",
        "versions": {"cus_a": 1},
        "clock_version": 1,
        "policy_version": "sha256:test",
    }
    with pytest.raises(GatewayError) as exc:
        asyncio.run(
            approval_service.create_for_evaluation(
                None, plan=plan, evaluation=evaluation, view=view
            )
        )
    assert exc.value.status == 503
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
