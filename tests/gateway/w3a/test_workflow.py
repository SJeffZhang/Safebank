"""Stage ⑦ workflow: sequences, cooldowns, retry windows, history boundaries."""

import asyncio
from datetime import timedelta

from safebank_contracts.codes import Code

from services.gateway.app.pipeline.workflow import WorkflowHistory, WorkflowStage
from services.gateway.app.schemas.enums import FindingEffect
from services.gateway.app.workflow.sequence import CommittedOp, merge_committed
from tests.bankcore.fixtures import NOW
from tests.gateway.w3a.conftest import build_ctx, run_entity, transfer, w3_state

SIM = NOW  # 2026-10-15T09:00+08


def run(ctx, history=None, writes=None):
    recorded = {}

    async def load(ctx):
        return history or WorkflowHistory()

    async def write(ctx, fingerprint, until_at):
        recorded.setdefault("cooldowns", []).append((fingerprint, until_at))

    stage = WorkflowStage(load_history=load, write_cooldown=writes or write)

    async def execute():
        await stage.run(ctx)

    asyncio.run(execute())
    return ctx, recorded


def committed(**kw):
    defaults = dict(
        event_id="evt_1",
        customer_id="cus_a",
        action_kind="TRANSFER_EXTERNAL",
        target_fingerprint="pye_extfp",
        amount_cents=0,
        occurred_at=SIM - timedelta(hours=1),
    )
    defaults.update(kw)
    return CommittedOp(**defaults)


def test_a1_split_transfer_escalates_red():
    history = WorkflowHistory(
        committed=(
            committed(event_id="e1", amount_cents=200000, occurred_at=SIM - timedelta(hours=2)),
            committed(event_id="e2", amount_cents=200000, occurred_at=SIM - timedelta(hours=1)),
        )
    )
    ctx = run_entity(build_ctx([transfer("pye_ext", 200000)]))
    ctx, _ = run(ctx, history)
    assert any(
        f.code is Code.SPLIT_TRANSFER and f.effect is FindingEffect.RISK_RED for f in ctx.findings
    )


def test_a3_risky_sequence_deny_with_cooldown():
    history = WorkflowHistory(
        committed=(
            committed(
                event_id="e1",
                action_kind="PAYEE_ADD",
                target_fingerprint="pye_extfp",
                occurred_at=SIM - timedelta(hours=2),
            ),
            committed(
                event_id="e2",
                action_kind="CARD_LIMIT_LOOSEN",
                target_fingerprint=None,
                occurred_at=SIM - timedelta(hours=1),
            ),
        )
    )
    ctx = run_entity(build_ctx([transfer("pye_ext", 100000)]))
    ctx, recorded = run(ctx, history)
    deny = [f for f in ctx.findings if f.code is Code.RISKY_SEQUENCE]
    assert deny and all(f.effect is FindingEffect.DENY for f in deny)
    assert ("pye_extfp", SIM + timedelta(hours=24)) in recorded["cooldowns"]


def test_a3_order_swap_loosen_before_payee_add():
    history = WorkflowHistory(
        committed=(
            committed(
                event_id="e1",
                action_kind="CARD_LIMIT_LOOSEN",
                target_fingerprint=None,
                occurred_at=SIM - timedelta(hours=2),
            ),
            committed(
                event_id="e2",
                action_kind="PAYEE_ADD",
                target_fingerprint="pye_extfp",
                occurred_at=SIM - timedelta(hours=1),
            ),
        )
    )
    ctx = run_entity(build_ctx([transfer("pye_ext", 100000)]))
    ctx, _ = run(ctx, history)
    assert any(f.code is Code.RISKY_SEQUENCE for f in ctx.findings)


def test_a3_no_card_savings_variant_still_denied():
    # Transferring from the cardless savings account must not dodge the rule.
    history = WorkflowHistory(
        committed=(
            committed(
                event_id="e1",
                action_kind="PAYEE_ADD",
                target_fingerprint="pye_extfp",
                occurred_at=SIM - timedelta(hours=2),
            ),
            committed(
                event_id="e2",
                action_kind="CARD_CONTROL_LOOSEN",
                target_fingerprint=None,
                occurred_at=SIM - timedelta(hours=1),
            ),
        )
    )
    from tests.contracts.samples import step as raw_step

    save = raw_step("transfer", from_account_id="acc_s", payee_id="pye_ext", amount_cents=100000)
    ctx = run_entity(build_ctx([save]))
    ctx, _ = run(ctx, history)
    assert any(f.code is Code.RISKY_SEQUENCE for f in ctx.findings)


