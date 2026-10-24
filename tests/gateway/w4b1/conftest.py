"""Shared in-memory fixtures for W4b1 plan-view / binding unit tests (no DB).

Builds a small ``BankState`` and an ``EvalContext`` whose pipeline slots are
already populated, so ``build_view`` can be exercised directly without running
L1/L2/L3 or the pipeline stages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from safebank_contracts.enums import Risk
from safebank_contracts.state import Account, BankState, Card, Customer, Payee
from safebank_contracts.views import (
    ChannelFlags,
    ProductView,
    ScheduledView,
    SubscriptionView,
)

from services.gateway.app.approval import service as approval_service
from services.gateway.app.pipeline.context import EvalContext, ResolvedStep
from services.gateway.app.schemas.decision import Decision, DecisionValue, PreflightView
from services.gateway.app.schemas.enums import ParseStatus, UtteranceStatus
from services.gateway.app.schemas.scope import (
    AuthorizationInputs,
    NormalizedScope,
    UtteranceRef,
)

WALL = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
HMAC_KEY = b"w4b1-approval-hmac-key-at-least-32-bytes"

PROFILES = frozenset({"B0", "B1", "B2", "B2_NO_PREFLIGHT", "B2_NO_WORKFLOW", "B2_NO_TAINT"})


@pytest.fixture
def approval_config():
    saved = approval_service._config
    approval_service._config = None
    yield
    approval_service._config = saved


def configure_approval(profile="B2", wall=None):
    approval_service.configure(
        HMAC_KEY, profile=profile, wall_now=wall if wall is not None else (lambda: WALL)
    )


def state() -> BankState:
    return BankState(
        sim_now=WALL,
        clock_version=1,
        versions={"cus_a": 1},
        accounts={
            "acc_a": Account(
                id="acc_a",
                customer_id="cus_a",
                type="CHECKING",
                display_name="活期",
                account_no="6222020000000001",
                current_card_id="crd_a",
                credits_posted_cents=600000,
            ),
            "acc_s": Account(
                id="acc_s",
                customer_id="cus_a",
                type="SAVINGS",
                display_name="储蓄",
                account_no="6222020000000002",
                credits_posted_cents=2000000,
            ),
        },
        customers={"cus_a": Customer(id="cus_a", display_name="张三", phone="13800000000")},
        cards={
            "crd_a": Card(
                id="crd_a",
                account_id="acc_a",
                last4="1234",
                status="ACTIVE",
                single_limit_cents=500000,
                daily_limit_cents=2000000,
                controls=ChannelFlags(online=True, overseas=False, atm=True, quickpass=True),
                created_at=WALL,
            )
        },
        payees={
            "pye_ww": Payee(
                id="pye_ww",
                customer_id="cus_a",
                name="王五",
                alias=None,
                phone=None,
                bank_name="本行",
                account_no="6222020000000003",
                target_account_id="acc_ww",
                target_fingerprint="fp_ww",
                created_at=WALL,
            ),
            "pye_zl": Payee(
                id="pye_zl",
                customer_id="cus_a",
                name="赵六",
                alias=None,
                phone=None,
                bank_name="招商银行",
                account_no="6222020000000099",
                target_account_id=None,
                target_fingerprint="fp_zl",
                created_at=WALL,
            ),
        },
        products=(
            ProductView(
                id="prd_a",
                name="稳盈短债",
                product_type="BOND",
                risk_level=2,
                risk_label="稳健",
                nav="1.2311",
                nav_date=WALL.date(),
                recent_return_bp=10,
                confirm_days=1,
                settle_days=1,
                min_amount_cents=100,
                risk_disclosure="合成历史表现，不构成收益承诺",
                disclosure_version="v1",
                nav_history=(),
            ),
        ),
        instructions=(
            ScheduledView(
                id="si_t",
                from_account_id="acc_a",
                payee_id="pye_ww",
                to_account_id=None,
                target_display="王五",
                amount_cents=10000,
                period="MONTHLY",
                anchor_day=20,
                weekday=None,
                time_of_day="06:00",
                next_run_at=WALL + timedelta(days=5),
                end_at=None,
                status="ACTIVE",
                last_run=None,
            ),
        ),
        subscriptions=(
            SubscriptionView(
                instruction_id="si_gym",
                billing_id="bil_gym",
                account_id="acc_a",
                merchant_id="mer_gym",
                merchant_name="健身",
                period="MONTHLY",
                amount_cents=19900,
                max_amount_cents=19900,
                next_due_at=WALL,
                status="ACTIVE",
                annual_cost_cents=238800,
            ),
        ),
    )


def _preflight_view(executable: bool = True, balances: tuple[dict, ...] = ()) -> PreflightView:
    return PreflightView(
        executable=executable,
        balances=tuple(balances),
    )


def make_ctx(
    steps: tuple[ResolvedStep, ...],
    *,
    decision: DecisionValue = DecisionValue.REQUIRE_APPROVAL,
    risk: Risk = Risk.YELLOW,
    preflight: PreflightView | None = None,
    snapshot: BankState | None = None,
    authorization: AuthorizationInputs | None = None,
    findings: tuple[dict, ...] = (),
) -> EvalContext:
    ctx = EvalContext(
        plan_id="pln_1",
        session_id="sid_1",
        origin="agent",
        customer_id="cus_a",
    )
    ctx.steps = steps
    ctx.snapshot = snapshot if snapshot is not None else state()
    ctx.decision = Decision(
        plan_id="pln_1",
        evaluation_id="evl_1",
        revision=1,
        decision=decision,
        risk=risk,
        findings=tuple(findings),
        preflight=preflight if preflight is not None else _preflight_view(),
        versions={"cus_a": 1},
        clock_version=1,
        policy_version="sha256:test",
    )
    ctx.wall_now = lambda: WALL
    if authorization is not None:
        ctx.authorization = authorization
    return ctx


def utterance_auth(*, expires: datetime | None = None) -> AuthorizationInputs:
    expiry = expires if expires is not None else WALL + timedelta(minutes=10)
    utterance = UtteranceRef(
        id="utt_1",
        kind="typed",
        text="转账",
        scope=NormalizedScope(action_kinds=("TRANSFER_EXTERNAL",)),
        parse_status=ParseStatus.SCOPED,
        created_at_wall=WALL.isoformat(),
        expires_at_wall=expiry.isoformat(),
    )
    return AuthorizationInputs(
        sid="sid_1",
        customer_id="cus_a",
        utterance_ids=("utt_1",),
        utterances={"utt_1": utterance},
        scope_ids=("utt_1",),
        statuses={"utt_1": UtteranceStatus.ACTIVE},
    )


def transfer_step(step_id="s1", payee_id="pye_ww", amount_cents=80000, memo=None) -> ResolvedStep:
    return ResolvedStep(
        step_id,
        "transfer",
        {
            "from_account_id": "acc_a",
            "payee_id": payee_id,
            "to_account_id": None,
            "amount_cents": amount_cents,
            "memo": memo,
        },
        "TRANSFER_EXTERNAL",
        "YELLOW",
        True,
    )
