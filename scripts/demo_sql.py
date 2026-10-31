"""Pure SELECT snapshot plans and strict default six-scene SQL payload validators.

No I/O, provider, subprocess, DB connection or credential access. The trusted
coordinator executes these fixed SELECTs before/after each case, binds actual
manifest metadata, and calls verify_case. This is an attestation helper, not a
signature, authorization service, full43 verifier or simulated-clock verifier.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo


class OracleError(ValueError):
    """Missing, mismatched or financially incorrect evidence (no payload echo)."""


SHANGHAI = ZoneInfo("Asia/Shanghai")
CASES = {
    "01": "给王五转100元",
    "16": "给我出一份9月的账单报告",
    "27": "哪些订阅是闲置没用的",
    "34": "赎回安心货币1000份",
    "36": "找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户",
    "23": "我的卡丢了，帮我挂失",
}
# Fixed trusted table/column expressions only. Names never come from callers.
BANK_FIELDS = {
    "state_versions": ("state_versions", "customer_id AS id,customer_id,version", "customer_id"),
    "accounts": (
        "accounts",
        (
            "id,customer_id,type,current_card_id,flags,single_limit_cents,"
            "daily_limit_cents,debits_posted_cents,credits_posted_cents,"
            "debits_pending_cents,credits_pending_cents"
        ),
        "id",
    ),
    "cards": (
        "cards",
        (
            "id,account_id,last4,status,single_limit_cents,daily_limit_cents,"
            "max_single_limit_cents,max_daily_limit_cents,controls,replaces_id,"
            "created_at"
        ),
        "id",
    ),
    "payees": ("payees", "id,customer_id,target_account_id,target_fingerprint", "id"),
    "transactions": (
        "transactions",
        (
            "id,transfer_id,customer_id,account_id,direction,kind,phase,status,"
            "amount_cents,balance_after_cents,available_after_cents,category,"
            "merchant_id,payee_id,card_id,channel,occurred_at"
        ),
        "id",
    ),
    "transfers": (
        "transfers",
        (
            "id,debit_account_id,credit_account_id,amount_cents,code,phase,"
            "pending_id,reverses_id,plan_id,event_id,step_id,leg,occurred_at,"
            "expires_at"
        ),
        "id",
    ),
    "instructions": (
        "standing_instructions",
        (
            "id,customer_id,kind,amount_type,status,from_account_id,payee_id,"
            "to_account_id,merchant_id,billing_id,amount_cents,max_amount_cents,"
            "recurrence,period,anchor_day,weekday,time_of_day,next_run_at,end_at,"
            "priority,grant_scope_hash,source_plan_id,created_at"
        ),
        "id",
    ),
    "billings": (
        "merchant_billing",
        (
            "id,customer_id,account_id,merchant_id,amount_cents,period,anchor_day,"
            "weekday,time_of_day,next_due_at,active,created_at"
        ),
        "id",
    ),
    "merchants": ("merchants", "id,mcc,category,usage_expected", "id"),
    "holdings": (
        "holdings",
        (
            "customer_id,account_id,product_id,units::text AS units,"
            "reserved_units::text AS reserved_units,cost_cents,reserved_cost_cents"
        ),
        "account_id,product_id",
    ),
    "orders": (
        "fund_orders",
        (
            "id,customer_id,account_id,product_id,type,status,amount_cents,"
            "units::text AS units,nav::text AS nav,reserved_cost_cents,"
            "pending_transfer_id,plan_id,step_id,trade_date,confirm_at,settle_at,"
            "expires_at,failure_code"
        ),
        "id",
    ),
    "executions": (
        "plan_executions",
        (
            "customer_id,plan_id,plan_hash,result_json->>'status' AS status,"
            "authorization_evidence AS evidence"
        ),
        "customer_id,plan_id",
    ),
    "operations": (
        "operation_events",
        (
            "event_id,customer_id,plan_id,step_id,action_kind,target_fingerprint,"
            "amount_cents,occurred_at"
        ),
        "event_id",
    ),
    "event_jobs": (
        "event_jobs",
        "id,kind,rank,due_at,payload_hash,source_id,period_key,status,attempt_count",
        "id",
    ),
    "event_executions": (
        "event_executions",
        "event_id,status,authorization_evidence AS evidence",
        "event_id",
    ),
    "instruction_runs": (
        "instruction_runs",
        (
            "id,customer_id,account_id,kind,instruction_id,billing_id,due_at,"
            "event_id,status,code,amount_cents,transfer_id"
        ),
        "id",
    ),
    "split_requests": (
        "split_requests",
        "id,customer_id,account_id,total_cents,include_self,self_share_cents,created_at",
        "id",
    ),
    "split_items": (
        "split_items",
        (
            "id,request_id,customer_id,account_id,payee_id,amount_cents,status,"
            "paid_at,payment_event_id"
        ),
        "id",
    ),
    "risk_assessments": (
        "risk_assessments",
        "id,customer_id,questionnaire_id,score,level,assessed_at,valid_until,plan_id",
        "id",
    ),
}


def _require(ok, label):
    if not ok:
        raise OracleError(label)


def _dt(value):
    _require(isinstance(value, str), "timestamp must be a string")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise OracleError("invalid timestamp") from None
    _require(
        result.tzinfo is not None and result.utcoffset() is not None, "timestamp lacks timezone"
    )
    return result


def _int(value):
    _require(type(value) is int, "money/count must be a strict integer")
    return value


def _dec(value):
    _require(isinstance(value, str), "shares/NAV must be an exact string")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise OracleError("invalid decimal") from None
    _require(result.is_finite(), "non-finite decimal")
    return result


def _array_query(table, columns, order, where=""):
    return (
        "COALESCE((SELECT jsonb_agg(to_jsonb(s)) FROM (SELECT "
        + columns
        + " FROM "
        + table
        + where
        + " ORDER BY "
        + order
        + ") s),'[]'::jsonb)"
    )


def bank_snapshot_query():
    """The exact bank-before SELECT; no future case sid or credentials needed."""
    entries = [
        "'database', current_database()",
        (
            "'clock', (SELECT to_jsonb(c) FROM (SELECT sim_now,version,"
            "dataset_revision FROM clock WHERE id=1) c)"
        ),
    ]
    entries += [f"'{name}', " + _array_query(*spec) for name, spec in BANK_FIELDS.items()]
    return "SELECT jsonb_build_object(\n" + ",\n".join(entries) + ")"


def build_queries(session_id):
    """Return two standalone SELECTs. Only a whitelist-validated sid is embedded.

    Execute bank before the case. After its trace yields a sid, execute both
    against current generation DBs. Gateway query is scoped to that exact sid.
    No account numbers, free-form bank memos, auth tokens, OTP values/digests or
    ciphertexts are selected. Read responses/user synthetic utterances remain
    data: coordinator should keep raw snapshots private and review before export.
    """
    _require(
        isinstance(session_id, str) and re.fullmatch(r"ses_[A-Za-z0-9_-]{1,128}", session_id),
        "unsafe session identifier",
    )
    bank_sql = bank_snapshot_query()
    sid = "'" + session_id + "'"
    plan_filter = f" WHERE sid={sid}"
    plan_ids = f"(SELECT id FROM plans WHERE sid={sid})"
    gateway_specs = {
        "plans": (
            "plans",
            "id,customer_id,sid,origin,plan_hash,status,plan_draft,"
            "utterance_ids,scope_ids,authorization_epoch",
            "id",
            plan_filter,
        ),
        "approvals": (
            "approvals a JOIN evaluations e ON e.id=a.evaluation_id",
            (
                "a.id,a.plan_id,a.evaluation_id,a.customer_id,a.plan_hash,a.view_hash,a.status,"
                "a.risk_ack,a.confirmed_at_wall,e.risk"
            ),
            "a.id",
            f" WHERE a.plan_id IN {plan_ids}",
        ),
        "evaluations": (
            "evaluations",
            "id,plan_id,risk,decision",
            "id",
            f" WHERE plan_id IN {plan_ids}",
        ),
        "audits": (
            "audit_events",
            (
                "id,plan_id,event_type,payload->>'approval_id' AS approval_id,"
                "payload->>'view_hash' AS view_hash,"
                "payload->'otp_verified' AS otp_verified"
            ),
            "id",
            f" WHERE plan_id IN {plan_ids}",
        ),
        "reads": (
            "read_records",
            "id,sid,tool,canonical_args AS args,response AS data,untrusted_paths,"
            "observed_at,created_at_wall,versions",
            "id",
            plan_filter,
        ),
        "sessions": (
            "sessions",
            "id,customer_id,authorization_epoch,revoked_at_wall",
            "id",
            f" WHERE id={sid}",
        ),
        "claims": (
            "authorization_claims",
            "scope_id AS id,scope_id,plan_id,status",
            "scope_id",
            f" WHERE plan_id IN {plan_ids}",
        ),
        "utterances": (
            "utterances",
            "id,sid,kind,encode(sha256(convert_to(coalesce(text,''),'UTF8')),'hex') AS text_sha256,"
            "status,parse_status,authorization_epoch,consumed_by,source_id,parent_ids,"
            "created_at_wall,expires_at_wall",
            "id",
            plan_filter,
        ),
    }
    gateway_sql = (
        "SELECT jsonb_build_object('database',current_database(),\n"
        + ",\n".join(f"'{name}', " + _array_query(*spec) for name, spec in gateway_specs.items())
        + ")"
    )
    return {"bank": bank_sql, "gateway": gateway_sql}


def _key(name, row):
    if name == "holdings":
        return row.get("account_id"), row.get("product_id")
    if name == "executions":
        return row.get("customer_id"), row.get("plan_id")
    if name in ("operations", "event_executions"):
        return row.get("event_id")
    return row.get("id")


def _index(name, rows):
    _require(isinstance(rows, list), "missing table/list: " + name)
    out = {}
    for row in rows:
        _require(isinstance(row, dict), "invalid table row: " + name)
        key = _key(name, row)
        _require(
            key is not None and (not isinstance(key, tuple) or all(key)), "missing row id: " + name
        )
        _require(key not in out, "duplicate row id: " + name)
        out[key] = row
    return out


def _new(before, after, name):
    prior, current = _index(name, before[name]), _index(name, after[name])
    _require(
        all(current.get(k) == value for k, value in prior.items()),
        "append-only rows changed: " + name,
    )
    return [v for k, v in current.items() if k not in prior]


def _unchanged(before, after, except_tables=()):
    for name in BANK_FIELDS:
        if name not in except_tables:
            _require(
                _index(name, before[name]) == _index(name, after[name]),
                "unexpected bank change: " + name,
            )


def _check_bank(bank, generation):
    _require(
        isinstance(bank, dict) and bank.get("database") == "bank_" + generation,
        "wrong bank generation",
    )
    clock = bank.get("clock")
    _require(
        isinstance(clock, dict) and clock.get("dataset_revision") == "l1_seed_v1.0",
        "wrong or missing seed",
    )
    _dt(clock.get("sim_now"))
    _int(clock.get("version"))
    for name in BANK_FIELDS:
        for row in _index(name, bank.get(name)).values():
            for field, value in row.items():
                if field.endswith("_cents") and value is not None:
                    _require(_int(value) >= 0, "negative/invalid financial amount")
    rows = bank["accounts"]
    _require(rows, "empty ledger")
    for row in rows:
        for name in (
            "debits_posted_cents",
            "credits_posted_cents",
            "debits_pending_cents",
            "credits_pending_cents",
        ):
            _require(_int(row.get(name)) >= 0, "negative ledger counter")
        if row.get("customer_id") is not None:
            _require(
                row["debits_posted_cents"] + row["debits_pending_cents"]
                <= row["credits_posted_cents"],
                "customer balance violation",
            )
    for debit, credit in [
        ("debits_posted_cents", "credits_posted_cents"),
        ("debits_pending_cents", "credits_pending_cents"),
    ]:
        _require(
            sum(r[debit] for r in rows) == sum(r[credit] for r in rows), "ledger is unbalanced"
        )


def _customer_txs(bank, customer="cus_zs"):
    accounts = _index("accounts", bank["accounts"])
    return [
        t
        for t in bank["transactions"]
        if accounts.get(t.get("account_id"), {}).get("customer_id") == customer
    ]


def bill_oracle(bank):
    """Independent integer/Decimal arithmetic over actual raw SQL history.

    Exact Shanghai September2026 plus August reference, fixed seed assertions.
    Recomputes four anomaly predicates using only prior CARD_SPEND/POSTED rows.
    It intentionally does not import/use the bank's analytics implementation.
    """
    txs = _customer_txs(bank)
    totals = {"2026-09": 0, "2026-08": 0}
    category = defaultdict(lambda: {"current": 0, "previous": 0})
    allowed = {"餐饮", "交通", "购物", "娱乐", "生活服务", "数码", "健身", "其他"}
    for tx in txs:
        amount = _int(tx.get("amount_cents"))
        if tx.get("status") != "POSTED" or tx.get("kind") not in {"CARD_SPEND", "MANDATE"}:
            continue
        month = _dt(tx["occurred_at"]).astimezone(SHANGHAI).strftime("%Y-%m")
        if month not in totals:
            continue
        totals[month] += amount
        cat = tx.get("category") if tx.get("category") in allowed else "其他"
        category[cat]["current" if month == "2026-09" else "previous"] += amount
    current, previous = totals["2026-09"], totals["2026-08"]
    _require((current, previous) == (644800, 133190), "seed September/August totals differ")

    def rounded(n, d):
        return int((Decimal(n) / Decimal(d)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))

    categories = {
        k: dict(
            amount_cents=v["current"],
            previous_amount_cents=v["previous"],
            share_bp=rounded(v["current"] * 10000, current),
        )
        for k, v in category.items()
    }
    history = sorted(
        (t for t in txs if t.get("kind") == "CARD_SPEND" and t.get("status") == "POSTED"),
        key=lambda t: (_dt(t["occurred_at"]), t["id"]),
    )
    anomalies = []
    for index, tx in enumerate(history):
        at = _dt(tx["occurred_at"])
        if at.astimezone(SHANGHAI).strftime("%Y-%m") != "2026-09":
            continue
        # Exact prior instants, never later or equal-time rows.
        prior = [p for p in history[:index] if _dt(p["occurred_at"]) < at]
        amount = _int(tx["amount_cents"])
        codes = []
        if at.astimezone(SHANGHAI).hour <= 4 and amount > 50000:
            codes.append("ANOMALY_NIGHT_LARGE")
        matches = [
            p
            for p in prior
            if tx.get("card_id") is not None
            and tx.get("merchant_id") is not None
            and (p.get("card_id"), p.get("merchant_id"), p["amount_cents"])
            == (tx["card_id"], tx["merchant_id"], amount)
        ]
        if matches and at - _dt(matches[-1]["occurred_at"]) <= timedelta(minutes=5):
            codes.append("ANOMALY_DUPLICATE")
        recent = [p for p in prior if _dt(p["occurred_at"]) >= at - timedelta(days=90)]
        if (
            tx.get("merchant_id") is not None
            and amount > 100000
            and not any(p.get("merchant_id") == tx["merchant_id"] for p in recent)
        ):
            codes.append("ANOMALY_NEW_MERCHANT_LARGE")
        same_cat = [
            Decimal(p["amount_cents"])
            for p in recent
            if tx.get("category") and p.get("category") == tx["category"]
        ]
        if len(same_cat) >= 10:
            mean = sum(same_cat) / Decimal(len(same_cat))
            sigma = (sum((v - mean) ** 2 for v in same_cat) / Decimal(len(same_cat))).sqrt()
            if amount > int((mean + 3 * sigma).to_integral_value(rounding=ROUND_HALF_UP)):
                codes.append("ANOMALY_CATEGORY_OUTLIER")
        if codes:
            anomalies.append(
                dict(id=tx["id"], amount_cents=amount, occurred_at=tx["occurred_at"], codes=codes)
            )
    _require(
        {x["id"]: x["amount_cents"] for x in anomalies}
        == {"txn_zs_014": 128000, "txn_zs_016": 26800, "txn_zs_017": 349900},
        "seed anomaly facts differ",
    )
    return dict(
        total_cents=current,
        previous_total_cents=previous,
        mom_change_cents=current - previous,
        mom_change_bp=rounded((current - previous) * 10000, previous),
        categories=categories,
        anomalies=anomalies,
    )


def idle_oracle(bank):
    """Compute active idle MONTHLY mandates from raw charge/use history.

    Gateway predicate uses90-day recurring history; idle use windows are (now-60d,
    now] and (now-120d,now-60d]. Only usage_expected=true qualifies. Financial
    amounts use integers, variance compares cross-products, median floors halves.
    """
    now = _dt(bank["clock"]["sim_now"]).astimezone(SHANGHAI)
    merchants = _index("merchants", bank["merchants"])
    billings = _index("billings", bank["billings"])
    txs = _customer_txs(bank)
    out = {}
    for inst in bank["instructions"]:
        if (
            inst.get("customer_id") != "cus_zs"
            or inst.get("kind") != "MERCHANT_MANDATE"
            or inst.get("status") != "ACTIVE"
        ):
            continue
        merchant = inst.get("merchant_id")
        billing = billings.get(inst.get("billing_id"))
        if (
            not merchants.get(merchant, {}).get("usage_expected")
            or not billing
            or billing.get("active") is not True
        ):
            continue
        charges = sorted(
            (
                t
                for t in txs
                if t.get("merchant_id") == merchant
                and t.get("kind") == "MANDATE"
                and t.get("status") == "POSTED"
                and now - timedelta(days=90) <= _dt(t["occurred_at"]) < now
            ),
            key=lambda t: _dt(t["occurred_at"]),
        )
        if len(charges) < 3:
            continue
        intervals = [
            (
                _dt(b["occurred_at"]).astimezone(SHANGHAI)
                - _dt(a["occurred_at"]).astimezone(SHANGHAI)
            ).days
            for a, b in zip(charges, charges[1:], strict=False)
        ]
        if not all(28 <= days <= 31 for days in intervals):
            continue
        amounts = sorted(_int(t["amount_cents"]) for t in charges)
        typical = (amounts[(len(amounts) - 1) // 2] + amounts[len(amounts) // 2]) // 2
        if not typical or any(abs(a - typical) * 10000 > typical * 500 for a in amounts):
            continue
        uses = [
            _dt(t["occurred_at"])
            for t in txs
            if t.get("merchant_id") == merchant
            and t.get("status") == "POSTED"
            and t.get("kind") != "MANDATE"
        ]
        recent = any(now - timedelta(days=60) < t <= now for t in uses)
        previous = any(now - timedelta(days=120) < t <= now - timedelta(days=60) for t in uses)
        if not recent and previous:
            _require(
                billing.get("period") == "MONTHLY",
                "default core idle oracle only supports monthly charges",
            )
            out[inst["id"]] = _int(billing["amount_cents"])
    return out


def _one(rows, label):
    _require(isinstance(rows, list) and len(rows) == 1, "expected exactly one " + label)
    return rows[0]


def _execute(before, after, case_id, risk):
    bank, gw = after["bank"], after["gateway"]
    plan = _one(gw["plans"], "session plan")
    customer = "cus_ls" if case_id in {"23", "34"} else "cus_zs"
    _require(
        plan.get("customer_id") == customer
        and plan.get("sid") == after["meta"]["session_id"]
        and plan.get("status") == "EXECUTED"
        and plan.get("origin") == "agent",
        "wrong plan identity/state",
    )
    execution = _one(_new(before["bank"], bank, "executions"), "committed execution")
    evidence = execution.get("evidence", {})
    _require(
        execution.get("plan_id") == plan["id"]
        and execution.get("customer_id") == customer
        and execution.get("status") == "EXECUTED"
        and execution.get("plan_hash") == plan["plan_hash"],
        "wrong committed execution",
    )
    approval = _one(gw["approvals"], "approval")
    _require(
        approval.get("plan_id") == plan["id"]
        and approval.get("customer_id") == customer
        and approval.get("status") == "USED"
        and approval.get("risk") == risk
        and approval.get("risk_ack") is True
        and approval.get("confirmed_at_wall")
        and approval.get("plan_hash") == plan["plan_hash"],
        "approval not bound/used",
    )
    _require(
        evidence.get("type") == "INTERACTIVE"
        and evidence.get("origin") == "agent"
        and evidence.get("risk") == risk
        and evidence.get("approval_id") == approval["id"]
        and evidence.get("plan_hash") == plan["plan_hash"]
        and evidence.get("scope_ids")
        and evidence.get("utterance_ids")
        and evidence.get("confirmed_at_wall")
        and evidence.get("confirmed_view_hash")
        == evidence.get("final_view_hash")
        == approval.get("view_hash"),
        "wrong execution authorization evidence",
    )
    evaluations = {e["id"]: e for e in gw["evaluations"]}
    _require(
        evidence.get("approved_evaluation_id") == approval.get("evaluation_id"),
        "approval evaluation differs from execution evidence",
    )
    for id_ in (approval.get("evaluation_id"), evidence.get("evaluation_id")):
        evaluation = evaluations.get(id_, {})
        _require(
            evaluation.get("plan_id") == plan["id"]
            and evaluation.get("risk") == risk
            and evaluation.get("decision", {}).get("decision") in {"ALLOW", "REQUIRE_APPROVAL"},
            "missing/stale/denied actual evaluation",
        )
    confirmed = _one(
        [
            a
            for a in gw["audits"]
            if a.get("plan_id") == plan["id"] and a.get("event_type") == "APPROVAL_CONFIRMED"
        ],
        "confirmation audit",
    )
    _require(
        confirmed.get("approval_id") == approval["id"]
        and confirmed.get("view_hash") == approval["view_hash"]
        and confirmed.get("otp_verified") is (risk == "RED"),
        "wrong confirmation audit",
    )
    if risk == "RED":
        sent = [
            a
            for a in gw["audits"]
            if a.get("plan_id") == plan["id"]
            and a.get("event_type") == "APPROVAL_OTP_SENT"
            and a.get("approval_id") == approval["id"]
            and a.get("view_hash") == approval["view_hash"]
        ]
        _require(1 <= len(sent) <= 3, "missing actual OTP send audit")
    draft = plan.get("plan_draft")
    _require(
        isinstance(draft, dict) and isinstance(draft.get("steps"), list) and draft["steps"],
        "missing actual steps",
    )
    confirmed_at = _dt(approval.get("confirmed_at_wall"))
    _require(
        confirmed_at == _dt(evidence.get("confirmed_at_wall")),
        "actual approval confirmation differs from bank evidence",
    )
    _authorization_binding(after, case_id, plan, evidence, draft, confirmed_at)
    expected_versions = deepcopy(_index("state_versions", before["bank"]["state_versions"]))
    affected = {customer, "cus_ww"} if case_id == "01" else {customer}
    _require(affected.issubset(expected_versions), "missing execution-scope version")
    for id_ in affected:
        expected_versions[id_]["version"] = _int(expected_versions[id_]["version"]) + 1
    _require(
        expected_versions == _index("state_versions", bank["state_versions"]),
        "wrong execution-scope version transition",
    )
    return plan, draft["steps"]


def _authorization_binding(after, case_id, plan, evidence, draft, confirmed_at):
    gw = after["gateway"]
    session = _one(gw["sessions"], "actual authorization session")
    customer = "cus_ls" if case_id in {"23", "34"} else "cus_zs"
    epoch = _int(session.get("authorization_epoch"))
    _require(
        epoch >= 0
        and session.get("id") == after["meta"]["session_id"]
        and session.get("customer_id") == customer
        and session.get("revoked_at_wall") is None,
        "wrong authorization session/customer",
    )
    utterance = _one(gw["utterances"], "canonical typed authorization row")
    ids = [utterance.get("id")]
    _require(
        isinstance(ids[0], str)
        and ids[0]
        and utterance.get("sid") == session["id"]
        and utterance.get("kind") == "typed"
        and utterance.get("text_sha256") == hashlib.sha256(CASES[case_id].encode()).hexdigest()
        and utterance.get("parse_status") == "SCOPED"
        and utterance.get("status") == "CONSUMED"
        and utterance.get("consumed_by") == plan["id"]
        and utterance.get("source_id") is None
        and utterance.get("parent_ids") == []
        and _int(utterance.get("authorization_epoch")) == epoch
        and _int(evidence.get("authorization_epoch")) == epoch,
        "typed authorization row is not bound to this execution",
    )
    # Agent plans historically store NULL here; FORM captures this field. If a
    # future agent plan carries an epoch, a contradicting value is still invalid.
    if plan.get("authorization_epoch") is not None:
        _require(_int(plan["authorization_epoch"]) == epoch, "wrong captured plan epoch")
    for actual in (
        evidence.get("scope_ids"),
        evidence.get("utterance_ids"),
        plan.get("scope_ids"),
        plan.get("utterance_ids"),
        draft.get("utterance_ids"),
    ):
        _require(
            isinstance(actual, list) and actual == ids,
            "authorization IDs differ from actual typed row",
        )
    _require(draft.get("session_id") == session["id"], "plan draft belongs to another session")
    claim = _one(gw["claims"], "consumed authorization claim")
    _require(
        claim.get("scope_id") == ids[0]
        and claim.get("plan_id") == plan["id"]
        and claim.get("status") == "CONSUMED",
        "authorization claim belongs to another execution",
    )
    _require(
        _dt(utterance.get("created_at_wall"))
        <= confirmed_at
        < _dt(utterance.get("expires_at_wall")),
        "authorization was not live at confirmation",
    )


def _account_transition(before, after, deltas):
    expected = deepcopy(_index("accounts", before["accounts"]))
    for id_, fields in deltas.items():
        _require(id_ in expected, "missing account")
        for field, amount in fields.items():
            expected[id_][field] += _int(amount)
    _require(expected == _index("accounts", after["accounts"]), "wrong account counter delta")


def _transfer_rows(before, after, pid, amount, debit, credit, code):
    trf = _one(_new(before, after, "transfers"), "ledger transfer")
    expected = dict(
        plan_id=pid,
        amount_cents=amount,
        debit_account_id=debit,
        credit_account_id=credit,
        code=code,
        phase="POSTED",
    )
    _require(
        all(trf.get(k) == v for k, v in expected.items()), "wrong transfer amount/target/phase"
    )
    txs = _new(before, after, "transactions")
    accounts = _index("accounts", after["accounts"])
    expected_txs = Counter(
        (acct, direction)
        for acct, direction in [(debit, "OUT"), (credit, "IN")]
        if accounts[acct].get("customer_id") is not None
    )
    _require(
        Counter((t.get("account_id"), t.get("direction")) for t in txs) == expected_txs,
        "wrong transaction legs",
    )
    _require(
        all(
            t.get("transfer_id") == trf["id"]
            and t.get("amount_cents") == amount
            and t.get("status") == "POSTED"
            for t in txs
        ),
        "wrong posted transaction facts",
    )


def _operations(before, after, pid, expected):
    rows = _new(before, after, "operations")
    actual = Counter(
        (
            r.get("customer_id"),
            r.get("step_id"),
            r.get("action_kind"),
            r.get("amount_cents"),
            r.get("target_fingerprint"),
        )
        for r in rows
    )
    _require(actual == Counter(expected), "wrong operation customer/step/target/count/amount")
    _require(all(r.get("plan_id") == pid for r in rows), "operation belongs to another plan")


def _args(step, tool, expected):
    _require(step.get("tool") == tool and isinstance(step.get("args"), dict), "wrong tool")
    _require(
        all(
            step["args"].get(k) == v and type(step["args"].get(k)) is type(v)
            for k, v in expected.items()
        ),
        "wrong step arguments",
    )


def _reads(gw, tool):
    return [
        r
        for r in gw["reads"]
        if r.get("tool") == tool
        and isinstance(r.get("args"), dict)
        and isinstance(r.get("data"), dict)
    ]


def _readonly(before, after):
    _unchanged(before["bank"], after["bank"])
    _require(
        not after["gateway"]["plans"] and not after["gateway"]["approvals"],
        "read-only case created a plan/approval",
    )


def _bill_case(before, after):
    _readonly(before, after)
    facts = bill_oracle(after["bank"])
    gw = after["gateway"]
    summaries = [
        r
        for r in _reads(gw, "get_spending_summary")
        if r["args"].get("month") == "2026-09" and r["args"].get("account_id") is None
    ]
    summary = _one(summaries, "full September summary")["data"]
    for field in ("total_cents", "previous_total_cents", "mom_change_cents", "mom_change_bp"):
        _require(
            type(summary.get(field)) is int and summary[field] == facts[field],
            "wrong monthly aggregate",
        )
    actual_categories = {
        c["category"]: {k: c.get(k) for k in ("amount_cents", "previous_amount_cents", "share_bp")}
        for c in summary.get("categories", [])
    }
    _require(
        actual_categories == facts["categories"]
        and len(actual_categories) == len(summary.get("categories", [])),
        "wrong/duplicate full category aggregates",
    )
    start = _dt("2026-09-01T00:00:00+08:00")
    end = _dt("2026-10-01T00:00:00+08:00")
    matches = [
        r
        for r in _reads(gw, "detect_anomalies")
        if _dt(r["args"].get("from_at")) == start
        and _dt(r["args"].get("to_at")) == end
        and r["args"].get("account_id") is None
    ]
    items = _one(matches, "exact month anomaly read")["data"].get("items")
    _require(
        isinstance(items, list) and len(items) == len(facts["anomalies"]), "wrong anomaly count"
    )
    actual = {i["transaction"]["id"]: i for i in items}
    _require(len(actual) == len(items), "duplicate anomaly")
    for expected in facts["anomalies"]:
        got = actual.get(expected["id"], {})
        tx = got.get("transaction", {})
        _require(
            tx.get("amount_cents") == expected["amount_cents"]
            and _dt(tx.get("occurred_at")) == _dt(expected["occurred_at"])
            and set(got.get("codes", [])) == set(expected["codes"]),
            "wrong actual anomaly facts",
        )


def _idle_case(before, after):
    _readonly(before, after)
    expected = idle_oracle(before["bank"])
    _require(expected == {"si_zs_gym": 19900}, "unexpected default idle target")
    rows = _reads(after["gateway"], "detect_subscriptions")
    matches = []
    for read in rows:
        items = read["data"].get("items")
        if isinstance(items, list):
            found, seen = {}, set()
            for item in items:
                _require(
                    isinstance(item, dict) and type(item.get("is_idle")) is bool,
                    "invalid recurring subscription item",
                )
                id_ = item.get("instruction_id")
                _require(
                    id_ is None or isinstance(id_, str) and id_, "invalid subscription identity"
                )
                if id_ is not None:
                    _require(id_ not in seen, "duplicate subscription identity")
                    seen.add(id_)
                amount = _int(item.get("typical_amount_cents"))
                _require(amount > 0, "invalid recurring amount")
                if item["is_idle"] is True and id_:
                    found[id_] = amount
            matches.append(found)
    _require(expected in matches, "idle result does not match raw SQL history")


def _transfer_case(before, after):
    b, a = before["bank"], after["bank"]
    plan, steps = _execute(before, after, "01", "YELLOW")
    _require(len(steps) == 1, "unexpected transfer step count")
    _args(
        steps[0],
        "transfer",
        dict(
            from_account_id="acc_zs_chk",
            payee_id="pye_zs_ww",
            to_account_id=None,
            amount_cents=10000,
            memo=None,
        ),
    )
    payee = _index("payees", b["payees"]).get("pye_zs_ww", {})
    _require(
        payee.get("customer_id") == "cus_zs"
        and payee.get("target_account_id") == "acc_ww_chk"
        and isinstance(payee.get("target_fingerprint"), str)
        and re.fullmatch(r"[0-9a-f]{64}", payee["target_fingerprint"]),
        "wrong independently resolved payee",
    )
    _account_transition(
        b,
        a,
        {
            "acc_zs_chk": {"debits_posted_cents": 10000},
            "acc_ww_chk": {"credits_posted_cents": 10000},
        },
    )
    _transfer_rows(b, a, plan["id"], 10000, "acc_zs_chk", "acc_ww_chk", "P2P_INTERNAL")
    _operations(
        b,
        a,
        plan["id"],
        [("cus_zs", steps[0]["step_id"], "TRANSFER_EXTERNAL", 10000, payee["target_fingerprint"])],
    )
    _unchanged(
        b,
        a,
        {"accounts", "transfers", "transactions", "executions", "operations", "state_versions"},
    )


def _redeem_case(before, after):
    b, a = before["bank"], after["bank"]
    plan, steps = _execute(before, after, "34", "YELLOW")
    _require(len(steps) == 1, "unexpected redeem step count")
    _args(
        steps[0],
        "redeem_product",
        dict(account_id="acc_ls_chk", product_id="prd_mmf", units="1000.0000"),
    )
    sources = [
        s.get("source", {}) for s in steps[0].get("arg_sources", []) if s.get("param") == "units"
    ]
    src = _one(sources, "requested units source")
    _require(src.get("kind") == "utterance", "quantity must come from typed request")
    utterances = {u["id"]: u for u in after["gateway"]["utterances"]}
    utter = utterances.get(src.get("ref"), {})
    span = src.get("span")
    _require(
        utter.get("kind") == "typed"
        and isinstance(span, list)
        and len(span) == 2
        and all(type(i) is int for i in span)
        and 0 <= span[0] < span[1] <= len(CASES["34"])
        and utter.get("text_sha256") == hashlib.sha256(CASES["34"].encode()).hexdigest()
        and CASES["34"][span[0] : span[1]] == "1000",
        "wrong typed quantity span",
    )
    account = _index("accounts", b["accounts"]).get("acc_ls_chk", {})
    _require(
        account.get("credits_posted_cents", 0) - account.get("debits_posted_cents", 0) == 1200000
        and account.get("debits_pending_cents") == 0,
        "default MMF case requires fresh Li Si cash",
    )
    _account_transition(
        b,
        a,
        {
            "acc_ls_chk": {"credits_posted_cents": 100000},
            "acc_fund_prd_mmf": {"debits_posted_cents": 100000},
        },
    )
    prior = _index("holdings", b["holdings"])
    expected = deepcopy(prior)
    key = ("acc_ls_chk", "prd_mmf")
    _require(
        key in expected
        and _dec(expected[key]["units"]) == Decimal("10000")
        and _dec(expected[key]["reserved_units"]) == 0,
        "wrong initial MMF holding",
    )
    expected[key].update(units="9000.0000", cost_cents=900000)
    _require(
        expected == _index("holdings", a["holdings"])
        and expected[key].get("reserved_cost_cents") == 0,
        "wrong final MMF holding/reserve",
    )
    order = _one(_new(b, a, "orders"), "redemption order")
    fields = dict(
        plan_id=plan["id"],
        customer_id="cus_ls",
        account_id="acc_ls_chk",
        product_id="prd_mmf",
        type="RED",
        status="SETTLED",
        amount_cents=100000,
    )
    _require(
        all(order.get(k) == v for k, v in fields.items())
        and _dec(order.get("units")) == Decimal(1000)
        and _dec(order.get("nav")) == Decimal(1),
        "wrong settled MMF order",
    )
    _transfer_rows(b, a, plan["id"], 100000, "acc_fund_prd_mmf", "acc_ls_chk", "FUND_REDEEM")
    _operations(
        b, a, plan["id"], [("cus_ls", steps[0]["step_id"], "FUND_REDEEM", 100000, "prd_mmf")]
    )
    _unchanged(
        b,
        a,
        {
            "accounts",
            "holdings",
            "orders",
            "transfers",
            "transactions",
            "executions",
            "operations",
            "state_versions",
        },
    )


READ_TOOLS = {
    "get_accounts",
    "list_payees",
    "get_transactions",
    "get_spending_summary",
    "detect_anomalies",
    "get_cards",
    "list_scheduled",
    "get_split_status",
    "detect_subscriptions",
    "list_subscriptions",
    "upcoming_charges",
    "get_risk_questionnaire",
    "get_risk_profile",
    "list_products",
    "compare_products",
    "get_holdings",
    "get_product",
    "get_profile",
}


def _resolve_pointer(data, path):
    _require(isinstance(path, str) and (path == "" or path.startswith("/")), "invalid read pointer")
    value = data
    if path == "":
        return value
    for raw in path[1:].split("/"):
        _require("~" not in raw.replace("~0", "").replace("~1", ""), "invalid read pointer escape")
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            _require(
                part.isascii() and part.isdecimal() and str(int(part)) == part,
                "invalid read array index",
            )
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise OracleError("read pointer crosses scalar")
    return value


def _derived_binding(before, after, source, cancel_ids):
    _require(set(source) == {"kind", "template", "inputs"}, "invalid derived source shape")
    reads = _index("reads", after["gateway"]["reads"])
    versions = {r["customer_id"]: _int(r["version"]) for r in before["bank"]["state_versions"]}
    execution = _one(_new(before["bank"], after["bank"], "executions"), "derived execution")
    confirmed = _dt(execution["evidence"]["confirmed_at_wall"])
    leaf_steps, read_count = set(), 0
    for leaf in source["inputs"]:
        _require(isinstance(leaf, dict), "invalid derived leaf")
        if leaf.get("kind") == "step":
            _require(
                set(leaf) == {"kind", "ref"} and leaf.get("ref") in cancel_ids,
                "derived leaf is not an actual cancellation step",
            )
            leaf_steps.add(leaf["ref"])
            continue
        _require(
            leaf.get("kind") == "read" and set(leaf) == {"kind", "ref", "pointer"},
            "invalid derived read leaf shape",
        )
        row = reads.get(leaf.get("ref"), {})
        recorded = row.get("versions")
        _require(
            row.get("sid") == after["meta"]["session_id"]
            and row.get("tool") in READ_TOOLS
            and isinstance(recorded, dict)
            and "cus_zs" in recorded
            and all(type(v) is int and versions.get(k) == v for k, v in recorded.items())
            and _dt(row.get("observed_at")) == _dt(before["bank"]["clock"]["sim_now"])
            and _dt(row.get("created_at_wall")) <= confirmed,
            "derived read is not fresh/current-customer evidence",
        )
        _resolve_pointer(row.get("data"), leaf["pointer"])
        paths = row.get("untrusted_paths")
        _require(
            isinstance(paths, list) and all(isinstance(p, str) for p in paths),
            "missing read taint labels",
        )
        path = leaf["pointer"]
        _require(
            not any(
                path == p or path.startswith(p + "/") or p.startswith(path + "/") or path == ""
                for p in paths
            ),
            "derived read leaf is tainted",
        )
        read_count += 1
    _require(
        leaf_steps == cancel_ids and leaf_steps and read_count,
        "derived savings must include every cancellation and a trusted read",
    )


def _cross_case(before, after):
    b, a = before["bank"], after["bank"]
    plan, steps = _execute(before, after, "36", "YELLOW")
    idle = idle_oracle(b)
    _require(idle == {"si_zs_gym": 19900}, "default cross case requires actual idle gym only")
    cancels = [s for s in steps if s.get("tool") == "cancel_subscription"]
    schedule = _one(
        [s for s in steps if s.get("tool") == "schedule_transfer"], "saving schedule step"
    )
    _require(
        len(steps) == len(idle) + 1
        and Counter(s.get("args", {}).get("instruction_id") for s in cancels)
        == Counter(idle.keys()),
        "wrong cancellation set",
    )
    amount = sum(idle.values())
    _args(
        schedule,
        "schedule_transfer",
        dict(
            from_account_id="acc_zs_chk",
            payee_id=None,
            to_account_id="acc_zs_sav",
            amount_cents=amount,
            period="MONTHLY",
            end_at=None,
        ),
    )
    expected_first = datetime(2026, 11, 1, 8, tzinfo=SHANGHAI)
    _require(
        _dt(schedule["args"].get("first_run_at")) == expected_first, "wrong first business instant"
    )
    cancel_ids = {s.get("step_id") for s in cancels}
    _require(
        cancel_ids.issubset(set(schedule.get("depends_on", []))),
        "saving must depend on every cancellation",
    )
    source = _one(
        [
            s.get("source", {})
            for s in schedule.get("arg_sources", [])
            if s.get("param") == "amount_cents"
        ],
        "derived saving source",
    )
    _require(
        source.get("kind") == "derived"
        and source.get("template") == "cancelled_monthly_fees"
        and isinstance(source.get("inputs"), list)
        and source["inputs"],
        "saving must use declared derived template",
    )
    _derived_binding(before, after, source, cancel_ids)
    prior = _index("instructions", b["instructions"])
    current = _index("instructions", a["instructions"])
    expected = deepcopy(prior)
    for id_ in idle:
        expected[id_]["status"] = "DELETED"
    _require(
        all(current.get(k) == v for k, v in expected.items()),
        "wrong existing mandate modifications",
    )
    new = _one([v for k, v in current.items() if k not in prior], "created saving instruction")
    fields = dict(
        customer_id="cus_zs",
        kind="SCHEDULED_TRANSFER",
        status="ACTIVE",
        from_account_id="acc_zs_chk",
        payee_id=None,
        to_account_id="acc_zs_sav",
        amount_cents=amount,
        period="MONTHLY",
        anchor_day=1,
        time_of_day="08:00:00",
        source_plan_id=plan["id"],
    )
    _require(
        all(new.get(k) == v for k, v in fields.items())
        and _dt(new.get("next_run_at")) == expected_first,
        "wrong saved instruction",
    )
    job = _one(_new(b, a, "event_jobs"), "schedule event job")
    _require(
        job.get("kind") == "SCHEDULED_TRANSFER"
        and job.get("source_id") == new["id"]
        and job.get("status") == "PENDING"
        and _dt(job.get("due_at")) == expected_first,
        "wrong first pending scheduled job",
    )
    operations = [
        (
            "cus_zs",
            step["step_id"],
            "MANDATE_REVOKE",
            idle[step["args"]["instruction_id"]],
            prior[step["args"]["instruction_id"]]["merchant_id"],
        )
        for step in cancels
    ]
    operations.append(
        ("cus_zs", schedule["step_id"], "SCHEDULED_TRANSFER_CREATE", amount, "acc_zs_sav")
    )
    _operations(b, a, plan["id"], operations)
    _unchanged(b, a, {"instructions", "event_jobs", "executions", "operations", "state_versions"})


def _lost_case(before, after):
    b, a = before["bank"], after["bank"]
    plan, steps = _execute(before, after, "23", "RED")
    _require(len(steps) == 1, "unexpected card step count")
    _args(steps[0], "report_card_lost", dict(card_id="crd_ls_1", replace=False))
    expected = deepcopy(_index("cards", b["cards"]))
    _require(
        expected.get("crd_ls_1", {}).get("account_id") == "acc_ls_chk"
        and expected["crd_ls_1"].get("status") == "ACTIVE",
        "wrong initial lost card",
    )
    expected["crd_ls_1"]["status"] = "LOST"
    _require(expected == _index("cards", a["cards"]), "wrong lost/replacement card state")
    _operations(b, a, plan["id"], [("cus_ls", steps[0]["step_id"], "CARD_LOST", 0, None)])
    _unchanged(b, a, {"cards", "executions", "operations", "state_versions"})


def verify_case(case_id, before, after, expected_meta):
    """Return runner-compatible receipt only after every fixed assertion passes.

    Before envelope is {'meta': binding, 'bank': bank-before SELECT JSON}; after
    also requires 'gateway': exact-sid gateway SELECT JSON. Capture bank-before
    BEFORE invoking the case, save it, and bind actual sid/trace metadata once
    the trace exists. Do not invent before-session gateway records. Both bank
    envelopes must be bound to the same
    actual case trace, code SHA, run and generation by the coordinator. This
    module cannot authenticate caller-created attestations or prove deployed
    image identity; runner/root must establish those independent gates.
    """
    try:
        _require(
            case_id in CASES and isinstance(expected_meta, dict), "unsupported default core case"
        )
        meta = expected_meta
        _require(
            set(meta)
            == {"run_id", "checkout_sha", "generation", "case_id", "trace_sha256", "session_id"},
            "missing/extra manifest binding",
        )
        _require(
            meta["case_id"] == case_id
            and isinstance(meta["run_id"], str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", meta["run_id"])
            and re.fullmatch(r"[0-9a-f]{40}", meta["checkout_sha"])
            and re.fullmatch(r"[0-9a-f]{64}", meta["trace_sha256"])
            and re.fullmatch(r"demo_[0-9]{14}_[0-9a-f]{8}", meta["generation"])
            and re.fullmatch(r"ses_[A-Za-z0-9_-]{1,128}", meta["session_id"]),
            "invalid manifest binding",
        )
        _require(
            isinstance(before, dict)
            and isinstance(after, dict)
            and before.get("meta") == meta
            and after.get("meta") == meta,
            "stale/cross-run snapshot binding",
        )
        for snapshot in (before, after):
            _check_bank(snapshot.get("bank"), meta["generation"])
        gw = after.get("gateway")
        _require(
            isinstance(gw, dict) and gw.get("database") == "gateway_" + meta["generation"],
            "wrong gateway generation",
        )
        for name in (
            "plans",
            "approvals",
            "evaluations",
            "audits",
            "reads",
            "utterances",
            "sessions",
            "claims",
        ):
            _index(name, gw.get(name))
        _require(
            before["bank"]["clock"] == after["bank"]["clock"], "clock advanced inside a single case"
        )
        utterances = after["gateway"]["utterances"]
        _require(
            any(
                u.get("kind") == "typed"
                and u.get("text_sha256") == hashlib.sha256(CASES[case_id].encode()).hexdigest()
                for u in utterances
            ),
            "actual typed case text missing",
        )
        {
            "01": _transfer_case,
            "16": _bill_case,
            "27": _idle_case,
            "34": _redeem_case,
            "36": _cross_case,
            "23": _lost_case,
        }[case_id](before, after)
    except OracleError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, InvalidOperation):
        raise OracleError("incomplete/invalid SQL evidence") from None
    checks = dict(
        exact_manifest_binding=True,
        same_current_generation_and_seed=True,
        balanced_ledger=True,
        no_unexpected_captured_bank_changes=True,
        case_specific_SQL_facts=True,
    )
    if case_id in {"01", "34", "36", "23"}:
        checks["single_execution_bound_used_approval"] = True
    if case_id == "23":
        checks["actual_red_otp_confirmation_audit"] = True
    return {
        **{k: meta[k] for k in ("run_id", "checkout_sha", "generation", "case_id", "trace_sha256")},
        "verifier": "SQL",
        "verified": True,
        "checks": checks,
    }
