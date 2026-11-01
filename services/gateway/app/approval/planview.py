"""Deterministic approval panel PlanView and semantic ``view_hash`` (W4).

The content hash covers the *visible business semantics*: plan / session /
origin, the ordered steps (target name + masked account + same-bank marker,
amount, memo, frequency, business dates), step-by-step balances, risk,
irreversible flags, the full disclosure text and its version, and the
``risk_ack_required`` / ``otp_required`` flags. Approval / evaluation IDs,
display status, approval expiry and the hash itself are the *mutable envelope*:
they are excluded from the content hash and are instead bound by the HMAC in
``service.py``, so an equivalent re-evaluation yields the same ``view_hash``
(PRD §7.10.1/5).

This module is a pure function of ``EvalContext``: it performs no database or
HTTP I/O and never trusts ``ctx.decision``'s rationale text.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from safebank_bankcore.funds import fund_event_at
from safebank_bankcore.ids import canonical, derive
from safebank_bankcore.periods import normalize_period
from safebank_contracts import risk_questionnaire
from safebank_contracts.enums import Risk
from safebank_contracts.state import BankState
from safebank_contracts.tools import Assessment

from ..policy.registry import policy
from ..schemas.enums import PlanStatus
from ..schemas.planview import Disclosure, PlanView, PlanViewField, PlanViewStep

if TYPE_CHECKING:  # pragma: no cover
    from ..pipeline.context import EvalContext, ResolvedStep

# Placeholder used only for the in-memory view; service.py always replaces it
# with the real ``apv_*`` ID before persisting ``view_json`` (PRD §9.5).
UNASSIGNED_APPROVAL_ID = "apv_unassigned"

_APPROVAL_TTL = timedelta(minutes=5)
_FUND_TOOLS = frozenset({"subscribe_product", "redeem_product"})


def _yuan(cents: int) -> str:
    # Integer arithmetic only (§0.1: 金额禁止 float); negative guards keep the
    # display helper honest even for a non-positive sentinel.
    if cents < 0:
        return f"-{_yuan(-cents)}"
    return f"{cents // 100:,}.{cents % 100:02d}"


def _state(ctx: EvalContext) -> BankState | None:
    # ⑤ snapshot may be skipped (profile ablation / earlier hard DENY), but ②
    # entity always caches the same L3 state into ``_lazy_state``.
    return ctx.snapshot if ctx.snapshot is not None else ctx._lazy_state


def _last4(account_no: str) -> str:
    return account_no[-4:]


def _target_payee(ctx: EvalContext, state: BankState | None, payee_id: str):
    if state is not None and payee_id in state.payees:
        return state.payees[payee_id]
    # A payee created earlier in the same plan (EntityRef) has a derived ID and
    # is not yet present in the snapshot; its display fields come from the
    # add_payee args that created it.
    for step in ctx.steps:
        if step.tool == "add_payee" and derive(ctx.plan_id, step.step_id, "pye") == payee_id:
            return step.args
    raise ValueError(f"payee {payee_id!r} is absent from the snapshot")


def _transfer_fields(ctx: EvalContext, state: BankState | None, args: dict[str, Any]):
    if args.get("payee_id"):
        target = _target_payee(ctx, state, args["payee_id"])
        if isinstance(target, dict):
            name, bank_name, account_no, target_account_id = (
                target.get("name"),
                target.get("bank_name"),
                target.get("account_no"),
                None,
            )
        else:
            name, bank_name, account_no, target_account_id = (
                target.name,
                target.bank_name,
                target.account_no,
                target.target_account_id,
            )
        label = "本行" if target_account_id else bank_name
        target_field = PlanViewField(key="target", label="收款人", value=name)
        account_field = PlanViewField(
            key="target_account", label="收款账户", value=f"{label} 尾号{_last4(account_no)}"
        )
    else:
        account_id = args.get("to_account_id")
        if state is None or account_id not in state.accounts:
            raise ValueError(f"self-transfer account {account_id!r} is absent from the snapshot")
        account = state.accounts[account_id]
        target_field = PlanViewField(key="target", label="转入账户", value=account.display_name)
        account_field = PlanViewField(
            key="target_account", label="本人账户", value=f"本行 尾号{_last4(account.account_no)}"
        )
    return [target_field, account_field]


def _assessment_fields(ctx: EvalContext, args: dict[str, Any]) -> list[PlanViewField]:
    if ctx.origin != "app_form":
        raise ValueError("risk assessment requires app_form")
    assessment = Assessment.model_validate(args)
    questions = risk_questionnaire.QUESTIONNAIRE.get(assessment.questionnaire_id)
    answers = {answer.question_id: answer.option_id for answer in assessment.answers}
    if questions is None or len(questions) != 6 or set(answers) != set(questions):
        raise ValueError("assessment requires complete authoritative questionnaire metadata")
    fields = [
        PlanViewField(key="questionnaire_id", label="问卷版本", value=assessment.questionnaire_id)
    ]
    # The versioned contract supplies both order and text. Never display caller
    # labels or bare IDs; these fields already participate in the semantic hash.
    for question_id, question in questions.items():
        options = {option.id: option for option in question.options}
        selected = options.get(answers[question_id])
        if (
            question.id != question_id
            or not isinstance(question.prompt, str)
            or not question.prompt.strip()
            or len(options) != len(question.options)
            or selected is None
            or not isinstance(selected.label, str)
            or not selected.label.strip()
        ):
            raise ValueError("assessment requires authoritative question and answer text")
        fields.append(
            PlanViewField(key=f"answer:{question_id}", label=question.prompt, value=selected.label)
        )
    return fields


def _fields_for(ctx: EvalContext, state: BankState | None, step: ResolvedStep):
    args = step.args
    tool = step.tool
    fields: list[PlanViewField] = []

    def amount(value: int) -> PlanViewField:
        return PlanViewField(key="amount", label="金额", value=_yuan(value), unit="元")

    if tool in ("transfer", "schedule_transfer"):
        fields.extend(_transfer_fields(ctx, state, args))
        fields.append(amount(args["amount_cents"]))
        if tool == "schedule_transfer":
            fields.append(PlanViewField(key="period", label="周期", value=args["period"]))
            fields.append(
                PlanViewField(key="first_run_at", label="首次执行", value=args["first_run_at"])
            )
            if args.get("end_at") is not None:
                fields.append(PlanViewField(key="end_at", label="结束日期", value=args["end_at"]))
        if args.get("memo") is not None:
            fields.append(PlanViewField(key="memo", label="备注", value=args["memo"]))
    elif tool == "add_payee":
        fields.append(PlanViewField(key="name", label="收款人", value=args["name"]))
        fields.append(
            PlanViewField(
                key="account_no",
                label="收款账户",
                value=f"{args['bank_name']} 尾号{_last4(args['account_no'])}",
            )
        )
        if args.get("alias") is not None:
            fields.append(PlanViewField(key="alias", label="别名", value=args["alias"]))
    elif tool == "cancel_scheduled":
        if state is None:
            raise ValueError("cancel_scheduled requires the snapshot")
        instruction = next((i for i in state.instructions if i.id == args["instruction_id"]), None)
        if instruction is None:
            raise ValueError(
                f"scheduled instruction {args['instruction_id']!r} is absent from the snapshot"
            )
        fields.append(
            PlanViewField(key="instruction_id", label="定时转账", value=args["instruction_id"])
        )
        fields.append(
            PlanViewField(key="target_display", label="目标", value=instruction.target_display)
        )
    elif tool == "create_split_request":
        fields.append(amount(args["total_cents"]))
        fields.append(
            PlanViewField(
                key="include_self", label="包含本人", value="是" if args["include_self"] else "否"
            )
        )
        for pid in args["participant_payee_ids"]:
            if state is None or pid not in state.payees:
                raise ValueError(f"split participant {pid!r} is absent from the snapshot")
            fields.append(
                PlanViewField(
                    key=f"participant:{pid}", label="参与人", value=state.payees[pid].name
                )
            )
    elif tool in (
        "update_card_limits",
        "update_card_controls",
        "freeze_card",
        "unfreeze_card",
        "report_card_lost",
    ):
        card_id = args["card_id"]
        if state is None or card_id not in state.cards:
            raise ValueError(f"card {card_id!r} is absent from the snapshot")
        card = state.cards[card_id]
        fields.append(PlanViewField(key="card", label="卡片", value=f"尾号{card.last4}"))
        if tool == "update_card_limits":
            if args.get("single_limit_cents") is not None:
                fields.append(
                    PlanViewField(
                        key="single_limit",
                        label="单笔限额",
                        value=_yuan(args["single_limit_cents"]),
                        unit="元",
                    )
                )
            if args.get("daily_limit_cents") is not None:
                fields.append(
                    PlanViewField(
                        key="daily_limit",
                        label="每日限额",
                        value=_yuan(args["daily_limit_cents"]),
                        unit="元",
                    )
                )
        elif tool == "update_card_controls":
            for channel in ("online", "overseas", "atm", "quickpass"):
                if args.get(channel) is not None:
                    fields.append(
                        PlanViewField(
                            key=channel, label=channel, value="开启" if args[channel] else "关闭"
                        )
                    )
        elif tool == "report_card_lost":
            fields.append(
                PlanViewField(key="replace", label="补卡", value="是" if args["replace"] else "否")
            )
    elif tool == "cancel_subscription":
        if state is None:
            raise ValueError("cancel_subscription requires the snapshot")
        subscription = next(
            (s for s in state.subscriptions if s.instruction_id == args["instruction_id"]), None
        )
        if subscription is None:
            raise ValueError(f"subscription {args['instruction_id']!r} is absent from the snapshot")
        fields.append(
            PlanViewField(key="instruction_id", label="代扣授权", value=args["instruction_id"])
        )
        fields.append(PlanViewField(key="merchant", label="商户", value=subscription.merchant_name))
        active = subscription.status == "ACTIVE"
        monthly: int | None = None if active else 0
        if state.obligations is not None:
            instruction = state.obligations.standing_instructions.get(subscription.instruction_id)
            billing = state.obligations.billings.get(subscription.billing_id)
            if (
                instruction is None
                or billing is None
                or instruction.kind != "MERCHANT_MANDATE"
                or instruction.billing_id != billing.id
                or instruction.customer_id != billing.customer_id
                or instruction.from_account_id != billing.account_id
                or instruction.merchant_id != billing.merchant_id
            ):
                raise ValueError("subscription is absent from the authoritative billing snapshot")
            active = active and instruction.status == "ACTIVE" and billing.active
            monthly = normalize_period(billing.amount_cents, billing.period)[0] if active else 0
        fields.append(
            PlanViewField(
                key="monthly_saving",
                label="预计每月节省",
                value=_yuan(monthly) if monthly is not None else "暂无法确定",
                unit="元" if monthly is not None else None,
            )
        )
    elif tool == "submit_risk_assessment":
        fields.extend(_assessment_fields(ctx, args))
    elif tool in _FUND_TOOLS:
        product_id = args["product_id"]
        if state is None:
            raise ValueError(f"fund product {product_id!r} is absent from the snapshot")
        product = next((p for p in state.products if p.id == product_id), None)
        if product is None:
            raise ValueError(f"fund product {product_id!r} is absent from the snapshot")
        fields.append(PlanViewField(key="product", label="产品", value=product.name))
        if tool == "subscribe_product":
            fields.append(amount(args["amount_cents"]))
            fields.append(
                PlanViewField(
                    key="expected_confirm_at",
                    label="预计确认时间",
                    value=fund_event_at(product, state.sim_now, "SUB").isoformat(),
                    unit="datetime",
                )
            )
        else:
            fields.append(
                PlanViewField(key="units", label="份额", value=str(args["units"]), unit="份")
            )
            fields.append(
                PlanViewField(
                    key="expected_settle_at",
                    label="预计到账时间",
                    value=fund_event_at(product, state.sim_now, "RED").isoformat(),
                    unit="datetime",
                )
            )
    return tuple(fields)


def _build_steps(ctx: EvalContext, state: BankState | None) -> tuple[PlanViewStep, ...]:
    steps = []
    for step in ctx.steps:
        rule = policy(step.tool)
        steps.append(
            PlanViewStep(
                step_id=step.step_id,
                action_kind=step.action_kind,
                title=rule.title_template,
                fields=_fields_for(ctx, state, step),
                irreversible=rule.irreversible,
            )
        )
    return tuple(steps)


def _build_disclosures(ctx: EvalContext, state: BankState | None) -> tuple[Disclosure, ...]:
    disclosures: list[Disclosure] = []
    seen: set[str] = set()
    for step in ctx.steps:
        if step.tool not in _FUND_TOOLS:
            continue
        product_id = step.args["product_id"]
        if state is None:
            raise ValueError(f"fund product {product_id!r} is absent from the snapshot")
        product = next((p for p in state.products if p.id == product_id), None)
        if product is None or not product.risk_disclosure or not product.disclosure_version:
            raise ValueError(f"fund disclosure for {product_id!r} is missing")
        if product_id in seen:
            continue
        seen.add(product_id)
        disclosures.append(
            Disclosure(
                product_id=product_id,
                version=product.disclosure_version,
                text=product.risk_disclosure,
            )
        )
    return tuple(disclosures)


def _steps_payload(steps: tuple[PlanViewStep, ...]) -> list[dict[str, Any]]:
    return [
        {
            "step_id": s.step_id,
            "action_kind": s.action_kind,
            "title": s.title,
            "fields": [
                {"key": f.key, "label": f.label, "value": f.value, "unit": f.unit} for f in s.fields
            ],
            "irreversible": s.irreversible,
        }
        for s in steps
    ]


def _content_dict(
    *,
    plan_id: str,
    session_id: str,
    origin: str,
    risk: Risk,
    steps: tuple[PlanViewStep, ...],
    preflight: dict[str, Any],
    findings: list[dict[str, Any]],
    disclosures: tuple[Disclosure, ...],
    risk_ack_required: bool,
    otp_required: bool,
) -> dict[str, Any]:
    return {
        "plan_id": plan_id,
        "session_id": session_id,
        "origin": origin,
        "risk": risk.value,
        "steps": _steps_payload(steps),
        "preflight": preflight,
        "findings": findings,
        "disclosures": [
            {"product_id": d.product_id, "version": d.version, "text": d.text} for d in disclosures
        ],
        "risk_ack_required": risk_ack_required,
        "otp_required": otp_required,
    }


def _content_from_view(view: PlanView) -> dict[str, Any]:
    return _content_dict(
        plan_id=view.plan_id,
        session_id=view.session_id,
        origin=view.origin,
        risk=view.risk,
        steps=view.steps,
        preflight=view.preflight.model_dump(mode="json"),
        findings=[dict(f) for f in view.findings],
        disclosures=view.disclosures,
        risk_ack_required=view.risk_ack_required,
        otp_required=view.otp_required,
    )


def content_hash(view: PlanView) -> str:
    """Recompute the semantic hash from a view's content (envelope excluded)."""
    return hashlib.sha256(canonical(_content_from_view(view)).encode("utf-8")).hexdigest()