def test_cooldown_denies_transfer_during_window():
    history = WorkflowHistory(active_cooldowns={"pye_extfp": SIM + timedelta(hours=1)})
    ctx = run_entity(build_ctx([transfer("pye_ext", 100000)]))
    ctx, _ = run(ctx, history)
    deny = [f for f in ctx.findings if f.code is Code.RISKY_SEQUENCE]
    assert deny and all(f.effect is FindingEffect.DENY for f in deny)


def test_duplicate_plan_and_rejected_retry_wall_windows():
    history = WorkflowHistory(duplicate=True)
    ctx = run_entity(build_ctx([transfer("pye_ext", 100000)]))
    ctx, _ = run(ctx, history)
    assert any(
        f.code is Code.DUPLICATE_PLAN and f.effect is FindingEffect.RISK_YELLOW
        for f in ctx.findings
    )

    history = WorkflowHistory(rejected=((("TRANSFER_EXTERNAL", "payee:pye_ext"),),))
    ctx = run_entity(build_ctx([transfer("pye_ext", 100000)]))
    ctx, _ = run(ctx, history)
    assert any(
        f.code is Code.REJECTED_RETRY and f.effect is FindingEffect.RISK_RED for f in ctx.findings
    )


def test_first_transfer_to_existing_payee_escalates_yellow():
    # pye_ext has no prior committed OUT transaction in the fixture.
    ctx = run_entity(build_ctx([transfer("pye_ext", 100000)]))
    ctx, _ = run(ctx)
    assert any(
        f.code is Code.FIRST_TRANSFER_TO_PAYEE and f.effect is FindingEffect.RISK_YELLOW
        for f in ctx.findings
    )


def test_new_payee_within_30_minutes_escalates_red():
    from safebank_bankcore.ledger import replace_state
    from safebank_contracts.state import Payee

    state = w3_state()
    recent = Payee(
        id="pye_recent",
        customer_id="cus_a",
        name="近期收款人",
        alias=None,
        phone=None,
        bank_name="他行",
        account_no="6222020000000088",
        target_account_id=None,
        target_fingerprint="recentfp",
        created_at=SIM - timedelta(minutes=20),
    )
    state = replace_state(state, payees=dict(state.payees) | {"pye_recent": recent})
    ctx = run_entity(build_ctx([transfer("pye_recent", 100000)], state=state))
    ctx, _ = run(ctx)
    assert any(
        f.code is Code.NEW_PAYEE_FIRST_TRANSFER and f.effect is FindingEffect.RISK_RED
        for f in ctx.findings
    )


def test_merge_dedups_same_event_id_and_ignores_non_committed():
    l3 = (committed(event_id="e1", amount_cents=100000),)
    gateway = (
        dict(
            event_id="e1",
            customer_id="cus_a",
            action_kind="TRANSFER_EXTERNAL",
            target_id="pye_extfp",
            amount_cents=100000,
            occurred_at=SIM,
            status="COMMITTED",
        ),
        dict(
            event_id="e2",
            customer_id="cus_a",
            action_kind="TRANSFER_EXTERNAL",
            target_id="pye_extfp",
            amount_cents=1,
            occurred_at=SIM,
            status="BLOCKED",
        ),
        dict(
            event_id="e3",
            customer_id="cus_b",
            action_kind="TRANSFER_EXTERNAL",
            target_id="pye_extfp",
            amount_cents=1,
            occurred_at=SIM,
            status="COMMITTED",
        ),
    )
    merged = merge_committed(l3, gateway)
    # e1 dedups (L3 wins), e2 BLOCKED dropped; e3 is a different customer and is
    # dropped by window_ops / customer filtering in the stage, not here.
    ids = {op.event_id for op in merged}
    assert "e1" in ids and "e2" not in ids


def test_future_ops_are_outside_window():
    history = WorkflowHistory(
        committed=(committed(event_id="e1", occurred_at=SIM + timedelta(hours=1)),)
    )
    ctx = run_entity(build_ctx([transfer("pye_ext", 200000)]))
    ctx, _ = run(ctx, history)
    # A future op must not contribute to a 24h split window.
    assert not any(f.code is Code.SPLIT_TRANSFER for f in ctx.findings)
