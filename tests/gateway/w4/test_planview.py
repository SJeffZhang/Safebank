from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from services.gateway.app.approval.planview import build_view, content_hash
from services.gateway.app.pipeline.context import EvalContext, ResolvedStep
from services.gateway.app.schemas.decision import BalanceView, Decision, PreflightView

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def context():
    return EvalContext(
        plan_id="pln_one",
        session_id="ses_one",
        origin="agent",
        wall_now=lambda: NOW,
        steps=(
            ResolvedStep(
                "s1",
                "transfer",
                {
                    "from_account_id": "acc_one",
                    "payee_id": "pay_one",
                    "amount_cents": 100,
                    "memo": "午饭",
                    "to_account_id": None,
                },
                "TRANSFER_EXTERNAL",
                "YELLOW",
                True,
            ),
        ),
        decision=Decision(
            plan_id="pln_one",
            evaluation_id="evl_one",
            revision=1,
            decision="REQUIRE_APPROVAL",
            risk="YELLOW",
            preflight=PreflightView(
                executable=True,
                balances=(
                    BalanceView(
                        account_id="acc_one",
                        balance_before_cents=500,
                        balance_after_cents=400,
                        available_before_cents=500,
                        available_after_cents=400,
                        daily_used_before_cents=0,
                        daily_used_after_cents=100,
                    ),
                ),
            ),
        ),
    )


def test_equivalence_excludes_wall_countdown_and_new_evaluation_identity():
    ctx = context()
    view, digest = build_view(ctx)
    ctx.wall_now = lambda: NOW + timedelta(minutes=1)
    ctx.decision = ctx.decision.model_copy(update={"evaluation_id": "evl_two", "revision": 2})
    other, actual = build_view(ctx)
    assert view.expires_at_wall != other.expires_at_wall
    assert actual == digest == content_hash(view)
    assert any(f.value == "午饭" for f in view.steps[0].fields)


@pytest.mark.parametrize(
    "field,value", [("amount_cents", 101), ("memo", "晚饭"), ("payee_id", "pay_other")]
)
def test_each_sensitive_value_changes_hash(field, value):
    ctx = context()
    _, before = build_view(ctx)
    step = ctx.steps[0]
    ctx.steps = (replace(step, args={**step.args, field: value}),)
    assert build_view(ctx)[1] != before


def test_risk_and_balance_changes_require_new_confirmation():
    ctx = context()
    _, before = build_view(ctx)
    ctx.decision = ctx.decision.model_copy(update={"risk": "RED"})
    view, after = build_view(ctx)
    assert view.otp_required and before != after
    ctx = context()
    ctx.decision = ctx.decision.model_copy(update={"preflight": PreflightView(executable=False)})
    assert build_view(ctx)[1] != before


def test_incomplete_context_fails():
    with pytest.raises(ValueError):
        build_view(EvalContext())


def test_new_payee_accounts_sharing_last_four_still_change_hash():
    ctx = context()
    ctx.steps = (
        ResolvedStep(
            "s1",
            "add_payee",
            {
                "account_no": "1111222233334444",
                "name": "合成人",
                "alias": "家人",
                "bank_name": "外行",
            },
            "PAYEE_ADD",
            "YELLOW",
            False,
        ),
    )
    view, before = build_view(ctx)
    assert any(f.value == "1111222233334444" for f in view.steps[0].fields)
    ctx.steps = (
        replace(ctx.steps[0], args={**ctx.steps[0].args, "account_no": "9999222233334444"}),
    )
    assert build_view(ctx)[1] != before


def test_missing_product_disclosure_fails_closed():
    ctx = context()
    ctx.steps = (
        ResolvedStep(
            "s1",
            "subscribe_product",
            {"product_id": "prd_a", "account_id": "acc_a", "amount_cents": 100},
            "FUND_SUBSCRIBE",
            "RED",
            False,
        ),
    )
    with pytest.raises(ValueError, match="disclosure"):
        build_view(ctx)
