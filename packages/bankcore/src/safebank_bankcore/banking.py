"""Deterministic transfer/card business rules (PRD §5/§7.7/§8.2)."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.plan import Effect, StepResult, Violation
from safebank_contracts.state import (
    BankState,
    Card,
    Counters,
    InsertAssessment,
    InsertPayee,
    LedgerTransfer,
    Operation,
    OperationEvent,
    Payee,
    RiskAssessmentRecord,
    UpsertCard,
)
from safebank_contracts.tools import EntityRef
from safebank_contracts.views import WRITE_RESULTS, CardView, ChannelFlags

from .ids import derive
from .ledger import post_transfer, replace_state
from .risk import apply_risk_assessment as score_risk
from .split import apply_create_split_request
from .funds import apply_redeem, apply_subscribe, order_view
from .standing import apply_cancel_scheduled, apply_cancel_subscription, apply_schedule_transfer


@dataclass(frozen=True)
class Context:
    customer_id: str
    plan_id: str
    step_id: str
    daily_outflow_limit_cents: int = 5_000_000


def reject(state, ctx, code):
    return state, (), (Violation(code=code, step_id=ctx.step_id, message=code.value),)


def operation(state, ctx, now, kind, *, target=None, amount=0):
    return OperationEvent(
        operation=Operation(
            event_id=derive(ctx.plan_id, ctx.step_id, "evt"),
            customer_id=ctx.customer_id,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            action_kind=kind,
            target_fingerprint=target,
            amount_cents=amount,
            occurred_at=now,
        )
    )


def apply_transfer(state: BankState, args, now: datetime, ctx: Context):
    source = state.accounts.get(args.from_account_id)
    if source is None or source.customer_id != ctx.customer_id:
        return reject(state, ctx, Code.NOT_FOUND)
    external = args.payee_id is not None
    payee = None
    if external:
        if not isinstance(args.payee_id, str):
            return reject(state, ctx, Code.DEPENDENCY_UNMET)
        payee = state.payees.get(args.payee_id)
        if not payee or payee.customer_id != ctx.customer_id:
            return reject(state, ctx, Code.PAYEE_NOT_FOUND)
        target = state.accounts.get(payee.target_account_id or "acc_external_clearing")
        if target is None:
            return reject(state, ctx, Code.DEPENDENCY_UNMET)
        # A payee pointing to one's own account must use the explicit self-transfer path.
        if target.customer_id == ctx.customer_id:
            return reject(state, ctx, Code.CONTRACT_INVALID)
        if source.current_card_id:
            card = state.cards.get(source.current_card_id)
            if not card or card.status != "ACTIVE":
                return reject(state, ctx, Code.CARD_STATE_CONFLICT)
            single, daily = card.single_limit_cents, card.daily_limit_cents
        else:
            single, daily = source.single_limit_cents, source.daily_limit_cents
        if args.amount_cents > single:
            return reject(state, ctx, Code.CARD_SINGLE_LIMIT)
        if state.counters.daily_used_cents.get(source.id, 0) + args.amount_cents > daily:
            return reject(state, ctx, Code.CARD_DAILY_LIMIT)
        if (
            state.counters.daily_outflow_cents.get(ctx.customer_id, 0) + args.amount_cents
            > ctx.daily_outflow_limit_cents
        ):
            return reject(state, ctx, Code.DAILY_OUTFLOW_LIMIT)
        code = "P2P_INTERNAL" if payee.target_account_id else "P2P_EXTERNAL"
    else:
        target = state.accounts.get(args.to_account_id)
        if not target or target.customer_id != ctx.customer_id:
            return reject(state, ctx, Code.NOT_FOUND)
        code = "SELF_TRANSFER"
    cmd = LedgerTransfer(
        id=derive(ctx.plan_id, ctx.step_id, "trf"),
        debit_account_id=source.id,
        credit_account_id=target.id,
        amount_cents=args.amount_cents,
        code=code,
        phase="POSTED",
        pending_id=None,
        plan_id=ctx.plan_id,
        step_id=ctx.step_id,
        occurred_at=now,
        expires_at=None,
    )
    after, effects, errors = post_transfer(
        state,
        cmd,
        now,
        memo=args.memo,
        payee_id=payee.id if payee else None,
        card_id=source.current_card_id if external else None,
    )
    if errors:
        return after, effects, errors
    if not effects:
        return after, effects, ()
    if external:
        daily = dict(state.counters.daily_outflow_cents)
        rolling = dict(state.counters.rolling_outflow_cents)
        used = dict(state.counters.daily_used_cents)
        daily[ctx.customer_id] = daily.get(ctx.customer_id, 0) + args.amount_cents
        rolling[ctx.customer_id] = rolling.get(ctx.customer_id, 0) + args.amount_cents
        used[source.id] = used.get(source.id, 0) + args.amount_cents
        after = replace_state(
            after,
            counters=Counters(
                daily_outflow_cents=daily, rolling_outflow_cents=rolling, daily_used_cents=used
            ),
        )
    kind = "TRANSFER_EXTERNAL" if external else "TRANSFER_SELF"
    return (
        after,
        (
            *effects,
            operation(
                state,
                ctx,
                now,
                kind,
                target=payee.target_fingerprint if payee else target.id,
                amount=args.amount_cents,
            ),
        ),
        (),
    )


def apply_add_payee(state: BankState, args, now: datetime, ctx: Context):
    entry = state.directory.get("number:" + args.account_no)
    if entry is None:
        return reject(state, ctx, Code.DEPENDENCY_UNMET)
    existing = next(
        (
            p
            for p in state.payees.values()
            if p.customer_id == ctx.customer_id and p.target_fingerprint == entry.target_fingerprint
        ),
        None,
    )
    if existing:
        return state, (), ()
    if entry.customer_id == ctx.customer_id:
        return reject(state, ctx, Code.CONTRACT_INVALID)
    payee = Payee(
        id=derive(ctx.plan_id, ctx.step_id, "pye"),
        customer_id=ctx.customer_id,
        name=args.name,
        alias=args.alias,
        phone=args.phone,
        bank_name=args.bank_name,
        account_no=args.account_no,
        target_account_id=entry.target_account_id,
        target_fingerprint=entry.target_fingerprint,
        created_at=now,
    )
    return (
        replace_state(state, payees=dict(state.payees) | {payee.id: payee}),
        (
            InsertPayee(payee=payee),
            operation(state, ctx, now, "PAYEE_ADD", target=payee.target_fingerprint),
        ),
        (),
    )


def owned_card(state, args, ctx):
    card = state.cards.get(args.card_id)
    if card and state.accounts[card.account_id].customer_id == ctx.customer_id:
        return card
    return None


def apply_card(state: BankState, args, now: datetime, ctx: Context, tool: str):
    card = owned_card(state, args, ctx)
    if card is None:
        return reject(state, ctx, Code.NOT_FOUND)
    if card.status == "LOST":
        return reject(state, ctx, Code.CARD_STATE_CONFLICT)
    data = card.model_dump()
    kind = ""
    if tool == "update_card_limits":
        for key in ("single_limit_cents", "daily_limit_cents"):
            if getattr(args, key) is not None:
                data[key] = getattr(args, key)
        loosen = any(
            data[k] > getattr(card, k) for k in ("single_limit_cents", "daily_limit_cents")
        )
        kind = "CARD_LIMIT_LOOSEN" if loosen else "CARD_LIMIT_TIGHTEN"
    elif tool == "update_card_controls":
        controls = card.controls.model_dump()
        for key in controls:
            if getattr(args, key) is not None:
                controls[key] = getattr(args, key)
        loosen = any(controls[k] and not getattr(card.controls, k) for k in controls)
        data["controls"] = controls
        kind = "CARD_CONTROL_LOOSEN" if loosen else "CARD_CONTROL_TIGHTEN"
    else:
        loosen = tool == "unfreeze_card"
        data["status"] = {
            "freeze_card": "FROZEN",
            "unfreeze_card": "ACTIVE",
            "report_card_lost": "LOST",
        }[tool]
        kind = {
            "freeze_card": "CARD_FREEZE",
            "unfreeze_card": "CARD_UNFREEZE",
            "report_card_lost": "CARD_LOST",
        }[tool]
    if card.status == "FROZEN" and loosen:
        # Explicit unfreeze is allowed; opening channels or limits is not.
        if tool != "unfreeze_card":
            return reject(state, ctx, Code.CARD_STATE_CONFLICT)
    try:
        changed = Card(**data)
    except ValidationError:
        return reject(state, ctx, Code.CONTRACT_INVALID)
    if changed == card:
        return state, (), ()
    cards = dict(state.cards) | {card.id: changed}
    accounts = dict(state.accounts)
    effects = [UpsertCard(card=changed), operation(state, ctx, now, kind)]
    if tool == "report_card_lost" and args.replace:
        new_id = derive(ctx.plan_id, ctx.step_id, "crd")
        last4 = f"{int(hashlib.sha256(new_id.encode()).hexdigest()[:8], 16) % 10000:04d}"
        new = Card(
            **(
                card.model_dump()
                | {
                    "id": new_id,
                    "last4": last4,
                    "status": "ACTIVE",
                    "controls": ChannelFlags(
                        online=True, overseas=False, atm=False, quickpass=False
                    ),
                    "replaces_id": card.id,
                    "created_at": now,
                }
            )
        )
        cards[new.id] = new
        accounts[card.account_id] = accounts[card.account_id].model_copy(
            update={"current_card_id": new.id}
        )
        effects.insert(1, UpsertCard(card=new, make_current=True))
    return replace_state(state, cards=cards, accounts=accounts), tuple(effects), ()


def card_view(state: BankState, card: Card) -> CardView:
    used = state.counters.daily_used_cents.get(card.account_id, 0)
    replacement = next((c.id for c in state.cards.values() if c.replaces_id == card.id), None)
    return CardView(
        **card.model_dump(exclude={"created_at", "replaces_id"}),
        daily_used_cents=used,
        remaining_daily_cents=max(0, card.daily_limit_cents - used),
        current=state.accounts[card.account_id].current_card_id == card.id,
        replacement_id=replacement,
    )


def apply_risk_assessment(state: BankState, args, now: datetime, ctx: Context):
    """Score answers via the pure risk engine and produce an InsertAssessment effect.

    Only app_form origin is allowed; L3 trusts the questionnaire answers and
    computes score/level itself.
    """
    from .ids import derive as _derive

    try:
        result = score_risk(
            questionnaire_id=args.questionnaire_id,
            answers=tuple((a.question_id, a.option_id) for a in args.answers),
            assessed_at=now,
        )
    except ValueError:
        return reject(state, ctx, Code.CONTRACT_INVALID)

    assessment_id = _derive(ctx.plan_id, ctx.step_id, "asm")
    record = RiskAssessmentRecord(
        id=assessment_id,
        customer_id=ctx.customer_id,
        questionnaire_id=args.questionnaire_id,
        answers=tuple((a.question_id, a.option_id) for a in args.answers),
        score=result.score,
        level=f"R{result.level}",
        assessed_at=now,
        valid_until=result.valid_until,
        plan_id=ctx.plan_id,
    )

    # Update customer summary
    customers = dict(state.customers)
    c = customers[ctx.customer_id]
    customers[ctx.customer_id] = c.model_copy(
        update={
            "risk_level": result.level,
            "risk_assessed_at": now,
            "risk_valid_until": result.valid_until.isoformat(),
        }
    )

    # Add to in-memory assessments
    assessments = state.assessments + (record,)

    new_state = replace_state(state, customers=customers, assessments=assessments)
    effects = (
        InsertAssessment(record=record),
        operation(state, ctx, now, "ASSESSMENT_SUBMIT"),
    )
    return new_state, effects, ()


def to_public_effects(before: BankState, after: BankState, customer_id: str):
    effects = []
    for account in after.accounts.values():
        if account.customer_id != customer_id:
            continue
        old = before.accounts[account.id]
        for field in ("balance_cents", "available_cents", "current_card_id"):
            if getattr(old, field) != getattr(account, field):
                effects.append(
                    Effect(
                        type="account",
                        entity_id=account.id,
                        field=field,
                        before=getattr(old, field),
                        after=getattr(account, field),
                    )
                )
    for card in after.cards.values():
        if after.accounts[card.account_id].customer_id != customer_id:
            continue
        old = before.cards.get(card.id)
        for field in ("status", "single_limit_cents", "daily_limit_cents", "controls"):
            value = getattr(card, field)
            previous = getattr(old, field) if old else None
            if hasattr(value, "model_dump"):
                value = value.model_dump()
                previous = previous.model_dump() if previous else None
            if previous != value:
                effects.append(
                    Effect(
                        type="card", entity_id=card.id, field=field, before=previous, after=value
                    )
                )
    return tuple(effects)


def step_result(before, after, step, ctx, effects):
    args = step.args
    created = {}
    if step.tool == "transfer":
        transfer_id = derive(ctx.plan_id, ctx.step_id, "trf")
        txs = [
            t.id
            for t in after.transactions
            if t.transfer_id == transfer_id and t.customer_id == ctx.customer_id
        ]
        result = dict(
            transfer_id=transfer_id,
            transaction_ids=txs,
            from_available_after_cents=after.accounts[args.from_account_id].available_cents,
            to_available_after_cents=after.accounts[args.to_account_id].available_cents
            if args.to_account_id
            else None,
        )
    elif step.tool == "add_payee":
        fp = after.directory["number:" + args.account_no].target_fingerprint
        payee = next(
            p
            for p in after.payees.values()
            if p.customer_id == ctx.customer_id and p.target_fingerprint == fp
        )
        result = dict(payee_id=payee.id, target_fingerprint=fp, no_op=not bool(effects))
        created = {"payee_id": payee.id}
    elif step.tool in ("update_card_limits", "update_card_controls"):
        card = after.cards[args.card_id]
        kind = next((e.operation.action_kind for e in effects if isinstance(e, OperationEvent)), "")
        result = dict(
            card=card_view(after, card).model_dump(mode="json"),
            direction="LOOSEN" if kind.endswith("LOOSEN") else "TIGHTEN",
        )
    elif step.tool == "report_card_lost":
        new = next((c for c in after.cards.values() if c.replaces_id == args.card_id), None)
        result = dict(
            old_card_id=args.card_id,
            status="LOST",
            new_card_id=new.id if new else None,
            new_last4=new.last4 if new else None,
        )
        created = {"card_id": new.id} if new else {}
    elif step.tool == "schedule_transfer":
        from .standing import commitment_30d_cents

        inst = after.obligations.standing_instructions[derive(ctx.plan_id, ctx.step_id, "si")]
        result = dict(
            instruction_id=inst.id,
            next_run_at=inst.next_run_at,
            grant_scope_hash=inst.grant_scope_hash,
            commitment_30d_cents=commitment_30d_cents(inst, after.sim_now),
        )
        created = {"instruction_id": inst.id}
    elif step.tool == "cancel_scheduled":
        result = dict(instruction_id=args.instruction_id, status="DELETED", no_op=not bool(effects))
    elif step.tool == "cancel_subscription":
        inst = after.obligations.standing_instructions[args.instruction_id]
        billing = after.obligations.billings.get(inst.billing_id)
        no_op = not bool(effects)
        monthly = 0 if no_op else (billing.amount_cents if (billing and billing.active) else 0)
        annual = monthly * (12 if (billing and billing.period == "MONTHLY") else 52)
        result = dict(
            instruction_id=args.instruction_id,
            status="DELETED",
            monthly_saving_cents=monthly,
            annual_saving_cents=annual,
            no_op=no_op,
        )
    elif step.tool == "submit_risk_assessment":
        record = next(e.record for e in effects if isinstance(e, InsertAssessment))
        result = dict(
            assessment_id=record.id,
            score=record.score,
            level=int(record.level[1:]),
            assessed_at=record.assessed_at,
            valid_until=record.valid_until,
        )
    elif step.tool == "create_split_request":
        from safebank_contracts.state import FUTURE_EFFECTS

        InsertSplit = FUTURE_EFFECTS["InsertSplit"]
        split_effect = next((e for e in effects if isinstance(e, InsertSplit)), None)
        if split_effect is not None:
            result = split_effect.split.model_dump(mode="json")
            created = {"split_request_id": split_effect.split.id}
        else:
            result = {}
            created = {}
    elif step.tool == "subscribe_product":
        order = next(o for o in after.fund_orders if o.id == derive(ctx.plan_id, ctx.step_id, "ord"))
        result = dict(
            order=order_view(order).model_dump(mode="json"),
            available_after_cents=after.accounts[args.account_id].available_cents,
            pending_debit_after_cents=after.accounts[args.account_id].debits_pending_cents,
            estimated_units=order.units,
        )
        created = {"order_id": order.id}
    elif step.tool == "redeem_product":
        order = next(o for o in after.fund_orders if o.id == derive(ctx.plan_id, ctx.step_id, "ord"))
        holding = next(
            h for h in after.holdings if h.account_id == order.account_id and h.product_id == order.product_id
        )
        result = dict(
            order=order_view(order).model_dump(mode="json"),
            available_units_after=holding.available_units,
            estimated_amount_cents=order.amount_cents,
        )
        created = {"order_id": order.id}
    else:
        result = dict(
            card_id=args.card_id, status=after.cards[args.card_id].status, no_op=not bool(effects)
        )
    verified = WRITE_RESULTS[step.tool].model_validate(result)
    return StepResult(
        step_id=step.step_id,
        tool=step.tool,
        status="EXECUTED",
        created_ids=created,
        result=verified.model_dump(mode="json"),
        effects=to_public_effects(before, after, ctx.customer_id),
    )


@dataclass(frozen=True)
class Simulation:
    state: BankState
    effects: tuple
    results: tuple[StepResult, ...]
    error: Code | None


SUPPORTED_WRITES = {
    "transfer",
    "add_payee",
    "update_card_limits",
    "update_card_controls",
    "freeze_card",
    "unfreeze_card",
    "report_card_lost",
    "schedule_transfer",
    "cancel_scheduled",
    "cancel_subscription",
    "submit_risk_assessment",
    "create_split_request",
    "subscribe_product",
    "redeem_product",
}


def apply_plan(state: BankState, steps, customer_id: str, plan_id: str) -> Simulation:
    original = state
    effects = []
    results = []
    refs = {}
    for step in steps:
        ctx = Context(customer_id, plan_id, step.step_id)
        args = step.args
        if step.tool == "transfer" and isinstance(args.payee_id, EntityRef):
            target = refs.get(args.payee_id.ref)
            if target:
                args = args.__class__(**(args.model_dump() | {"payee_id": target}))
        resolved = step.model_copy(update={"args": args})
        before = state
        if step.tool == "transfer":
            state, changes, errors = apply_transfer(state, args, state.sim_now, ctx)
        elif step.tool == "add_payee":
            state, changes, errors = apply_add_payee(state, args, state.sim_now, ctx)
        elif step.tool == "schedule_transfer":
            state, changes, errors = apply_schedule_transfer(state, args, state.sim_now, ctx)
        elif step.tool == "cancel_scheduled":
            state, changes, errors = apply_cancel_scheduled(state, args, state.sim_now, ctx)
        elif step.tool == "cancel_subscription":
            state, changes, errors = apply_cancel_subscription(state, args, state.sim_now, ctx)
        elif step.tool == "submit_risk_assessment":
            state, changes, errors = apply_risk_assessment(state, args, state.sim_now, ctx)
        elif step.tool == "create_split_request":
            state, changes, errors = apply_create_split_request(state, args, state.sim_now, ctx)
        elif step.tool == "subscribe_product":
            state, changes, errors = apply_subscribe(state, args, state.sim_now, ctx)
        elif step.tool == "redeem_product":
            state, changes, errors = apply_redeem(state, args, state.sim_now, ctx)
        elif step.tool in SUPPORTED_WRITES:
            state, changes, errors = apply_card(state, args, state.sim_now, ctx, step.tool)
        else:
            state, changes, errors = reject(state, ctx, Code.TOOL_NOT_ALLOWED)
        if errors:
            error = errors[0].code
            failed = tuple(
                StepResult(
                    step_id=s.step_id,
                    tool=s.tool,
                    status="FAILED" if s.step_id == step.step_id else "ROLLED_BACK",
                    created_ids={},
                    result={},
                    effects=(),
                    code=error if s.step_id == step.step_id else Code.LINKED_STEP_FAILED,
                )
                for s in steps
            )
            return Simulation(original, (), failed, error)
        effects.extend(changes)
        result = step_result(before, state, resolved, ctx, changes)
        results.append(result)
        if step.tool == "add_payee":
            refs[step.step_id] = result.result["payee_id"]
    return Simulation(state, tuple(effects), tuple(results), None)
