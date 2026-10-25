"""Stage ⑧ decision aggregation (PRD §7.9, §9.5, §10.1).

Priority ``DENY > REPLAN > REQUIRE_APPROVAL > ALLOW``; risk only ever rises from
the step base risks plus RISK_YELLOW / RISK_RED findings. Replan hints are only
shrink-only (reduce amount, never a new action, target, source or a LOOSEN) and
only when every user finding is a shrinkable business failure and no taint /
unknown-source / scope / injection finding is present.

The B2 profile additionally computes the in-memory ``shadow_b1`` (a B1-profile
re-run against the same snapshot) for the judge view; the shadow run performs
no database writes, cooldowns, audit or approvals.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from safebank_bankcore.ids import derive
from safebank_contracts.codes import Code
from safebank_contracts.enums import Risk
from safebank_contracts.state import BankState

from ..schemas.decision import Decision, DecisionValue, PreflightView, ReplanHint
from ..schemas.enums import FindingEffect, ParseStatus, UtteranceStatus
from .context import EvalContext, EvalMode, Stage

_POLICY_FILES = (
    Path(__file__).resolve().parents[2] / "policies" / "thresholds.yaml",
    Path(__file__).resolve().parents[2] / "policies" / "workflow.yaml",
    Path(__file__).resolve().parents[2] / "policies" / "tools.yaml",
)

_RISK_ORDER = {"GREEN": 0, "YELLOW": 1, "RED": 2}

# The only findings for which a shrink-only replan hint is allowed (PRD §7.9).
_SHRINKABLE = frozenset(
    {
        Code.INSUFFICIENT_FUNDS,
        Code.CARD_SINGLE_LIMIT,
        Code.CARD_DAILY_LIMIT,
        Code.SCHEDULED_SHORTFALL,
        Code.HOLDING_INSUFFICIENT,
        Code.MIN_PURCHASE_NOT_MET,
    }
)

# Their presence suppresses replan hints entirely (PRD §7.9).
_HINT_FORBIDDEN = frozenset(
    {
        Code.TAINTED_SENSITIVE_ARG,
        Code.UNKNOWN_SENSITIVE_ARG,
        Code.INTENT_SCOPE_EXCEEDED,
        Code.INJECTION_SUSPECTED,
    }
)


def _policy_version() -> str:
    digest = hashlib.sha256()
    for path in _POLICY_FILES:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _format_cents(cents: int) -> str:
    # Integer arithmetic only (§0.1: 金额禁止 float); negative guards keep the
    # display helper honest even if a caller passes a non-positive sentinel.
    if cents < 0:
        return f"-{_format_cents(-cents)}"
    return f"{cents // 100:,}.{cents % 100:02d} 元"


class DecisionStage(Stage):
    name = "decision"
    always_run = True

    async def run(self, ctx: EvalContext) -> None:
        if ctx.plan_draft is None:
            return
        # The snapshot may be absent when stage ⑤ was skipped (profile ablation)
        # or short-circuited by an earlier hard DENY. A DENY Decision must never
        # depend on L3 being reachable just to assemble display fields, so we
        # never re-fetch here: use the cached snapshot or empty wire defaults.
        state = ctx.snapshot
        decision, risk = self._aggregate(ctx)
        hints = await self._hints(ctx, decision, state)
        preflight = self._preflight_view(ctx)
        shadow_b1 = (
            await self._shadow_b1(ctx, state)
            if ctx.profile == "B2" and ctx.mode is EvalMode.EVALUATE and state is not None
            else None
        )

        ctx.decision = Decision(
            plan_id=ctx.plan_id,
            evaluation_id=f"evl_{ctx.plan_id}",
            revision=1,
            decision=decision,
            risk=risk,
            findings=tuple(ctx.findings),
            replan_hints=hints,
            preflight=preflight,
            versions=dict(state.versions) if state is not None else {},
            clock_version=state.clock_version if state is not None else 0,
            policy_version=_policy_version(),
            shadow_b1=shadow_b1,
        )

    # ---- aggregation -------------------------------------------------------
    def _aggregate(self, ctx: EvalContext) -> tuple[DecisionValue, Risk]:
        findings = ctx.findings
        base = max((_RISK_ORDER.get(s.base_risk, 0) for s in ctx.steps), default=0)
        risk_order = base
        for finding in findings:
            if finding.effect is FindingEffect.RISK_YELLOW:
                risk_order = max(risk_order, _RISK_ORDER["YELLOW"])
            elif finding.effect is FindingEffect.RISK_RED:
                risk_order = max(risk_order, _RISK_ORDER["RED"])
        risk = ("GREEN", "YELLOW", "RED")[risk_order]
        if any(f.effect is FindingEffect.DENY for f in findings):
            return DecisionValue.DENY, Risk(risk)
        if any(f.effect is FindingEffect.REPLAN for f in findings):
            return DecisionValue.REPLAN, Risk(risk)
        if risk == "GREEN":
            return DecisionValue.ALLOW, Risk.GREEN
        return DecisionValue.REQUIRE_APPROVAL, Risk(risk)

    # ---- preflight view ----------------------------------------------------
    @staticmethod
    def _preflight_view(ctx: EvalContext) -> PreflightView:
        pre = ctx.preflight
        if pre is None:
            # Stage ⑥ was skipped (profile ablation or an earlier hard DENY).
            # A rejected plan must not report a fabricated "executable: true".
            return PreflightView(executable=not ctx.has_hard_deny())
        steps = tuple(
            {
                "step_id": s["step_id"],
                "status": s["status"],
                "effects": s["effects"],
                "violations": s["violations"],
            }
            for s in pre.steps
        )
        return PreflightView(
            executable=pre.executable,
            steps=steps,
            balances=tuple(pre.balances),
            holdings=tuple(pre.holdings),
            scheduled_reserve_cents=pre.scheduled_reserve_cents,
        )

    # ---- replan hints ------------------------------------------------------
    async def _hints(self, ctx: EvalContext, decision: DecisionValue, state: BankState | None):
        if decision is not DecisionValue.REPLAN:
            return ()
        if state is None or ctx.preflight is None:
            return ()
        findings = ctx.findings
        replan = [f for f in findings if f.effect is FindingEffect.REPLAN]
        if not replan:
            return ()
        if any(f.effect is FindingEffect.DENY for f in findings):
            return ()
        if any(f.code in _HINT_FORBIDDEN for f in findings):
            return ()
        if not all(f.resolver == "user" and f.code in _SHRINKABLE for f in replan):
            return ()
        scopes = self._active_scopes(ctx)
        if len(scopes) != 1:
            return ()
        scope = scopes[0]
        fingerprints = self._fingerprints(state, ctx)
        transfers = list(scope.transfers)
        shrunk: list[int] = []
        for finding in replan:
            if finding.step_id is None:
                continue
            maximum = ctx.preflight.hint_max_outflow.get(finding.step_id)
            if maximum is None or maximum <= 0:
                return ()
            step = next((s for s in ctx.plan_draft.steps if s.step_id == finding.step_id), None)
            if step is None or step.tool != "transfer":
                continue
            args = step.args
            target = (
                fingerprints.get(args.payee_id)
                if args.payee_id
                else ("int:" + args.to_account_id if args.to_account_id else None)
            )
            old_amount = args.amount_cents
            # A hint may only ever shrink (§7.9). If the proposed maximum is not
            # strictly below the replaced amount, it would enlarge or no-op the
            # authorization, so no hint is produced at all.
            if maximum >= old_amount:
                return ()
            matched = False
            for i, transfer in enumerate(transfers):
                if transfer.target_fingerprint == target and transfer.amount_cents == old_amount:
                    transfers[i] = transfer.model_copy(update={"amount_cents": maximum})
                    shrunk.append(maximum)
                    matched = True
                    break
            if not matched:
                return ()
        if not shrunk:
            return ()
        new_scope = scope.model_copy(update={"transfers": tuple(transfers)})
        hint = ReplanHint(
            id=derive(ctx.plan_id, "hint", "hnt"),
            label="最多 " + "、".join(_format_cents(amount) for amount in shrunk),
            scope=new_scope,
            expires_at_wall=(ctx.wall_now() + timedelta(minutes=10)).isoformat(),
        )
        return (hint,)

    @staticmethod
    def _active_scopes(ctx: EvalContext):
        auth = ctx.authorization
        if auth is None:
            return []
        scopes = []
        for u in auth.utterances.values():
            if (
                u.id in auth.utterance_ids
                and u.kind != "context"
                and u.parse_status is ParseStatus.SCOPED
                and auth.statuses.get(u.id) is UtteranceStatus.ACTIVE
                and datetime.fromisoformat(u.expires_at_wall) > ctx.wall_now()
            ):
                scopes.append(u.scope)
        return scopes

    @staticmethod
    def _fingerprints(state: BankState, ctx: EvalContext) -> dict[str, str]:
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

    # ---- shadow B1 ---------------------------------------------------------
    async def _shadow_b1(self, ctx: EvalContext, state: BankState) -> Decision | None:
        from . import evaluate  # lazy import avoids the pipeline/decision cycle

        shadow = EvalContext(
            plan_draft=ctx.plan_draft,
            plan_id=ctx.plan_id,
            origin=ctx.origin,
            customer_id=ctx.customer_id,
            session_id=ctx.session_id,
            authorization=ctx.authorization,
            identity=ctx.identity,
            mode=EvalMode.SHADOW_B1,
            profile="B1",
            wall_now=ctx.wall_now,
            conn=ctx.conn,
            l3=ctx.l3,
        )
        shadow._lazy_state = state
        await evaluate(shadow)
        return shadow.decision


stage = DecisionStage()