def _approval_deadline(ctx: EvalContext, now: datetime) -> datetime:
    deadline = now + _APPROVAL_TTL
    auth = ctx.authorization
    if auth is not None:
        for uid in auth.utterance_ids:
            utterance = auth.utterances.get(uid)
            if utterance is None:
                # A missing authorization utterance must not silently extend the
                # approval beyond its utterance validity (fail closed).
                raise ValueError(f"authorization is missing utterance {uid!r}")
            expires = datetime.fromisoformat(utterance.expires_at_wall)
            if expires < deadline:
                deadline = expires
    return deadline


def _wall_now(ctx: EvalContext) -> datetime:
    now = ctx.wall_now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("wall clock must return a timezone-aware datetime")
    return now


def build_view(ctx: EvalContext) -> tuple[PlanView, str]:
    """Build the approval panel PlanView and its ``view_hash`` from ``ctx``.

    Pure (no DB/HTTP). ``approval_id`` is a placeholder that ``service.py``
    replaces with the real ID; ``expires_at_wall`` / ``status`` / ``view_hash``
    are the envelope and never affect the content hash.
    """
    if ctx.plan_id is None or ctx.session_id is None or ctx.origin is None:
        raise ValueError("build_view requires plan_id, session_id and origin")
    decision = ctx.decision
    if decision is None:
        raise ValueError("build_view requires ctx.decision")
    state = _state(ctx)
    risk = decision.risk
    steps = _build_steps(ctx, state)
    disclosures = _build_disclosures(ctx, state)
    findings = [f.model_dump(mode="json") for f in decision.findings]
    preflight = decision.preflight
    risk_ack_required = risk is not Risk.GREEN
    otp_required = any(policy(s.tool).otp_required for s in ctx.steps) or risk is Risk.RED
    view_hash = hashlib.sha256(
        canonical(
            _content_dict(
                plan_id=ctx.plan_id,
                session_id=ctx.session_id,
                origin=ctx.origin,
                risk=risk,
                steps=steps,
                preflight=preflight.model_dump(mode="json"),
                findings=findings,
                disclosures=disclosures,
                risk_ack_required=risk_ack_required,
                otp_required=otp_required,
            )
        ).encode("utf-8")
    ).hexdigest()
    view = PlanView(
        approval_id=UNASSIGNED_APPROVAL_ID,
        plan_id=ctx.plan_id,
        evaluation_id=decision.evaluation_id,
        session_id=ctx.session_id,
        status=PlanStatus.PENDING_APPROVAL,
        origin=ctx.origin,
        risk=risk,
        steps=steps,
        preflight=preflight,
        findings=tuple(findings),
        disclosures=disclosures,
        risk_ack_required=risk_ack_required,
        otp_required=otp_required,
        expires_at_wall=_approval_deadline(ctx, _wall_now(ctx)).isoformat(),
        view_hash=view_hash,
    )
    return view, view_hash
