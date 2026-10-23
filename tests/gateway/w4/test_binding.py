from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidTag

from services.gateway.app.approval import service, sms
from services.gateway.app.errors import GatewayError


def setup():
    service.configure("a" * 32, "b" * 32)
    plan = dict(id="pln_a", customer_id="cus_a", sid="ses_a", plan_hash="c" * 64)
    evaluation = dict(
        id="evl_a",
        plan_id="pln_a",
        versions={"cus_a": 1},
        clock_version=1,
        policy_version="B2:1",
        decision={},
        view_hash="d" * 64,
    )
    approval = dict(
        plan_id="pln_a",
        evaluation_id="evl_a",
        customer_id="cus_a",
        sid="ses_a",
        plan_hash="c" * 64,
        view_hash="d" * 64,
        expires_at_wall=datetime(2026, 10, 23, tzinfo=UTC),
    )
    approval["binding_mac"] = service.binding(plan, evaluation, approval)
    return plan, evaluation, approval


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan_hash", "changed"),
        ("customer_id", "cus_other"),
        ("sid", "ses_other"),
        ("id", "pln_other"),
    ],
)
def test_plan_binding_cannot_be_moved_or_changed(field, value):
    plan, evaluation, approval = setup()
    service.verify_binding(plan, evaluation, approval)
    with pytest.raises(GatewayError):
        service.verify_binding({**plan, field: value}, evaluation, approval)


@pytest.mark.parametrize(
    "field,value",
    [
        ("clock_version", 2),
        ("versions", {"cus_a": 2}),
        ("policy_version", "B0:1"),
        ("view_hash", "wrong"),
    ],
)
def test_evaluation_versions_profile_and_view_bound(field, value):
    plan, evaluation, approval = setup()
    with pytest.raises(GatewayError):
        service.verify_binding(plan, {**evaluation, field: value}, approval)


def test_sms_ciphertext_random_and_audience_bound():
    key = b"k" * 32
    ciphertext = sms.encrypt(key, "apv_a", "view_a", "123456")
    assert ciphertext != sms.encrypt(key, "apv_a", "view_a", "123456")
    assert "123456" not in ciphertext
    assert sms.decrypt(key, "apv_a", "view_a", ciphertext) == "123456"
    with pytest.raises(InvalidTag):
        sms.decrypt(key, "apv_b", "view_a", ciphertext)
    with pytest.raises(InvalidTag):
        sms.decrypt(key, "apv_a", "view_b", ciphertext)


def test_otp_digest_binds_both_view_and_approval():
    setup()
    digest = service.otp_digest("apv_a", "view_a", "123456")
    assert digest != service.otp_digest("apv_b", "view_a", "123456")
    assert digest != service.otp_digest("apv_a", "view_b", "123456")
