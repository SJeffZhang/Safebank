"""Stage ③: independently verify every sensitive parameter's evidence."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml
from safebank_bankcore import projected_shortfall
from safebank_contracts.codes import Code
from safebank_contracts.tools import READ_ARGS

from ..errors import GatewayError
from ..policy.registry import policy
from ..schemas.enums import FindingEffect
from ..taint.authorization import authorization_evidence
from ..taint.records import load_records, pointer
from ..taint.text import matches, normalize, numbers
from .context import EvalContext, ProvenanceResult, Resolver, Stage

_PATTERNS = tuple(
    re.compile(p, re.I)
    for p in yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "policies/injection_patterns.yaml").read_text()
    )["patterns"]
)
_MONEY_PATTERN = re.compile(
    r"[0-9]+(?:\.[0-9]{1,2})?(?:元|人民币|CNY|RMB)"
    r"|(?:人民币|CNY|RMB)[0-9]+(?:\.[0-9]{1,2})?"
    r"|[¥￥](?=[0-9])|[0-9][¥￥]",
    re.I,
)
_MISSING = object()


def _read(source, records, state, now, customer_id):
    row = records.get(source.ref)
    if (
        row is None
        or row.get("tool") not in READ_ARGS
        or customer_id not in row["versions"]
        or any(state.versions.get(k) != v for k, v in row["versions"].items())
    ):
        return _MISSING, "unknown"
    observed = row.get("observed_at")
    if isinstance(observed, str):
        observed = datetime.fromisoformat(observed)
    if observed != state.sim_now:
        return _MISSING, "unknown"
    created = row.get("created_at_wall")
    if isinstance(created, str):
        created = datetime.fromisoformat(created)
    if created is None or created > now:
        return _MISSING, "unknown"
    try:
        value = pointer(row["response"], source.pointer)
    except (ValueError, KeyError, IndexError, TypeError):
        return _MISSING, "unknown"
    untrusted = row["untrusted_paths"]
    tainted = any(
        source.pointer == p
        or source.pointer.startswith(p + "/")
        or p.startswith(source.pointer + "/")
        or source.pointer == ""
        for p in untrusted
    )
    return value, "untrusted" if tainted else "trusted"


def _copied_untrusted(value, records):
    for row in records.values():
        for path in row["untrusted_paths"]:
            try:
                candidate = pointer(row["response"], path)
            except (ValueError, KeyError, IndexError, TypeError):
                continue
            if type(value) is type(candidate) and value == candidate:
                return True
            if isinstance(value, str) and isinstance(candidate, str):
                if normalize(value) in numbers(candidate):
                    return True
    return False


class ProvenanceStage(Stage):
    name = "provenance"

    def __init__(self, *, reserve: Callable | None = None) -> None:
        # Production uses the sole bankcore pure function. ``None`` means "no
        # test override", never an unavailable dependency: incomplete obligation
        # data fails closed inside the projection itself.
        self.reserve = projected_shortfall if reserve is None else reserve

    async def run(self, ctx: EvalContext) -> None:
        if ctx.plan_draft is None:
            return
        ctx.provenance = ProvenanceResult()
        if ctx.has_hard_deny():
            return
        state = await ctx.bank_state()
        records = await load_records(ctx.conn, ctx.session_id, ctx.customer_id)
        evidence = await authorization_evidence(ctx)
        saved = evidence.sources
        resolved = {s.step_id: s for s in ctx.steps}
        verified = {}
        refs = set()
        previous = {}
        for step in ctx.plan_draft.steps:
            results = {}
            declarations = {item.param: item.source for item in step.arg_sources}
            args = (
                resolved[step.step_id].args
                if step.step_id in resolved
                else step.args.model_dump(mode="json")
            )
            for param in policy(step.tool).sensitive_params:
                value = args.get(param)
                if value is None:
                    results[param] = {"trust": "null"}
                    continue
                source = declarations.get(param)
                trust = "unknown"
                if source is not None:
                    if source.kind == "utterance":
                        # Only persisted nodes reachable from a live, credential-bound
                        # root can supply text. Parents never add write scopes.
                        row = evidence.utterances.get(source.ref)
                        text = row["text"] if row is not None else None
                        valid = row is not None and row["kind"] == "typed"
                        start, end = source.span
                        if (
                            valid
                            and text is not None
                            and end <= len(text)
                            and matches(value, text[start:end], param, state, ctx.customer_id)
                        ):
                            trust = "user"
                    elif source.kind == "read":
                        candidate, trust = _read(
                            source, records, state, ctx.wall_now(), ctx.customer_id
                        )
                        refs.add(source.ref)
                        # Taint propagates even when the declared value differs.
                        if trust != "untrusted" and (
                            type(candidate) is not type(value) or candidate != value
                        ):
                            trust = "unknown"
                        if param.endswith("_cents") and trust == "trusted":
                            trust = "unknown"  # balances are data, never amount authorization
                    elif source.kind in ("hint", "action"):
                        item = saved.get(source.ref)
                        if item is not None and item["type"] == source.kind:
                            fields = (
                                item["normalized_fields"]
                                if source.kind == "action"
                                else item["scope_json"].get("fields", {})
                            )
                            candidates = [fields.get(param)]
                            if source.kind == "hint":
                                scope = item["scope_json"]
                                for transfer in scope.get("transfers", []):
                                    if param in ("amount_cents", "from_account_id"):
                                        candidates.append(transfer.get(param) or scope.get(param))
                                    if param == "payee_id" and value in state.payees:
                                        if state.payees[value].target_fingerprint == transfer.get(
                                            "target_fingerprint"
                                        ):
                                            candidates.append(value)
                                    if param == "to_account_id" and (
                                        transfer.get("target_fingerprint") == "int:" + str(value)
                                    ):
                                        candidates.append(value)
                            if any(
                                type(candidate) is type(value) and candidate == value
                                for candidate in candidates
                            ):
                                trust = "user"
                    elif source.kind == "step":
                        creator = previous.get(source.ref)
                        original = getattr(step.args, param, None)
                        if (
                            creator is not None
                            and creator.tool == "add_payee"
                            and getattr(original, "ref", None) == source.ref
                        ):
                            inherited = verified.get(source.ref, {}).values()
                            trust = (
                                "untrusted"
                                if any(v["trust"] == "untrusted" for v in inherited)
                                else "trusted"
                                if inherited
                                and all(v["trust"] in ("user", "null") for v in inherited)
                                else "unknown"
                            )
                    elif source.kind == "derived":
                        trust = self._derived(ctx, step, source, value, previous, records, state)
                if param in ("memo", "alias") and source is not None:
                    if source.kind not in ("utterance", "action"):
                        trust = "untrusted"
                if trust != "user" and _copied_untrusted(value, records):
                    trust = "untrusted"
                results[param] = {
                    "trust": trust,
                    "source": source.model_dump(mode="json") if source else None,
                }
                if trust == "untrusted":
                    ctx.add(Code.TAINTED_SENSITIVE_ARG, FindingEffect.DENY, step_id=step.step_id)
                elif trust == "unknown":
                    ctx.add(
                        Code.UNKNOWN_SENSITIVE_ARG,
                        FindingEffect.REPLAN,
                        step_id=step.step_id,
                        resolver=Resolver.USER,
                    )
                if (
                    param in ("memo", "alias")
                    and isinstance(value, str)
                    and (numbers(value) or _MONEY_PATTERN.search(normalize(value)))
                ):
                    ctx.add(
                        Code.MEMO_SENSITIVE_CONTENT,
                        FindingEffect.RISK_YELLOW,
                        step_id=step.step_id,
                    )
            verified[step.step_id] = results
            previous[step.step_id] = step
        ctx.provenance.arg_sources = verified
        ctx.provenance.read_refs = tuple(sorted(refs))
        self._injection(ctx, records, state)

    def _derived(self, ctx, step, source, value, previous, records, state):
        leaf_reads = []
        leaf_steps = set()
        for leaf in source.inputs:
            if leaf.kind == "read":
                _, trust = _read(leaf, records, state, ctx.wall_now(), ctx.customer_id)
                if trust != "trusted":
                    return trust
                leaf_reads.append(leaf)
            elif leaf.kind == "step" and leaf.ref in previous:
                leaf_steps.add(leaf.ref)
            else:
                return "unknown"
        if source.template == "available_less_7d_charges":
            if step.tool != "subscribe_product" or not leaf_reads:
                ctx.add(
                    Code.INTENT_SCOPE_EXCEEDED,
                    FindingEffect.REPLAN,
                    step_id=step.step_id,
                    resolver=Resolver.USER,
                )
                return "unknown"
            if ctx.has_hard_deny():
                # An earlier step or parameter already produced a hard DENY; do not
                # let a reserve dependency failure mask it. The DENY stays in findings.
                return "unknown"
            expected = self._reserve_max_outflow(state, step.args.account_id)
            return "derived" if type(expected) is int and expected == value else "unknown"
        cancellations = {sid: s for sid, s in previous.items() if s.tool == "cancel_subscription"}
        selected = {s.args.instruction_id for s in cancellations.values()}
        subscriptions = [s for s in state.subscriptions if s.instruction_id in selected]
        if (
            step.tool != "schedule_transfer"
            or step.args.to_account_id is None
            or step.args.period != "MONTHLY"
            or leaf_steps != set(cancellations)
            or not leaf_steps
            or not leaf_reads
            or len(cancellations) != len(selected)
            or len(subscriptions) != len(selected)
            or not self._is_own_savings(state, step.args.to_account_id, ctx.customer_id)
            or any(
                s.period != "MONTHLY"
                or s.status != "ACTIVE"
                or s.account_id != step.args.from_account_id
                for s in subscriptions
            )
        ):
            ctx.add(
                Code.INTENT_SCOPE_EXCEEDED,
                FindingEffect.REPLAN,
                step_id=step.step_id,
                resolver=Resolver.USER,
            )
            return "unknown"
        expected = sum(s.amount_cents for s in subscriptions)
        return "derived" if type(value) is int and value == expected else "unknown"

    def _reserve_max_outflow(self, state, account_id):
        """Project with the sole bankcore function; incomplete data fails closed."""
        try:
            report = self.reserve(state, days=7)
        except ValueError as exc:
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE,
                503,
                message="obligation reserve is unavailable or incomplete",
            ) from exc
        projection = report.accounts.get(account_id)
        if projection is None:
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE,
                503,
                message="reserve target account is not present in the projection",
            )
        return projection.max_outflow_cents

    @staticmethod
    def _is_own_savings(state, account_id, customer_id):
        account = state.accounts.get(account_id)
        return (
            account is not None and account.customer_id == customer_id and account.type == "SAVINGS"
        )

    @staticmethod
    def _injection(ctx, records, state):
        texts = []
        for row in records.values():
            for path in row["untrusted_paths"]:
                try:
                    item = pointer(row["response"], path)
                    if isinstance(item, str):
                        texts.append(item)
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
        texts.extend(str(v) for step in ctx.steps for v in step.args.values() if isinstance(v, str))
        echoed = bool(
            ctx.authorization
            and any(u.echoes_untrusted for u in ctx.authorization.utterances.values())
        )
        if echoed or any(p.search(text) for p in _PATTERNS for text in texts):
            ctx.add(Code.INJECTION_SUSPECTED, FindingEffect.RISK_RED)
        contaminated_numbers = set()
        for row in records.values():
            for path in row["untrusted_paths"]:
                # Value-level copy detection deliberately covers free text only.
                if path.rsplit("/", 1)[-1] in (
                    "memo",
                    "counterparty",
                    "merchant_name",
                    "risk_disclosure",
                    "target_display",
                ):
                    try:
                        text = pointer(row["response"], path)
                        if isinstance(text, str):
                            contaminated_numbers.update(numbers(text))
                    except (ValueError, KeyError, IndexError, TypeError):
                        pass
        new_targets = set()
        for step in ctx.steps:
            if step.tool == "add_payee":
                number = normalize(step.args["account_no"])
                entry = state.directory.get("number:" + number)
                existing = entry and any(
                    p.customer_id == ctx.customer_id
                    and p.target_fingerprint == entry.target_fingerprint
                    for p in state.payees.values()
                )
                if not existing and (echoed or number in contaminated_numbers):
                    ctx.add(Code.INJECTION_SUSPECTED, FindingEffect.RISK_RED, step_id=step.step_id)
                    if entry is not None:
                        new_targets.add(entry.target_fingerprint)
        for step in ctx.steps:
            if not step.outflow:
                continue
            target = state.payees.get(step.args.get("payee_id"))
            fingerprint = target.target_fingerprint if target else None
            if target is None:
                # New payees have deterministic IDs; connect them through creator steps.
                from safebank_bankcore.ids import derive

                for creator in ctx.steps:
                    if creator.tool == "add_payee" and derive(
                        ctx.plan_id, creator.step_id, "pye"
                    ) == step.args.get("payee_id"):
                        entry = state.directory.get("number:" + creator.args["account_no"])
                        fingerprint = entry.target_fingerprint if entry else None
            if fingerprint in new_targets:
                ctx.add(
                    Code.TAINTED_SENSITIVE_ARG,
                    FindingEffect.DENY,
                    step_id=step.step_id,
                    evidence_refs=(fingerprint,),
                )


stage = ProvenanceStage()
