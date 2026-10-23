from safebank_contracts.state import BankState
from safebank_contracts.tools import WRITE_ARGS

from services.gateway.app.policy.registry import action_kind, load_policies, outflow, policy
from services.gateway.app.schemas.enums import ActionKind


def test_registry_covers_exactly_the_14_write_tools():
    policies = load_policies()
    assert set(policies) == set(WRITE_ARGS)


def test_base_risks_and_sensitive_params_are_filled():
    assert policy("transfer").base_risk == "YELLOW"
    assert policy("transfer").outflow is False  # 初值；对外由 outflow() 按参数判定
    assert policy("submit_risk_assessment").allowed_origins == ("app_form",)
    assert policy("unfreeze_card").otp_required is True
    assert "amount_cents" in policy("transfer").sensitive_params


def test_action_kind_transfer_self_vs_external():
    assert action_kind("transfer", {"to_account_id": "acc_2"}, None) is ActionKind.TRANSFER_SELF
    assert (
        action_kind("transfer", {"payee_id": "pye_1", "to_account_id": None}, None)
        is ActionKind.TRANSFER_EXTERNAL
    )


def test_action_kind_card_limits_loosen_and_tighten():
    from safebank_contracts.enums import CardStatus
    from safebank_contracts.state import Card

    card = Card(
        id="crd_1",
        account_id="acc_1",
        last4="0000",
        status=CardStatus.ACTIVE,
        single_limit_cents=500_000,
        daily_limit_cents=2_000_000,
        controls={"online": False, "overseas": False, "atm": False, "quickpass": False},
        created_at="2026-10-15T09:00:00+08:00",
    )
    state = BankState(
        sim_now="2026-10-15T09:00:00+08:00",
        clock_version=0,
        versions={},
        accounts={},
        customers={},
        cards={"crd_1": card},
        payees={},
    )
    assert (
        action_kind(
            "update_card_limits", {"card_id": "crd_1", "single_limit_cents": 800_000}, state
        )
        is ActionKind.CARD_LIMIT_LOOSEN
    )
    assert (
        action_kind(
            "update_card_limits", {"card_id": "crd_1", "single_limit_cents": 100_000}, state
        )
        is ActionKind.CARD_LIMIT_TIGHTEN
    )


def test_action_kind_card_absent_fails_closed_r3():
    import pytest

    empty = BankState(
        sim_now="2026-10-15T09:00:00+08:00",
        clock_version=0,
        versions={},
        accounts={},
        customers={},
        cards={},
        payees={},
    )
    with pytest.raises(LookupError):
        action_kind(
            "update_card_limits",
            {"card_id": "crd_missing", "single_limit_cents": 99_999_999},
            empty,
        )
    with pytest.raises(LookupError):
        action_kind("update_card_controls", {"card_id": "crd_missing", "online": True}, empty)


def test_action_kind_huge_limit_is_loosen_when_card_present_r3():
    from safebank_contracts.enums import CardStatus
    from safebank_contracts.state import Card

    card = Card(
        id="crd_1",
        account_id="acc_1",
        last4="0000",
        status=CardStatus.ACTIVE,
        single_limit_cents=500_000,
        daily_limit_cents=2_000_000,
        controls={"online": False, "overseas": False, "atm": False, "quickpass": False},
        created_at="2026-10-15T09:00:00+08:00",
    )
    state = BankState(
        sim_now="2026-10-15T09:00:00+08:00",
        clock_version=0,
        versions={},
        accounts={},
        customers={},
        cards={"crd_1": card},
        payees={},
    )
    assert (
        action_kind(
            "update_card_limits",
            {"card_id": "crd_1", "single_limit_cents": 99_999_999},
            state,
        )
        is ActionKind.CARD_LIMIT_LOOSEN
    )


def test_outflow_matches_external_transfer_and_schedule_r4():
    assert outflow("transfer", {"payee_id": "pye_1", "to_account_id": None}, None) is True
    assert outflow("transfer", {"to_account_id": "acc_2"}, None) is False
    assert (
        outflow(
            "schedule_transfer",
            {"payee_id": "pye_1", "to_account_id": None, "period": "MONTHLY"},
            None,
        )
        is True
    )
    assert (
        outflow("schedule_transfer", {"to_account_id": "acc_2", "period": "MONTHLY"}, None) is False
    )
    assert (
        outflow(
            "subscribe_product",
            {"account_id": "a", "product_id": "p", "amount_cents": 1},
            None,
        )
        is False
    )
    assert (
        outflow("redeem_product", {"account_id": "a", "product_id": "p", "units": "1.0000"}, None)
        is False
    )
