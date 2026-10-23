"""Stage ⑧ deterministic aggregation, with no authorization delegated to an LLM."""

import hashlib
from pathlib import Path

from safebank_contracts.codes import Code
from safebank_contracts.enums import Risk

from ..policy.registry import policy
from ..schemas.decision import Decision, DecisionValue, PreflightView
from ..schemas.enums import FindingEffect
from ..workflow.identity import new_id
from ..workflow.sequence import THRESHOLDS
from .context import EvalContext, Stage

RANK = {Risk.GREEN: 0, Risk.YELLOW: 1, Risk.RED: 2}


def policy_version() -> str:
    root = Path(__file__).resolve().parents[2] / "policies"
    payload = b"".join(
        (root / name).read_bytes() for name in ("tools.yaml", "thresholds.yaml", "workflow.yaml")
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class DecisionStage(Stage):
    name = "decision"
    always_run = True

    async def run(self, ctx: EvalContext) -> None:
        if ctx.plan_id is None:
            return
        risk = Risk.GREEN
        for step in ctx.steps:
            risk = max(risk, Risk(policy(step.tool).base_risk), key=RANK.__getitem__)
            if step.action_kind.endswith("LOOSEN"):
                risk = Risk.RED
            if step.tool in ("report_card_lost", "subscribe_product"):
                ctx.add(Code.IRREVERSIBLE_STEP, FindingEffect.RISK_RED, step_id=step.step_id)
            if (
                ctx.profile != "B0"
                and step.outflow
                and (step.args.get("amount_cents", 0) > THRESHOLDS["yellow_cap_cents"])
                and not any(
                    f.code == Code.AMOUNT_ABOVE_YELLOW_CAP and f.step_id == step.step_id
                    for f in ctx.findings
                )
            ):
                ctx.add(Code.AMOUNT_ABOVE_YELLOW_CAP, FindingEffect.RISK_RED, step_id=step.step_id)
        for finding in ctx.findings:
            if finding.effect == FindingEffect.RISK_RED:
                risk = Risk.RED
            elif finding.effect == FindingEffect.RISK_YELLOW:
                risk = max(risk, Risk.YELLOW, key=RANK.__getitem__)
        effects = {f.effect for f in ctx.findings}
        if FindingEffect.DENY in effects:
            value = DecisionValue.DENY
        elif FindingEffect.REPLAN in effects:
            value = DecisionValue.REPLAN
        else:
            value = DecisionValue.ALLOW if risk == Risk.GREEN else DecisionValue.REQUIRE_APPROVAL
        ctx.decision = Decision(
            plan_id=ctx.plan_id,
            evaluation_id=new_id("evl"),
            revision=1,
            decision=value,
            risk=risk,
            findings=tuple(ctx.findings),
            preflight=ctx.preflight.view
            if ctx.preflight and ctx.preflight.view
            else PreflightView(executable=not ctx.has_hard_deny()),
            versions=dict(ctx.snapshot.versions) if ctx.snapshot else {},
            clock_version=ctx.snapshot.clock_version if ctx.snapshot else 0,
            policy_version=policy_version(),
        )


stage = DecisionStage()
