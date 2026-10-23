"""Deterministic confirmation panels; display content is the equivalence boundary."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from safebank_contracts.enums import Risk

from ..middleware import digest_hex
from ..policy.registry import policy
from ..schemas.enums import PlanStatus
from ..schemas.planview import Disclosure, PlanView, PlanViewField, PlanViewStep

if TYPE_CHECKING:
    from ..pipeline.context import EvalContext

# These change on an equivalent evaluation and are separately MAC-bound.
ENVELOPE = {"approval_id", "evaluation_id", "status", "expires_at_wall", "view_hash"}


def content_hash(view: PlanView) -> str:
    return digest_hex(view.model_dump(mode="json", exclude=ENVELOPE))


def _value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_view(ctx: EvalContext) -> tuple[PlanView, str]:
    """Use resolved steps and the exact preflight shown by the persisted decision."""
    if ctx.decision is None or not ctx.plan_id or not ctx.session_id or not ctx.origin:
        raise ValueError("completed context with plan/session/origin is required")
    decision = ctx.decision
    state = ctx.snapshot or ctx._lazy_state
    steps = []
    disclosures = {}
    otp = decision.risk == Risk.RED
    for step in ctx.steps:
        tool_policy = policy(step.tool)
        otp = otp or tool_policy.otp_required
        fields = []
        for key, value in sorted(step.args.items()):
            if value is None:
                continue
            display = _value(value)
            if key in {"from_account_id", "to_account_id", "account_id"} and state:
                entity = state.accounts.get(value) if isinstance(value, str) else None
                if entity:
                    display = f"{entity.display_name} / 尾号{entity.account_no[-4:]} / {entity.id}"
            elif key == "payee_id" and state and isinstance(value, str):
                entity = state.payees.get(value)
                if entity:
                    kind = "本行" if entity.target_account_id else entity.bank_name
                    display = f"{entity.name} / {kind} / 尾号{entity.account_no[-4:]} / {entity.id}"
            elif key == "card_id" and state and isinstance(value, str):
                entity = state.cards.get(value)
                if entity:
                    display = f"尾号{entity.last4} / {entity.id}"
            elif key == "product_id" and state:
                product = next((p for p in state.products if p.id == value), None)
                if product:
                    display = f"{product.name} / {product.risk_label} / {product.id}"
            fields.append(
                PlanViewField(
                    key=key, label=key, value=display, unit="分" if key.endswith("_cents") else None
                )
            )
        if step.tool == "subscribe_product":
            product_id = step.args.get("product_id")
            product = (
                next((p for p in state.products if p.id == product_id), None) if state else None
            )
            if product is None:
                raise ValueError("subscription panel requires the product disclosure")
            disclosures[product.id] = Disclosure(
                product_id=product.id,
                version=product.disclosure_version,
                text=product.risk_disclosure,
            )
        steps.append(
            PlanViewStep(
                step_id=step.step_id,
                action_kind=step.action_kind,
                title=tool_policy.title_template,
                fields=tuple(fields),
                irreversible=tool_policy.irreversible,
            )
        )
    view = PlanView(
        approval_id=decision.approval_id or "apv_unassigned",
        plan_id=ctx.plan_id,
        evaluation_id=decision.evaluation_id,
        session_id=ctx.session_id,
        status=PlanStatus.EVALUATED,
        origin=ctx.origin,
        risk=decision.risk,
        steps=tuple(steps),
        preflight=decision.preflight,
        findings=tuple(f.model_dump(mode="json") for f in decision.findings),
        disclosures=tuple(disclosures[k] for k in sorted(disclosures)),
        risk_ack_required=bool(disclosures),
        otp_required=otp,
        expires_at_wall=(ctx.wall_now() + timedelta(minutes=5)).isoformat(),
        view_hash="",
    )
    hash_value = content_hash(view)
    return view.model_copy(update={"view_hash": hash_value}), hash_value
