"""Stage ⑦ cross-session workflow (PRD §7.8).

Success sequences come from committed L3 ``operation_events`` (authoritative)
plus gateway ``workflow_events`` with status ``COMMITTED`` as a de-duplicated
cache. The current plan's proposed operations are appended in memory so that
"含本计划" windows (SPLIT_TRANSFER / CUMULATIVE_OUTFLOW / RISKY_SEQUENCE) see
them. Business windows and the 24h cooldown use ``sim_now``; the anti-retry
windows (DUPLICATE_PLAN, REJECTED_RETRY) use ``wall_now``.

A RISKY_SEQUENCE hit persists a 24h cooldown for the target fingerprint unless
this is the SHADOW_B1 in-memory comparison (which never writes business data,
audit or approvals).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from safebank_bankcore.ids import derive
from safebank_bankcore.references import request_digest
from safebank_contracts.codes import Code
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .. import tables as t
from ..schemas.enums import FindingEffect
from ..workflow.sequence import (
    CommittedOp,
    merge_committed,
    risky_sequence,
    split_transfer,
    window_ops,
)
from .context import EvalContext, EvalMode, Stage, WorkflowResult

_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / "policies" / "workflow.yaml"
_THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "policies" / "thresholds.yaml"


@dataclass
class WorkflowHistory:
    """Everything the workflow stage needs from the gateway database."""

    committed: tuple[CommittedOp, ...] = ()
    active_cooldowns: dict[str, datetime] = field(default_factory=dict)
    duplicate: bool = False
    rejected: tuple[tuple[tuple[str, str], ...], ...] = ()


def _load_workflow_yaml() -> dict[str, int]:
    return {str(k): int(v) for k, v in yaml.safe_load(_WORKFLOW_PATH.read_text()).items()}


def _load_thresholds() -> dict[str, int]:
    return {str(k): int(v) for k, v in yaml.safe_load(_THRESHOLDS_PATH.read_text()).items()}


def _step_signature(tool: str, args: dict) -> tuple[str, str]:
    """Deterministic (action-ish, target) signature for REJECTED_RETRY matching.

    Only transfer direction and primary entity identity are used; card LOOSEN vs
    TIGHTEN direction is not reconstructed from persisted steps (see progress
    notes) and falls back to the tool name for card tools.
    """
    if tool in ("transfer", "schedule_transfer"):
        if args.get("to_account_id"):
            kind = "TRANSFER_SELF" if tool == "transfer" else "SCHEDULE_CREATE"
            return (kind, "int:" + str(args["to_account_id"]))
        return (
            "TRANSFER_EXTERNAL" if tool == "transfer" else "SCHEDULE_CREATE",
            "payee:" + str(args["payee_id"]),
        )
    if tool == "add_payee":
        return ("PAYEE_ADD", "number:" + str(args["account_no"]))
    if tool in (
        "update_card_limits",
        "update_card_controls",
        "freeze_card",
        "unfreeze_card",
        "report_card_lost",
    ):
        return (tool, "card:" + str(args["card_id"]))
    if tool in ("cancel_subscription", "cancel_scheduled"):
        return (tool, "instruction:" + str(args["instruction_id"]))
    if tool in ("subscribe_product", "redeem_product"):
        return (tool, "product:" + str(args["product_id"]))
    if tool == "create_split_request":
        return (tool, "account:" + str(args["account_id"]))
    if tool == "submit_risk_assessment":
        return (tool, str(args["questionnaire_id"]))
    return (tool, "")


async def _load_history(ctx: EvalContext) -> WorkflowHistory:
    conn = ctx.conn
    state = await ctx.bank_state()
    sim_now = state.sim_now
    wall_now = ctx.wall_now()
    if conn is None:
        return WorkflowHistory()
    cfg = _load_workflow_yaml()

    wf_rows = (
        (
            await conn.execute(
                select(t.workflow_events).where(
                    t.workflow_events.c.customer_id == ctx.customer_id,
                    t.workflow_events.c.occurred_at >= sim_now - timedelta(days=7),
                )
            )
        )
        .mappings()
        .all()
    )
    committed = merge_committed(state.ops_7d, tuple(dict(row) for row in wf_rows))

    cooldown_rows = (
        (
            await conn.execute(
                select(t.cooldowns).where(t.cooldowns.c.customer_id == ctx.customer_id)
            )
        )
        .mappings()
        .all()
    )
    active_cooldowns = {
        row["payee_fingerprint"]: row["until_at"]
        for row in cooldown_rows
        if row["until_at"] > sim_now
    }

    steps_hash = request_digest(ctx.plan_draft.steps)
    duplicate_row = (
        await conn.execute(
            select(t.plans.c.id)
            .join(t.execution_jobs, t.execution_jobs.c.plan_id == t.plans.c.id)
            .where(
                t.plans.c.customer_id == ctx.customer_id,
                t.plans.c.steps_hash == steps_hash,
                t.plans.c.status == "EXECUTED",
                t.execution_jobs.c.status == "EXECUTED",
                t.plans.c.id != ctx.plan_id,
                t.execution_jobs.c.updated_at_wall
                >= wall_now - timedelta(minutes=cfg["duplicate_plan_wall_minutes"]),
                t.execution_jobs.c.updated_at_wall <= wall_now,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    rejected_rows = (
        (
            await conn.execute(
                select(t.plans).where(
                    t.plans.c.customer_id == ctx.customer_id,
                    t.plans.c.status.in_(["DENIED", "REJECTED"]),
                    t.plans.c.created_at_wall
                    >= wall_now - timedelta(minutes=cfg["rejected_retry_wall_minutes"]),
                    t.plans.c.id != ctx.plan_id,
                )
            )
        )
        .mappings()
        .all()
    )
    rejected = tuple(
        tuple(
            _step_signature(step["tool"], step["args"])
            for step in (row["resolved_steps"] or [])
            if isinstance(step, dict)
        )
        for row in rejected_rows
    )
    return WorkflowHistory(
        committed=committed,
        active_cooldowns=active_cooldowns,
        duplicate=duplicate_row is not None,
        rejected=rejected,
    )


async def _write_cooldown(ctx: EvalContext, fingerprint: str, until_at: datetime) -> None:
    if ctx.mode is EvalMode.SHADOW_B1 or ctx.conn is None:
        return
    await ctx.conn.execute(
        pg_insert(t.cooldowns)
        .values(
            customer_id=ctx.customer_id,
            payee_fingerprint=fingerprint,
            until_at=until_at,
            reason=Code.RISKY_SEQUENCE.value,
        )
        .on_conflict_do_update(
            index_elements=[t.cooldowns.c.customer_id, t.cooldowns.c.payee_fingerprint],
            set_={"until_at": until_at, "reason": Code.RISKY_SEQUENCE.value},
        )
    )


class WorkflowStage(Stage):
    name = "workflow"

    def __init__(
        self, *, load_history=None, write_cooldown=None, thresholds: dict[str, int] | None = None
    ) -> None:
        self.load_history = load_history or _load_history
        self.write_cooldown = write_cooldown or _write_cooldown
        self._cfg = _load_workflow_yaml()
        self._thresholds = _load_thresholds() if thresholds is None else thresholds

    async def run(self, ctx: EvalContext) -> None:
        ctx.workflow = WorkflowResult()
        if ctx.plan_draft is None or ctx.has_hard_deny():
            return
        state = await ctx.bank_state()
        sim_now = state.sim_now
        history = await self.load_history(ctx)
        fingerprints = self._fingerprints(state, ctx)
        current = self._current_ops(ctx, state, fingerprints, sim_now)
        all_ops = tuple(sorted((*history.committed, *current), key=CommittedOp.sort_key))
        window_start = sim_now - timedelta(hours=self._cfg["risky_sequence_window_hours"])

        await self._outflow_rules(ctx, state, all_ops, window_start, sim_now, history, fingerprints)
        self._retry_rules(ctx, history)

    # ---- proposed operations of the current plan ---------------------------
    @staticmethod
    def _fingerprints(state, ctx: EvalContext) -> dict[str, str]:
        result = {
            p.id: p.target_fingerprint
            for p in state.payees.values()
            if p.customer_id == ctx.customer_id
        }
        for step in ctx.steps:
            if step.tool == "add_payee":
                entry = state.directory.get("number:" + step.args["account_no"])
                if entry is not None:
                    result[derive(ctx.plan_id, step.step_id, "pye")] = entry.target_fingerprint
        return result

    @staticmethod
    def _current_ops(ctx, state, fingerprints, sim_now) -> tuple[CommittedOp, ...]:
        ops = []
        for step in ctx.steps:
            args = step.args
            target = None
            if step.tool in ("transfer", "schedule_transfer"):
                if args.get("to_account_id"):
                    target = "int:" + args["to_account_id"]
                elif args.get("payee_id") in fingerprints:
                    target = fingerprints[args["payee_id"]]
            elif step.tool == "add_payee":
                entry = state.directory.get("number:" + args["account_no"])
                target = entry.target_fingerprint if entry else None
            ops.append(
                CommittedOp(
                    event_id=f"plan:{ctx.plan_id}:{step.step_id}",
                    customer_id=ctx.customer_id,
                    action_kind=step.action_kind,
                    target_fingerprint=target,
                    amount_cents=int(args.get("amount_cents", 0)),
                    occurred_at=sim_now,
                )
            )
        return tuple(ops)

    # ---- §7.8 business-window rules ---------------------------------------
    async def _outflow_rules(
        self, ctx, state, all_ops, window_start, sim_now, history, fingerprints
    ):
        window = window_ops(all_ops, customer_id=ctx.customer_id, start=window_start, end=sim_now)
        for step in ctx.steps:
            if not step.outflow:
                continue
            target = fingerprints.get(step.args.get("payee_id"))
            if target is None:
                continue
            until = history.active_cooldowns.get(target)
            if until is not None and until > sim_now:
                ctx.add(
                    Code.RISKY_SEQUENCE,
                    FindingEffect.DENY,
                    step_id=step.step_id,
                    message="该收款人处于 24h 冷静期",
                    evidence_refs=(target,),
                )
                continue
            if risky_sequence(window, target_fingerprint=target):
                ctx.add(
                    Code.RISKY_SEQUENCE,
                    FindingEffect.DENY,
                    step_id=step.step_id,
                    message="新增收款人后 24h 内放松卡限制并对外转账",
                    evidence_refs=(target,),
                )
                await self.write_cooldown(
                    ctx,
                    target,
                    sim_now + timedelta(hours=self._cfg["risky_sequence_cooldown_hours"]),
                )
                continue
            count, total = split_transfer(window, target_fingerprint=target)
            if (
                count >= self._thresholds["split_transfer_min_count"]
                and total > self._thresholds["split_transfer_min_total_cents"]
            ):
                ctx.add(
                    Code.SPLIT_TRANSFER,
                    FindingEffect.RISK_RED,
                    step_id=step.step_id,
                    message="24h 内向同一目标多笔转账拆分",
                    evidence_refs=(target,),
                )
            if not self._transferred_before(state, target):
                ctx.add(
                    Code.FIRST_TRANSFER_TO_PAYEE,
                    FindingEffect.RISK_YELLOW,
                    step_id=step.step_id,
                    message="首次向该收款人转账",
                    evidence_refs=(target,),
                )
            if self._new_payee(state, step, sim_now):
                ctx.add(
                    Code.NEW_PAYEE_FIRST_TRANSFER,
                    FindingEffect.RISK_RED,
                    step_id=step.step_id,
                    message="新收款人创建后 30 分钟内转账",
                    evidence_refs=(target,),
                )

        committed_rolling = state.counters.rolling_outflow_cents.get(ctx.customer_id, 0)
        plan_outflow = sum(int(s.args["amount_cents"]) for s in ctx.steps if s.outflow)
        if committed_rolling + plan_outflow > self._thresholds["cumulative_outflow_24h_cents"]:
            ctx.add(
                Code.CUMULATIVE_OUTFLOW,
                FindingEffect.RISK_RED,
                message="24h 对外转出超过红线",
            )

    # ---- anti-retry wall-clock rules --------------------------------------
    def _retry_rules(self, ctx: EvalContext, history: WorkflowHistory) -> None:
        if history.duplicate:
            ctx.add(Code.DUPLICATE_PLAN, FindingEffect.RISK_YELLOW, message="疑似重复计划")
        current = {
            _step_signature(s.tool, s.args.model_dump(mode="json")) for s in ctx.plan_draft.steps
        }
        if any(set(signatures) & current for signatures in history.rejected):
            ctx.add(
                Code.REJECTED_RETRY, FindingEffect.RISK_RED, message="近期被拒绝或 DENY 的计划重试"
            )

    # ---- history helpers ---------------------------------------------------
    @staticmethod
    def _transferred_before(state, fingerprint: str) -> bool:
        payees = {p.id: p.target_fingerprint for p in state.payees.values()}
        for tx in state.transactions:
            if tx.direction == "OUT" and payees.get(tx.payee_id) == fingerprint:
                return True
        return False

    def _new_payee(self, state, step, sim_now: datetime) -> bool:
        payee = state.payees.get(step.args.get("payee_id"))
        if payee is None:
            # A payee created earlier in this same plan has created_at == sim_now.
            return True
        return sim_now - payee.created_at <= timedelta(
            minutes=self._thresholds["new_payee_first_transfer_minutes"]
        )


stage = WorkflowStage()
