"""W4b1 Task 1: deterministic PlanView and semantic content hash (no DB)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from safebank_contracts.enums import Risk

from services.gateway.app.approval.planview import (
    UNASSIGNED_APPROVAL_ID,
    build_view,
    content_hash,
)
from services.gateway.app.pipeline.context import ResolvedStep
from services.gateway.app.schemas.decision import DecisionValue, PreflightView
from services.gateway.app.schemas.enums import PlanStatus
from tests.gateway.w4b1.conftest import (
    WALL,
    make_ctx,
    transfer_step,
    utterance_auth,
)


def _view(steps, **kwargs):
    return build_view(make_ctx(tuple(steps), **kwargs))[0]


def _field(view, step_id, key):
    step = next(s for s in view.steps if s.step_id == step_id)
    return next(f for f in step.fields if f.key == key)


def test_transfer_to_existing_payee_shows_name_tail_bank_amount_memo():
    view = _view([transfer_step(payee_id="pye_ww", amount_cents=80000, memo="房租")])
    step = view.steps[0]
    assert step.title == "转账"
    assert step.irreversible is True
    assert _field(view, "s1", "target").value == "王五"
    assert _field(view, "s1", "target_account").value == "本行 尾号0003"
    assert _field(view, "s1", "amount").value == "800.00"
    assert _field(view, "s1", "amount").unit == "元"
    assert _field(view, "s1", "memo").value == "房租"


def test_self_transfer_shows_own_account_and_internal_marker():
    step = ResolvedStep(
        "s1",
        "transfer",
        {
            "from_account_id": "acc_a",
            "payee_id": None,
            "to_account_id": "acc_s",
            "amount_cents": 10000,
            "memo": None,
        },
        "TRANSFER_SELF",
        "YELLOW",
        False,
    )
    view = _view([step])
    assert _field(view, "s1", "target").value == "储蓄"
    assert _field(view, "s1", "target_account").value == "本行 尾号0002"


def test_external_payee_shows_bank_name_not_internal_marker():
    view = _view([transfer_step(payee_id="pye_zl", amount_cents=5000)])
    assert _field(view, "s1", "target").value == "赵六"
    assert _field(view, "s1", "target_account").value == "招商银行 尾号0099"


def test_card_tools_show_last4_and_irreversible_from_policy():
    view = _view(
        [
            ResolvedStep("s1", "freeze_card", {"card_id": "crd_a"}, "CARD_FREEZE", "GREEN", False),
            ResolvedStep(
                "s2",
                "report_card_lost",
                {"card_id": "crd_a", "replace": True},
                "CARD_LOST",
                "RED",
                False,
            ),
        ],
        decision=DecisionValue.REQUIRE_APPROVAL,
        risk=Risk.RED,
    )
    freeze, lost = view.steps
    assert freeze.title == "冻结卡"
    assert freeze.irreversible is False
    assert _field(view, "s1", "card").value == "尾号1234"
    assert lost.title == "挂失 / 补卡"
    assert lost.irreversible is True
    assert view.otp_required is True


def test_schedule_shows_period_first_run_and_end_date():
    step = ResolvedStep(
        "s1",
        "schedule_transfer",
        {
            "from_account_id": "acc_a",
            "payee_id": "pye_ww",
            "to_account_id": None,
            "amount_cents": 10000,
            "period": "MONTHLY",
            "first_run_at": "2026-11-01T06:00:00+08:00",
            "end_at": "2027-01-01T06:00:00+08:00",
        },
        "SCHEDULE_CREATE",
        "YELLOW",
        False,
    )
    view = _view([step])
    assert _field(view, "s1", "period").value == "MONTHLY"
    assert _field(view, "s1", "first_run_at").value == "2026-11-01T06:00:00+08:00"
    assert _field(view, "s1", "end_at").value == "2027-01-01T06:00:00+08:00"


def test_split_shows_participants():
    step = ResolvedStep(
        "s1",
        "create_split_request",
        {
            "account_id": "acc_a",
            "total_cents": 60000,
            "participant_payee_ids": ["pye_ww", "pye_zl"],
            "include_self": True,
            "memo": None,
        },
        "SPLIT_CREATE",
        "GREEN",
        False,
    )
    view = _view([step])
    participants = [f.value for f in view.steps[0].fields if f.key.startswith("participant:")]
    assert participants == ["王五", "赵六"]
    assert _field(view, "s1", "amount").value == "600.00"


def test_fund_step_collects_disclosure_and_version():
    step = ResolvedStep(
        "s1",
        "subscribe_product",
        {"account_id": "acc_a", "product_id": "prd_a", "amount_cents": 500000},
        "FUND_SUBSCRIBE",
        "RED",
        False,
    )
    view = _view([step], risk=Risk.RED)
    assert _field(view, "s1", "product").value == "稳盈短债"
    assert len(view.disclosures) == 1
    assert view.disclosures[0].product_id == "prd_a"
    assert view.disclosures[0].version == "v1"
    assert view.disclosures[0].text == "合成历史表现，不构成收益承诺"


def test_missing_payee_fails_closed():
    with pytest.raises(ValueError):
        _view([transfer_step(payee_id="pye_missing")])


def test_missing_card_fails_closed():
    with pytest.raises(ValueError):
        _view(
            [
                ResolvedStep(
                    "s1", "freeze_card", {"card_id": "crd_nope"}, "CARD_FREEZE", "GREEN", False
                )
            ]
        )


def test_missing_disclosure_fails_closed():
    step = ResolvedStep(
        "s1",
        "subscribe_product",
        {"account_id": "acc_a", "product_id": "prd_missing", "amount_cents": 100},
        "FUND_SUBSCRIBE",
        "RED",
        False,
    )
    with pytest.raises(ValueError):
        _view([step], risk=Risk.RED)


def test_envelope_changes_do_not_change_hash():
    ctx = make_ctx((transfer_step(),))
    view, view_hash = build_view(ctx)
    assert view.approval_id == UNASSIGNED_APPROVAL_ID
    assert content_hash(view) == view_hash

    # Only the mutable envelope differs: approval/evaluation ID, status, expiry.
    changed = view.model_copy(
        update={
            "approval_id": "apv_real",
            "evaluation_id": "evl_2",
            "status": PlanStatus.CONFIRMED,
            "expires_at_wall": (WALL + timedelta(minutes=4)).isoformat(),
        }
    )
    assert content_hash(changed) == view_hash


def test_each_visible_semantic_change_changes_hash():
    base_ctx = make_ctx((transfer_step(),))
    _, base_hash = build_view(base_ctx)

    # Amount change.
    ctx = make_ctx((transfer_step(amount_cents=80001),))
    assert build_view(ctx)[1] != base_hash

    # Target change.
    ctx = make_ctx((transfer_step(payee_id="pye_zl"),))
    assert build_view(ctx)[1] != base_hash

    # Step order / different step identity.
    ctx = make_ctx((transfer_step(step_id="s2"),))
    assert build_view(ctx)[1] != base_hash

    # Memo change.
    ctx = make_ctx((transfer_step(memo="房租"),))
    assert build_view(ctx)[1] != base_hash

    # Balance change in preflight.
    preflight = PreflightView(
        executable=True,
        balances=(
            {
                "account_id": "acc_a",
                "balance_before_cents": 600000,
                "balance_after_cents": 520000,
                "available_before_cents": 600000,
                "available_after_cents": 520000,
                "daily_used_before_cents": 0,
                "daily_used_after_cents": 80000,
            },
        ),
    )
    ctx = make_ctx((transfer_step(),), preflight=preflight)
    assert build_view(ctx)[1] != base_hash

    # Risk escalation.
    ctx = make_ctx((transfer_step(),), risk=Risk.RED)
    assert build_view(ctx)[1] != base_hash


def test_large_integer_amount_keeps_precision_and_input_unchanged():
    step = transfer_step(amount_cents=9_999_999_999_999_999)
    args_before = dict(step.args)
    view = _view([step])
    assert _field(view, "s1", "amount").value == "99,999,999,999,999.99"
    assert step.args == args_before


def test_otp_requires_policy_or_red_risk():
    yellow = _view([transfer_step()])
    assert yellow.otp_required is False
    red = _view([transfer_step()], risk=Risk.RED)
    assert red.otp_required is True


def test_risk_ack_required_only_off_green():
    assert _view([transfer_step()], risk=Risk.YELLOW).risk_ack_required is True
    green = _view(
        [ResolvedStep("s1", "freeze_card", {"card_id": "crd_a"}, "CARD_FREEZE", "GREEN", False)],
        decision=DecisionValue.ALLOW,
        risk=Risk.GREEN,
    )
    assert green.risk_ack_required is False


def test_expiry_is_capped_by_utterance_expiry():
    ctx = make_ctx((transfer_step(),))
    ctx.authorization = utterance_auth(expires=WALL + timedelta(minutes=3))
    view, _ = build_view(ctx)
    assert view.expires_at_wall == (WALL + timedelta(minutes=3)).isoformat()


def test_missing_authorization_utterance_fails_closed():
    from services.gateway.app.schemas.scope import AuthorizationInputs

    auth = utterance_auth()
    auth = AuthorizationInputs(
        sid="sid_1",
        customer_id="cus_a",
        utterance_ids=("utt_1", "utt_missing"),
        utterances=auth.utterances,
        scope_ids=("utt_1",),
    )
    ctx = make_ctx((transfer_step(),))
    ctx.authorization = auth
    with pytest.raises(ValueError):
        build_view(ctx)


def test_naive_wall_clock_fails_closed():
    ctx = make_ctx((transfer_step(),))
    ctx.wall_now = lambda: WALL.replace(tzinfo=None)
    with pytest.raises(ValueError):
        build_view(ctx)


def test_cancel_scheduled_shows_target_and_missing_fails_closed():
    step = ResolvedStep(
        "s1", "cancel_scheduled", {"instruction_id": "si_t"}, "SCHEDULE_CANCEL", "GREEN", False
    )
    view = _view([step])
    assert _field(view, "s1", "target_display").value == "王五"

    missing = ResolvedStep(
        "s1", "cancel_scheduled", {"instruction_id": "si_nope"}, "SCHEDULE_CANCEL", "GREEN", False
    )
    with pytest.raises(ValueError):
        _view([missing])


def test_cancel_subscription_shows_merchant_and_missing_fails_closed():
    step = ResolvedStep(
        "s1",
        "cancel_subscription",
        {"instruction_id": "si_gym"},
        "MANDATE_CANCEL",
        "YELLOW",
        False,
    )
    view = _view([step])
    assert _field(view, "s1", "merchant").value == "健身"

    missing = ResolvedStep(
        "s1",
        "cancel_subscription",
        {"instruction_id": "si_nope"},
        "MANDATE_CANCEL",
        "YELLOW",
        False,
    )
    with pytest.raises(ValueError):
        _view([missing])


def test_submit_risk_assessment_fails_closed():
    step = ResolvedStep(
        "s1",
        "submit_risk_assessment",
        {"questionnaire_id": "rq_v1", "answers": []},
        "ASSESSMENT_SUBMIT",
        "YELLOW",
        False,
    )
    with pytest.raises(ValueError):
        _view([step])


def test_redeem_units_rendered_as_plain_decimal_string():
    from decimal import Decimal

    for units in ("1.0000", Decimal("1.0000")):
        step = ResolvedStep(
            "s1",
            "redeem_product",
            {"account_id": "acc_a", "product_id": "prd_a", "units": units},
            "FUND_REDEEM",
            "YELLOW",
            False,
        )
        assert _field(_view([step]), "s1", "units").value == "1.0000"
