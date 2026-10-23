"""Stage ④: exact server scope matching; LLM intentions never confer permission."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from safebank_bankcore.ids import derive
from safebank_contracts.codes import Code

from ..api.tools import fetch_read
from ..errors import GatewayError
from ..policy.registry import policy
from ..schemas.enums import FindingEffect, ParseStatus, UtteranceStatus
from .context import EvalContext, IntentResult, Resolver, Stage


class IntentStage(Stage):
    name = "intent"

    async def run(self, ctx: EvalContext) -> None:
        if ctx.plan_draft is None:
            return
        ctx.intent = IntentResult(matched=not ctx.has_hard_deny())
        if ctx.has_hard_deny():
            return
        auth = ctx.authorization
        if auth is None or auth.sid != ctx.session_id or auth.customer_id != ctx.customer_id:
            self._reject(ctx, Code.INTENT_UNCLEAR)
            return
        scopes = [
            u.scope
            for u in auth.utterances.values()
            if u.id in auth.utterance_ids
            and u.kind != "context"
            and u.parse_status is ParseStatus.SCOPED
            and auth.statuses.get(u.id) is UtteranceStatus.ACTIVE
            and datetime.fromisoformat(u.expires_at_wall) > ctx.wall_now()
        ]
        if not scopes:
            self._reject(ctx, Code.INTENT_UNCLEAR)
            return
        state = await ctx.bank_state()
        allowed = Counter(kind for scope in scopes for kind in scope.action_kinds)
        actual = Counter(s.action_kind for s in ctx.steps)
        # Scope actions are a capability set for non-transfer repeats. Transfer
        # multiplicity is checked with complete paired triples below.
        repeatable = {
            "TRANSFER_EXTERNAL",
            "TRANSFER_SELF",
            "SCHEDULE_CREATE",
            "MANDATE_CANCEL",
            "SCHEDULE_CANCEL",
        }
        if set(actual) != set(allowed) or any(
            count > allowed[kind] for kind, count in actual.items() if kind not in repeatable
        ):
            self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED)
        checking = [
            a.id
            for a in state.accounts.values()
            if a.customer_id == ctx.customer_id and a.type == "CHECKING"
        ]
        default = checking[0] if len(checking) == 1 else None
        expected = Counter()
        for scope in scopes:
            for transfer in scope.transfers:
                source = transfer.from_account_id or scope.from_account_id or default
                amount = transfer.amount_cents
                template = scope.fields.get("derived_template")
                if amount == 0 and template == "cancelled_monthly_fees":
                    values = {
                        s.args["amount_cents"]
                        for s in ctx.steps
                        if s.tool == "schedule_transfer"
                        and s.args.get("from_account_id") == source
                        and s.args.get("to_account_id") is not None
                        and "int:" + s.args["to_account_id"] == transfer.target_fingerprint
                        and self._derived_verified(ctx, s, template)
                    }
                    if len(values) == 1:
                        amount = values.pop()
                expected[(source, transfer.target_fingerprint, amount)] += 1
        proposed = Counter()
        proposed_schedules = Counter()
        expected_schedules = Counter()
        for scope in scopes:
            if "SCHEDULE_CREATE" not in scope.action_kinds:
                continue
            if any(k in scope.action_kinds for k in ("TRANSFER_EXTERNAL", "TRANSFER_SELF")):
                self._reject(ctx, Code.INTENT_UNCLEAR)
                continue
            for transfer in scope.transfers:
                source = transfer.from_account_id or scope.from_account_id or default
                amount = next(
                    (
                        a
                        for (f, t, a) in expected
                        if f == source and t == transfer.target_fingerprint
                    ),
                    transfer.amount_cents,
                )
                expected_schedules[
                    (
                        source,
                        transfer.target_fingerprint,
                        amount,
                        scope.period,
                        scope.fields.get("first_run_at"),
                        scope.fields.get("end_at"),
                    )
                ] += 1
        new_payees = {}
        for s in ctx.steps:
            if s.tool == "add_payee":
                entry = state.directory.get("number:" + s.args["account_no"])
                if entry:
                    new_payees[derive(ctx.plan_id, s.step_id, "pye")] = entry.target_fingerprint
        for step in ctx.steps:
            args = step.args
            if step.tool in ("transfer", "schedule_transfer"):
                target = (
                    "int:" + args["to_account_id"]
                    if args.get("to_account_id")
                    else state.payees[args["payee_id"]].target_fingerprint
                    if args.get("payee_id") in state.payees
                    else new_payees.get(args.get("payee_id"))
                )
                proposed[(args["from_account_id"], target, args["amount_cents"])] += 1
                if step.tool == "transfer" and args.get("memo") is not None:
                    if not any(scope.fields.get("memo") == args["memo"] for scope in scopes):
                        self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED, step.step_id)
                if step.tool == "schedule_transfer":
                    proposed_schedules[
                        (
                            args["from_account_id"],
                            target,
                            args["amount_cents"],
                            args["period"],
                            args["first_run_at"],
                            args["end_at"],
                        )
                    ] += 1
                    if not any(
                        scope.period == args["period"]
                        and all(scope.fields.get(p) == args[p] for p in ("first_run_at", "end_at"))
                        for scope in scopes
                    ):
                        self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED, step.step_id)
            else:
                matching = [s for s in scopes if step.action_kind in s.action_kinds]
                if not any(self._fields_match(ctx, step, s.fields) for s in matching):
                    self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED, step.step_id)
        if proposed != expected or proposed_schedules != expected_schedules:
            # Exact equality prevents duplicate transfers, mismatched pairings,
            # source-account substitution and silently dropping a requested leg.
            self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED)
        await self._predicates(ctx, scopes, state)

    @staticmethod
    def _fields_match(ctx, step, fields):
        for param in policy(step.tool).sensitive_params:
            value = step.args.get(param)
            if value is None:
                continue
            if param == "instruction_id" and "instruction_ids" in fields:
                if value not in fields["instruction_ids"]:
                    return False
            elif (
                param == "product_id"
                and "product_id" not in fields
                and fields.get("predicates", {}).get("robust_products")
            ):
                continue  # recomputed independently below, never trust these IDs
            elif (
                param == "card_id"
                and "card_id" not in fields
                and fields.get("predicates", {}).get("suspicious_cards")
            ):
                continue
            elif (
                param == "instruction_id"
                and "instruction_id" not in fields
                and fields.get("predicates", {}).get("idle_subscriptions")
            ):
                continue
            elif param == "amount_cents" and fields.get("derived_template"):
                if not IntentStage._derived_verified(ctx, step, fields["derived_template"]):
                    return False
            elif type(fields.get(param)) is not type(value) or fields[param] != value:
                return False
        return True

    @staticmethod
    def _derived_verified(ctx, step, template):
        evidence = (
            ctx.provenance.arg_sources.get(step.step_id, {}).get("amount_cents", {})
            if ctx.provenance
            else {}
        )
        source = evidence.get("source") or {}
        return evidence.get("trust") == "derived" and source.get("template") == template

    async def _predicates(self, ctx, scopes, state):
        names = {p for scope in scopes for p in scope.fields.get("predicates", {})}
        if "idle_subscriptions" in names:
            data = await self._read(ctx, state, "detect_subscriptions", {"lookback_days": 180})
            allowed = {
                x["instruction_id"]
                for x in data["items"]
                if x["is_idle"] and x.get("instruction_id")
            }
            actual = {
                s.args["instruction_id"] for s in ctx.steps if s.tool == "cancel_subscription"
            }
            if actual != allowed:
                self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED)
        if "robust_products" in names:
            customer = state.customers.get(ctx.customer_id)
            if (
                customer is None
                or customer.risk_level is None
                or not customer.risk_valid_until
                or datetime.fromisoformat(customer.risk_valid_until).date() < state.sim_now.date()
            ):
                ctx.add(Code.ASSESSMENT_MISSING, FindingEffect.DENY)
                ctx.intent.matched = False
            else:
                allowed = {
                    p.id
                    for p in state.products
                    if p.risk_label in ("稳健", "平衡") and p.risk_level <= customer.risk_level
                }
                if any(
                    s.args["product_id"] not in allowed
                    for s in ctx.steps
                    if s.tool == "subscribe_product"
                ):
                    self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED)
        if "suspicious_cards" in names:
            first = state.sim_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            start = (first - timedelta(days=1)).replace(day=1)
            data = await self._read(
                ctx,
                state,
                "detect_anomalies",
                {
                    "from_at": start.isoformat(),
                    "to_at": state.sim_now.isoformat(),
                    "account_id": None,
                },
            )
            allowed = {
                x["transaction"]["card_id"]
                for x in data["items"]
                if x["transaction"].get("card_id")
            }
            actual = {s.args["card_id"] for s in ctx.steps if s.tool == "freeze_card"}
            if not allowed:
                self._reject(ctx, Code.INTENT_UNCLEAR)
            elif len(allowed) != 1 or actual != allowed:
                self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED)
        # Explicit list cancellation also uses equality: no partial completion.
        for scope in scopes:
            if "instruction_ids" in scope.fields:
                actual = {
                    s.args["instruction_id"] for s in ctx.steps if s.tool == "cancel_subscription"
                }
                if actual != set(scope.fields["instruction_ids"]):
                    self._reject(ctx, Code.INTENT_SCOPE_EXCEEDED)

    @staticmethod
    async def _read(ctx, state, tool, args):
        if ctx.l3 is None:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
        return (await fetch_read(ctx.l3, ctx.customer_id, tool, args, expected=state)).data

    @staticmethod
    def _reject(ctx, code, step_id=None):
        ctx.intent.matched = False
        ctx.intent.notes = (*ctx.intent.notes, code.value)
        ctx.add(code, FindingEffect.REPLAN, step_id=step_id, resolver=Resolver.USER)


stage = IntentStage()
