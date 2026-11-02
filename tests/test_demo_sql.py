"""Synthetic payload tests only; none executes SQL or claims real acceptance."""

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.demo_sql import OracleError, bill_oracle, build_queries, idle_oracle, verify_case

ROOT = Path(__file__).resolve().parents[1]
GEN = "demo_20261031113502_21268cec"
SHA = "a" * 40
TABLES = (
    "accounts",
    "cards",
    "payees",
    "transactions",
    "transfers",
    "instructions",
    "billings",
    "merchants",
    "holdings",
    "orders",
    "executions",
    "operations",
    "event_jobs",
    "event_executions",
    "instruction_runs",
    "split_requests",
    "split_items",
    "risk_assessments",
)
TEXTS = {
    "01": "给王五转100元",
    "16": "给我出一份9月的账单报告",
    "27": "哪些订阅是闲置没用的",
    "34": "赎回安心货币1000份",
    "36": "找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户",
    "23": "我的卡丢了，帮我挂失",
}


def meta(case_id):
    return dict(
        run_id="run-one",
        checkout_sha=SHA,
        generation=GEN,
        case_id=case_id,
        trace_sha256="b" * 64,
        session_id="ses_fixture",
    )


def bank():
    seed = json.loads((ROOT / "services/bank-backend/fixtures/l1_seed_v1.json").read_text())
    out = {k: [] for k in TABLES}
    out.update(
        database="bank_" + GEN,
        clock=dict(sim_now=seed["sim_now"], dataset_revision=seed["dataset_revision"], version=0),
    )
    account_fields = (
        "id",
        "customer_id",
        "type",
        "current_card_id",
        "single_limit_cents",
        "daily_limit_cents",
        "flags",
        "debits_posted_cents",
        "credits_posted_cents",
        "debits_pending_cents",
        "credits_pending_cents",
    )
    out["accounts"] = [{k: r[k] for k in account_fields} for r in seed["accounts"]]
    net = sum(r["credits_posted_cents"] - r["debits_posted_cents"] for r in out["accounts"])
    out["accounts"] += [
        dict(
            id="acc_external_clearing",
            customer_id=None,
            type="INTERNAL",
            current_card_id=None,
            single_limit_cents=1,
            daily_limit_cents=1,
            flags=0,
            debits_posted_cents=net,
            credits_posted_cents=0,
            debits_pending_cents=0,
            credits_pending_cents=0,
        ),
        dict(
            id="acc_fund_prd_mmf",
            customer_id=None,
            type="INTERNAL",
            current_card_id=None,
            single_limit_cents=1,
            daily_limit_cents=1,
            flags=0,
            debits_posted_cents=0,
            credits_posted_cents=0,
            debits_pending_cents=0,
            credits_pending_cents=0,
        ),
    ]
    out["cards"] = seed["cards"]
    out["payees"] = [
        {k: r.get(k) for k in ("id", "customer_id", "target_account_id", "target_fingerprint")}
        for r in seed["payees"]
    ]
    for r in out["payees"]:
        r["target_fingerprint"] = hashlib.sha256(r["id"].encode()).hexdigest()
    out["state_versions"] = [
        dict(id=r["id"], customer_id=r["id"], version=0) for r in seed["customers"]
    ]
    for r in seed["transactions"]:
        out["transactions"].append({**r, "status": "POSTED", "customer_id": "cus_zs"})
    out["merchants"] = [
        {**r, "usage_expected": r.get("usage_expected", False)} for r in seed["merchants"]
    ]
    out["billings"] = [{**r, "active": True, "period": "MONTHLY"} for r in seed["billings"]]
    out["instructions"] = [
        {
            **r,
            "kind": r.get("kind", "SCHEDULED_TRANSFER"),
            "status": "ACTIVE",
            "source_plan_id": "pln_demo_seed",
        }
        for r in seed["standing_instructions"]
    ]
    out["holdings"] = seed["holdings"]
    return out


def envelope(case_id):
    return {
        "meta": meta(case_id),
        "bank": bank(),
        "gateway": dict(
            database="gateway_" + GEN,
            plans=[],
            approvals=[],
            evaluations=[],
            sessions=[
                dict(
                    id="ses_fixture",
                    customer_id="cus_ls" if case_id in {"34", "23"} else "cus_zs",
                    authorization_epoch=0,
                    revoked_at_wall=None,
                )
            ],
            claims=[],
            audits=[],
            reads=[],
            utterances=[],
        ),
    }


def row(rows, id_):
    return next(r for r in rows if r["id"] == id_)


def step(tool, args, id_="s1", sources=None, depends=None):
    return dict(
        step_id=id_, tool=tool, args=args, arg_sources=sources or [], depends_on=depends or []
    )


def actual_utterance(case_id, plan_id=None):
    return dict(
        id="utt_fixture",
        sid="ses_fixture",
        kind="typed",
        text=TEXTS[case_id],
        text_sha256=hashlib.sha256(TEXTS[case_id].encode()).hexdigest(),
        authorization_epoch=0,
        parse_status="SCOPED" if plan_id else "NO_WRITE",
        status="CONSUMED" if plan_id else "ACTIVE",
        consumed_by=plan_id,
        parent_ids=[],
        source_id=None,
        created_at_wall="2026-10-31T00:59:00+00:00",
        expires_at_wall="2026-10-31T01:09:00+00:00",
    )


def execution(after, case_id, steps):
    pid = "pln_fixture"
    apv = "apv_fixture"
    customer = "cus_ls" if case_id in ("34", "23") else "cus_zs"
    risk = "RED" if case_id == "23" else "YELLOW"
    evidence = dict(
        type="INTERACTIVE",
        origin="agent",
        risk=risk,
        authorization_epoch=0,
        approval_id=apv,
        evaluation_id="evl_final",
        approved_evaluation_id="evl_approved",
        plan_hash="c" * 64,
        scope_ids=["utt_fixture"],
        utterance_ids=["utt_fixture"],
        confirmed_view_hash="d" * 64,
        final_view_hash="d" * 64,
        confirmed_at_wall="2026-10-31T01:00:00+00:00",
    )
    after["bank"]["executions"] = [
        dict(
            plan_id=pid,
            customer_id=customer,
            plan_hash="c" * 64,
            status="EXECUTED",
            evidence=evidence,
        )
    ]
    affected = {customer, "cus_ww"} if case_id == "01" else {customer}
    for version in after["bank"]["state_versions"]:
        if version["customer_id"] in affected:
            version["version"] += 1
    after["gateway"]["plans"] = [
        dict(
            id=pid,
            sid="ses_fixture",
            customer_id=customer,
            origin="agent",
            status="EXECUTED",
            plan_hash="c" * 64,
            authorization_epoch=None,
            utterance_ids=["utt_fixture"],
            scope_ids=["utt_fixture"],
            plan_draft=dict(steps=steps, session_id="ses_fixture", utterance_ids=["utt_fixture"]),
        )
    ]
    after["gateway"]["approvals"] = [
        dict(
            id=apv,
            plan_id=pid,
            evaluation_id="evl_approved",
            customer_id=customer,
            plan_hash="c" * 64,
            view_hash="d" * 64,
            status="USED",
            risk=risk,
            risk_ack=True,
            confirmed_at_wall="2026-10-31T01:00:00+00:00",
        )
    ]
    after["gateway"]["evaluations"] = [
        dict(id=id_, plan_id=pid, risk=risk, decision={"decision": "REQUIRE_APPROVAL"})
        for id_ in ("evl_approved", "evl_final")
    ]
    after["gateway"]["audits"] = [
        dict(
            id="evt_confirm",
            plan_id=pid,
            event_type="APPROVAL_CONFIRMED",
            approval_id=apv,
            view_hash="d" * 64,
            otp_verified=risk == "RED",
        )
    ]
    if risk == "RED":
        after["gateway"]["audits"].append(
            dict(
                id="evt_otp",
                plan_id=pid,
                event_type="APPROVAL_OTP_SENT",
                approval_id=apv,
                view_hash="d" * 64,
                otp_verified=None,
            )
        )
    after["gateway"]["utterances"] = [actual_utterance(case_id, pid)]
    after["gateway"]["claims"] = [
        dict(id="utt_fixture", scope_id="utt_fixture", plan_id=pid, status="CONSUMED")
    ]
    return pid


def operation(action, amount=0, target=None, id_="evt_operation", customer="cus_zs", step_id="s1"):
    return dict(
        event_id=id_,
        plan_id="pln_fixture",
        step_id=step_id,
        customer_id=customer,
        action_kind=action,
        amount_cents=amount,
        target_fingerprint=target,
    )


def transfer(amount, debit, credit, code):
    return dict(
        id="trf_fixture",
        plan_id="pln_fixture",
        event_id=None,
        step_id="s1",
        amount_cents=amount,
        debit_account_id=debit,
        credit_account_id=credit,
        phase="POSTED",
        code=code,
    )


def transactions(amount, debit, credit):
    return [
        dict(
            id="txn_live_out",
            transfer_id="trf_fixture",
            account_id=debit,
            amount_cents=amount,
            direction="OUT",
            status="POSTED",
        ),
        dict(
            id="txn_live_in",
            transfer_id="trf_fixture",
            account_id=credit,
            amount_cents=amount,
            direction="IN",
            status="POSTED",
        ),
    ]


def good(case_id):
    before = envelope(case_id)
    after = deepcopy(before)
    b, g = after["bank"], after["gateway"]
    if case_id == "01":
        execution(
            after,
            case_id,
            [
                step(
                    "transfer",
                    dict(
                        from_account_id="acc_zs_chk",
                        payee_id="pye_zs_ww",
                        to_account_id=None,
                        amount_cents=10000,
                        memo=None,
                    ),
                )
            ],
        )
        row(b["accounts"], "acc_zs_chk")["debits_posted_cents"] += 10000
        row(b["accounts"], "acc_ww_chk")["credits_posted_cents"] += 10000
        b["transfers"].append(transfer(10000, "acc_zs_chk", "acc_ww_chk", "P2P_INTERNAL"))
        b["transactions"] += transactions(10000, "acc_zs_chk", "acc_ww_chk")
        b["operations"].append(
            operation(
                "TRANSFER_EXTERNAL", 10000, row(b["payees"], "pye_zs_ww")["target_fingerprint"]
            )
        )
    elif case_id == "16":
        g["utterances"] = [actual_utterance(case_id)]
        g["reads"] = [
            dict(
                id="src_summary",
                tool="get_spending_summary",
                args=dict(month="2026-09", account_id=None),
                data=dict(
                    month="2026-09",
                    total_cents=644800,
                    previous_total_cents=133190,
                    mom_change_cents=511610,
                    mom_change_bp=38412,
                    categories=[
                        dict(
                            category="娱乐",
                            amount_cents=128000,
                            previous_amount_cents=0,
                            share_bp=1985,
                        ),
                        dict(
                            category="数码",
                            amount_cents=349900,
                            previous_amount_cents=0,
                            share_bp=5426,
                        ),
                        dict(
                            category="健身",
                            amount_cents=19900,
                            previous_amount_cents=36690,
                            share_bp=309,
                        ),
                        dict(
                            category="其他",
                            amount_cents=147000,
                            previous_amount_cents=96500,
                            share_bp=2280,
                        ),
                    ],
                ),
            ),
            dict(
                id="src_anomalies",
                tool="detect_anomalies",
                args=dict(
                    from_at="2026-09-01T00:00:00+08:00",
                    to_at="2026-10-01T00:00:00+08:00",
                    account_id=None,
                ),
                data=dict(
                    items=[
                        dict(
                            transaction=dict(
                                id="txn_zs_014",
                                amount_cents=128000,
                                occurred_at="2026-09-07T02:13:00+08:00",
                            ),
                            codes=["ANOMALY_NIGHT_LARGE", "ANOMALY_NEW_MERCHANT_LARGE"],
                        ),
                        dict(
                            transaction=dict(
                                id="txn_zs_016",
                                amount_cents=26800,
                                occurred_at="2026-09-15T12:04:00+08:00",
                            ),
                            codes=["ANOMALY_DUPLICATE"],
                        ),
                        dict(
                            transaction=dict(
                                id="txn_zs_017",
                                amount_cents=349900,
                                occurred_at="2026-09-22T15:30:00+08:00",
                            ),
                            codes=["ANOMALY_NEW_MERCHANT_LARGE"],
                        ),
                    ]
                ),
            ),
        ]
    elif case_id == "27":
        g["utterances"] = [actual_utterance(case_id)]
        g["reads"] = [
            dict(
                id="src_recurring",
                tool="detect_subscriptions",
                args=dict(lookback_days=90),
                data=dict(
                    items=[
                        dict(
                            instruction_id="si_zs_gym",
                            merchant_id="mer_gym",
                            is_idle=True,
                            typical_amount_cents=19900,
                        )
                    ]
                ),
            )
        ]
    elif case_id == "34":
        sources = [
            dict(param="units", source=dict(kind="utterance", ref="utt_fixture", span=[6, 10]))
        ]
        execution(
            after,
            case_id,
            [
                step(
                    "redeem_product",
                    dict(account_id="acc_ls_chk", product_id="prd_mmf", units="1000.0000"),
                    sources=sources,
                )
            ],
        )
        row(b["accounts"], "acc_ls_chk")["credits_posted_cents"] += 100000
        row(b["accounts"], "acc_fund_prd_mmf")["debits_posted_cents"] += 100000
        b["holdings"][0].update(units="9000.0000", cost_cents=900000)
        b["orders"].append(
            dict(
                id="ord_fixture",
                plan_id="pln_fixture",
                customer_id="cus_ls",
                account_id="acc_ls_chk",
                product_id="prd_mmf",
                type="RED",
                status="SETTLED",
                units="1000.0000",
                amount_cents=100000,
                nav="1.0000",
                reserved_cost_cents=100000,
            )
        )
        b["transfers"].append(transfer(100000, "acc_fund_prd_mmf", "acc_ls_chk", "FUND_REDEEM"))
        b["transactions"].append(transactions(100000, "acc_fund_prd_mmf", "acc_ls_chk")[1])
        b["operations"].append(operation("FUND_REDEEM", 100000, "prd_mmf", customer="cus_ls"))
    elif case_id == "36":
        steps = [
            step("cancel_subscription", dict(instruction_id="si_zs_gym")),
            step(
                "schedule_transfer",
                dict(
                    from_account_id="acc_zs_chk",
                    payee_id=None,
                    to_account_id="acc_zs_sav",
                    amount_cents=19900,
                    period="MONTHLY",
                    first_run_at="2026-11-01T08:00:00+08:00",
                    end_at=None,
                ),
                id_="s2",
                sources=[
                    dict(
                        param="amount_cents",
                        source=dict(
                            kind="derived",
                            template="cancelled_monthly_fees",
                            inputs=[
                                dict(kind="step", ref="s1"),
                                dict(
                                    kind="read",
                                    ref="src_subscriptions",
                                    pointer="/items/0/amount_cents",
                                ),
                            ],
                        ),
                    )
                ],
                depends=["s1"],
            ),
        ]
        execution(after, case_id, steps)
        g["reads"] = [
            dict(
                id="src_subscriptions",
                sid="ses_fixture",
                tool="list_subscriptions",
                args={},
                data={
                    "items": [
                        {
                            "instruction_id": "si_zs_gym",
                            "amount_cents": 19900,
                            "merchant_name": "力健健身",
                        }
                    ]
                },
                untrusted_paths=["/items/0/merchant_name"],
                observed_at=b["clock"]["sim_now"],
                created_at_wall="2026-10-31T00:59:30+00:00",
                versions={"cus_zs": 0},
            )
        ]
        row(b["instructions"], "si_zs_gym")["status"] = "DELETED"
        b["instructions"].append(
            dict(
                id="si_live_saving",
                customer_id="cus_zs",
                kind="SCHEDULED_TRANSFER",
                status="ACTIVE",
                from_account_id="acc_zs_chk",
                payee_id=None,
                to_account_id="acc_zs_sav",
                amount_cents=19900,
                period="MONTHLY",
                anchor_day=1,
                time_of_day="08:00:00",
                next_run_at="2026-11-01T08:00:00+08:00",
                source_plan_id="pln_fixture",
            )
        )
        b["event_jobs"].append(
            dict(
                id="job_fixture",
                kind="SCHEDULED_TRANSFER",
                source_id="si_live_saving",
                due_at="2026-11-01T08:00:00+08:00",
                status="PENDING",
                period_key="2026-11-01T08:00:00+08:00",
            )
        )
        b["operations"] += [
            operation("MANDATE_REVOKE", 19900, "mer_gym"),
            operation(
                "SCHEDULED_TRANSFER_CREATE", 19900, "acc_zs_sav", "evt_schedule", step_id="s2"
            ),
        ]
    elif case_id == "23":
        execution(
            after, case_id, [step("report_card_lost", dict(card_id="crd_ls_1", replace=False))]
        )
        row(b["cards"], "crd_ls_1")["status"] = "LOST"
        b["operations"].append(operation("CARD_LOST", customer="cus_ls"))
    return before, after


def test_queries_are_select_only_and_never_include_credential_columns():
    queries = build_queries("ses_fixture")
    assert set(queries) == {"bank", "gateway"}
    for sql in queries.values():
        assert sql.lstrip().startswith("SELECT")
        assert ";" not in sql.rstrip(";")
        assert not re.search(
            r"\b(?:INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE|CALL|COPY)\b", sql, re.I
        )
        assert not any(
            x in sql
            for x in ("code_digest", "code_ciphertext", "password", "account_no", "raw_text")
        )
    assert "'ses_fixture'" in queries["gateway"]


@pytest.mark.parametrize("sid", ["x';drop table plans;--", "", None, "../session", "ses_中文"])
def test_query_builder_rejects_untrusted_session_identifier(sid):
    with pytest.raises(OracleError):
        build_queries(sid)


def test_independent_bill_oracle_uses_raw_seed_history():
    facts = bill_oracle(bank())
    assert (facts["total_cents"], facts["previous_total_cents"]) == (644800, 133190)
    assert {x["id"]: x["amount_cents"] for x in facts["anomalies"]} == {
        "txn_zs_014": 128000,
        "txn_zs_016": 26800,
        "txn_zs_017": 349900,
    }
    assert facts["mom_change_bp"] == 38412


def test_idle_oracle_uses_usage_expected_and_raw_non_mandate_windows():
    assert idle_oracle(bank()) == {"si_zs_gym": 19900}
    b = bank()
    row(b["merchants"], "mer_gym")["usage_expected"] = False
    assert idle_oracle(b) == {}


@pytest.mark.parametrize("case_id", TEXTS)
def test_each_core_case_requires_and_accepts_its_specific_financial_evidence(case_id):
    before, after = good(case_id)
    proof = verify_case(case_id, before, after, meta(case_id))
    assert proof["verified"] is True and proof["verifier"] == "SQL"
    assert (
        proof["case_id"] == case_id
        and proof["checks"]
        and all(v is True for v in proof["checks"].values())
    )
    assert proof["generation"] == GEN and proof["checkout_sha"] == SHA


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", "demo_20261031000000_12345678"),
        ("checkout_sha", "c" * 40),
        ("trace_sha256", "c" * 64),
        ("run_id", "another-run"),
        ("case_id", "23"),
    ],
)
def test_cross_run_generation_head_or_trace_fails(field, value):
    before, after = good("01")
    after["meta"][field] = value
    with pytest.raises(OracleError):
        verify_case("01", before, after, meta("01"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b, a: a["bank"].pop("transfers"),
        lambda b, a: a["bank"]["accounts"].append(deepcopy(a["bank"]["accounts"][0])),
        lambda b, a: a["bank"]["accounts"][0].update(debits_posted_cents=True),
        lambda b, a: a["bank"]["accounts"][0].update(credits_posted_cents=9999999),
        lambda b, a: a["bank"].update(database="bank_demo_20261031000000_12345678"),
        lambda b, a: a["bank"]["transfers"].append(deepcopy(a["bank"]["transfers"][0])),
        lambda b, a: a["bank"]["transfers"][0].update(amount_cents=20000),
        lambda b, a: a["bank"]["transfers"][0].update(credit_account_id="acc_ls_chk"),
        lambda b, a: a["gateway"]["approvals"][0].update(status="OPEN"),
        lambda b, a: a["bank"]["executions"][0]["evidence"].update(approval_id="apv_stale"),
        lambda b, a: a["gateway"]["audits"].clear(),
    ],
)
def test_bad_or_incomplete_transfer_evidence_never_gets_a_receipt(mutate):
    before, after = good("01")
    mutate(before, after)
    with pytest.raises(OracleError):
        verify_case("01", before, after, meta("01"))


@pytest.mark.parametrize("case_id", ["16", "27"])
def test_read_only_case_rejects_hidden_bank_write(case_id):
    before, after = good(case_id)
    after["bank"]["cards"][0]["status"] = "FROZEN"
    with pytest.raises(OracleError):
        verify_case(case_id, before, after, meta(case_id))


@pytest.mark.parametrize(
    "mutation", ["wrong_range", "wrong_total", "missing_anomaly", "wrong_code"]
)
def test_bill_requires_exact_full_month_aggregate_and_three_real_anomalies(mutation):
    before, after = good("16")
    reads = after["gateway"]["reads"]
    if mutation == "wrong_range":
        reads[1]["args"]["to_at"] = "2026-09-30T23:59:59+08:00"
    elif mutation == "wrong_total":
        reads[0]["data"]["total_cents"] = 644801
    elif mutation == "missing_anomaly":
        reads[1]["data"]["items"].pop()
    else:
        reads[1]["data"]["items"][0]["codes"] = ["ANOMALY_DUPLICATE"]
    with pytest.raises(OracleError):
        verify_case("16", before, after, meta("16"))


@pytest.mark.parametrize(
    "mutation",
    ["all_subscriptions", "wrong_amount", "no_dependency", "literal_amount", "bank_moved"],
)
def test_cross_scene_is_exact_idle_set_and_derived_monthly_own_saving(mutation):
    before, after = good("36")
    p = after["gateway"]["plans"][0]["plan_draft"]["steps"][1]
    if mutation == "all_subscriptions":
        row(after["bank"]["instructions"], "si_zs_music")["status"] = "DELETED"
    elif mutation == "wrong_amount":
        row(after["bank"]["instructions"], "si_live_saving")["amount_cents"] = 6600
    elif mutation == "no_dependency":
        p["depends_on"] = []
    elif mutation == "literal_amount":
        p["arg_sources"][0]["source"] = {"kind": "utterance", "ref": "utt_fixture", "span": [0, 4]}
    else:
        row(after["bank"]["accounts"], "acc_zs_chk")["debits_posted_cents"] += 19900
    with pytest.raises(OracleError):
        verify_case("36", before, after, meta("36"))


@pytest.mark.parametrize(
    "mutation",
    ["old_units", "wrong_cash", "missing_order", "duplicate_order", "wrong_quantity_source"],
)
def test_redemption_requires_partial_quantity_single_settled_order_and_cash(mutation):
    before, after = good("34")
    if mutation == "old_units":
        after["bank"]["holdings"][0]["units"] = "10000.0000"
    elif mutation == "wrong_cash":
        row(after["bank"]["accounts"], "acc_ls_chk")["credits_posted_cents"] -= 1
    elif mutation == "missing_order":
        after["bank"]["orders"] = []
    elif mutation == "duplicate_order":
        after["bank"]["orders"].append(dict(after["bank"]["orders"][0], id="ord_duplicate"))
    else:
        after["gateway"]["plans"][0]["plan_draft"]["steps"][0]["arg_sources"][0]["source"][
            "kind"
        ] = "read"
    with pytest.raises(OracleError):
        verify_case("34", before, after, meta("34"))


@pytest.mark.parametrize("mutation", ["wrong_card", "replacement", "no_otp", "not_red"])
def test_lost_card_requires_correct_card_no_replacement_and_real_red_otp_audit(mutation):
    before, after = good("23")
    if mutation == "wrong_card":
        row(after["bank"]["cards"], "crd_ls_1")["status"] = "ACTIVE"
        row(after["bank"]["cards"], "crd_zs_1")["status"] = "LOST"
    elif mutation == "replacement":
        after["bank"]["cards"].append(
            dict(after["bank"]["cards"][1], id="crd_new", replaces_id="crd_ls_1")
        )
    elif mutation == "no_otp":
        after["gateway"]["audits"][0]["otp_verified"] = False
    else:
        after["gateway"]["approvals"][0]["risk"] = "YELLOW"
    with pytest.raises(OracleError):
        verify_case("23", before, after, meta("23"))


def test_duplicate_bill_category_is_incomplete_evidence():
    before, after = good("16")
    after["gateway"]["reads"][0]["data"]["categories"].append(
        deepcopy(after["gateway"]["reads"][0]["data"]["categories"][0])
    )
    with pytest.raises(OracleError):
        verify_case("16", before, after, meta("16"))


def test_bool_reserved_money_is_not_an_integer_zero():
    before, after = good("34")
    before["bank"]["holdings"][0]["reserved_cost_cents"] = False
    after["bank"]["holdings"][0]["reserved_cost_cents"] = False
    with pytest.raises(OracleError):
        verify_case("34", before, after, meta("34"))


def test_every_bank_select_column_exists_in_actual_sqlalchemy_metadata():
    import importlib.util

    from scripts import demo_sql as module

    spec = importlib.util.spec_from_file_location(
        "oracle_bank_models", ROOT / "services/bank-backend/app/models.py"
    )
    models = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(models)
    for table, columns, _order in module.BANK_FIELDS.values():
        actual = set(models.metadata.tables[table].columns.keys())
        for expression in columns.split(","):
            column = expression.split(" AS ")[0].split("::")[0].split("->")[0]
            assert column in actual, (table, column)


def test_stale_evaluation_cannot_be_hidden_by_used_approval():
    before, after = good("01")
    after["bank"]["executions"][0]["evidence"]["evaluation_id"] = "evl_stale"
    with pytest.raises(OracleError):
        verify_case("01", before, after, meta("01"))


def test_every_gateway_select_column_exists_without_opening_a_database(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    from services.gateway.app import tables

    sql = build_queries("ses_fixture")["gateway"]
    # Generated raw subselects have no functions: each expression is a column,
    # cast or json-key extraction. Outer json-building SELECTs are skipped.
    selects = re.findall(r"SELECT ([A-Za-z0-9_., :>'-]+) FROM ([a-z_]+)", sql)
    assert len(selects) >= 6
    for columns, table in selects:
        for expression in columns.split(","):
            column = expression.split(" AS ")[0].split("->")[0]
            owner = table
            if "." in column:
                alias, column = column.split(".", 1)
                owner = {"a": "approvals", "e": "evaluations"}[alias]
            assert column in tables.metadata.tables[owner].columns.keys(), (owner, column)


@pytest.mark.parametrize("case_id", ["06", "33", "38b", "37", "D3", None])
def test_non_default_six_is_explicitly_unsupported(case_id):
    before, after = good("01")
    with pytest.raises(OracleError):
        verify_case(case_id, before, after, meta("01"))


def test_before_bank_capture_does_not_require_future_session_or_fake_gateway_records():
    before, after = good("01")
    before.pop("gateway")
    assert verify_case("01", before, after, meta("01"))["verified"] is True


def test_bank_snapshot_query_needs_no_session_identifier():
    from scripts import demo_sql as module

    assert hasattr(module, "bank_snapshot_query"), "bank capture must not require a future sid"
    assert module.bank_snapshot_query() == build_queries("ses_fixture")["bank"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "other_session_id",
        "duplicate_ids",
        "extra_ids",
        "plan_ids",
        "draft_ids",
        "row_sid",
        "session_customer",
        "session_epoch",
        "utterance_epoch",
        "execution_epoch",
        "consumed_plan",
        "claim_plan",
        "revoked_session",
        "expired_before_confirm",
        "otp_wrong_view",
    ],
)
def test_review_r1_requires_actual_current_authorization_and_otp_view(mutation):
    case_id = "23" if mutation == "otp_wrong_view" else "01"
    before, after = good(case_id)
    g = after["gateway"]
    evidence = after["bank"]["executions"][0]["evidence"]
    if mutation == "missing":
        g["utterances"] = []
    elif mutation == "other_session_id":
        evidence.update(utterance_ids=["utt_other"], scope_ids=["utt_other"])
    elif mutation == "duplicate_ids":
        evidence.update(utterance_ids=["utt_fixture"] * 2, scope_ids=["utt_fixture"] * 2)
    elif mutation == "extra_ids":
        evidence.update(
            utterance_ids=["utt_fixture", "utt_other"], scope_ids=["utt_fixture", "utt_other"]
        )
    elif mutation == "plan_ids":
        g["plans"][0]["utterance_ids"] = ["utt_other"]
    elif mutation == "draft_ids":
        g["plans"][0]["plan_draft"]["utterance_ids"] = ["utt_other"]
    elif mutation == "row_sid":
        g["utterances"][0]["sid"] = "ses_other"
    elif mutation == "session_customer":
        g["sessions"][0]["customer_id"] = "cus_ls"
    elif mutation == "session_epoch":
        g["sessions"][0]["authorization_epoch"] = 1
    elif mutation == "utterance_epoch":
        g["utterances"][0]["authorization_epoch"] = 1
    elif mutation == "execution_epoch":
        evidence["authorization_epoch"] = 1
    elif mutation == "consumed_plan":
        g["utterances"][0]["consumed_by"] = "pln_other"
    elif mutation == "claim_plan":
        g["claims"][0]["plan_id"] = "pln_other"
    elif mutation == "revoked_session":
        g["sessions"][0]["revoked_at_wall"] = "2026-10-31T01:00:00Z"
    elif mutation == "expired_before_confirm":
        g["utterances"][0]["expires_at_wall"] = "2026-10-31T00:59:59Z"
    elif mutation == "otp_wrong_view":
        g["audits"][1]["view_hash"] = "wrong-view"
    with pytest.raises(OracleError):
        verify_case(case_id, before, after, meta(case_id))


@pytest.mark.parametrize(
    "mutation",
    [
        "only_step",
        "only_read",
        "wrong_pointer",
        "tainted",
        "stale_versions",
        "other_sid",
        "future_read",
        "step_extra_field",
    ],
)
def test_review_r2_requires_every_cancel_and_resolvable_fresh_trusted_read(mutation):
    before, after = good("36")
    g = after["gateway"]
    source = g["plans"][0]["plan_draft"]["steps"][1]["arg_sources"][0]["source"]
    if mutation == "only_step":
        source["inputs"] = [x for x in source["inputs"] if x["kind"] == "step"]
    elif mutation == "only_read":
        source["inputs"] = [x for x in source["inputs"] if x["kind"] == "read"]
    elif mutation == "wrong_pointer":
        source["inputs"][1]["pointer"] = "/items/99/amount_cents"
    elif mutation == "tainted":
        source["inputs"][1]["pointer"] = "/items/0/merchant_name"
    elif mutation == "stale_versions":
        g["reads"][0]["versions"]["cus_zs"] = 99
    elif mutation == "other_sid":
        g["reads"][0]["sid"] = "ses_other"
    elif mutation == "future_read":
        g["reads"][0]["created_at_wall"] = "2026-10-31T01:00:01Z"
    elif mutation == "step_extra_field":
        source["inputs"][0]["pointer"] = "/invented"
    with pytest.raises(OracleError):
        verify_case("36", before, after, meta("36"))


@pytest.mark.parametrize("wrong_amount", [1, 19900])
def test_review_r3_rejects_duplicate_idle_identity_before_mapping(wrong_amount):
    before, after = good("27")
    item = deepcopy(after["gateway"]["reads"][0]["data"]["items"][0])
    item["typical_amount_cents"] = wrong_amount
    after["gateway"]["reads"][0]["data"]["items"].insert(0, item)
    with pytest.raises(OracleError):
        verify_case("27", before, after, meta("27"))


@pytest.mark.parametrize("case_id", ["01", "34", "36", "23"])
@pytest.mark.parametrize(
    "field,value",
    [("customer_id", "cus_other"), ("step_id", "s99"), ("target_fingerprint", "wrong-target")],
)
def test_review_r4_operation_rows_bind_to_actual_step_customer_target(case_id, field, value):
    before, after = good(case_id)
    after["bank"]["operations"][0][field] = value
    with pytest.raises(OracleError):
        verify_case(case_id, before, after, meta(case_id))


@pytest.mark.parametrize("case_id", TEXTS)
def test_actual_select_hash_projection_needs_no_plaintext_utterance(case_id):
    before, after = good(case_id)
    for u in after["gateway"]["utterances"]:
        u.pop("text")
    assert verify_case(case_id, before, after, meta(case_id))["verified"] is True


def test_r2_valid_accounts_leaf_plus_all_steps_matches_gateway_semantics():
    before, after = good("36")
    g = after["gateway"]
    read = g["reads"][0]
    read.update(
        id="src_accounts",
        tool="get_accounts",
        data={"accounts": [{"available_cents": 600000}]},
        untrusted_paths=[],
    )
    leaf = g["plans"][0]["plan_draft"]["steps"][1]["arg_sources"][0]["source"]["inputs"][1]
    leaf.update(ref="src_accounts", pointer="/accounts/0/available_cents")
    assert verify_case("36", before, after, meta("36"))["verified"] is True


def test_r3_legitimate_non_idle_subscription_rows_remain_permitted():
    before, after = good("27")
    after["gateway"]["reads"][0]["data"]["items"].append(
        dict(
            instruction_id="si_zs_music",
            merchant_id="mer_music",
            is_idle=False,
            typical_amount_cents=1500,
        )
    )
    assert verify_case("27", before, after, meta("27"))["verified"] is True


def test_r1_nonnull_matching_agent_plan_epoch_is_also_permitted():
    before, after = good("01")
    after["gateway"]["plans"][0]["authorization_epoch"] = 0
    assert verify_case("01", before, after, meta("01"))["verified"] is True


@pytest.mark.parametrize("case_id", ["01", "34", "36", "23"])
def test_actual_confirmation_time_must_match_bank_evidence(case_id):
    before, after = good(case_id)
    after["gateway"]["approvals"][0]["confirmed_at_wall"] = "2099-01-01T00:00:00Z"
    with pytest.raises(OracleError):
        verify_case(case_id, before, after, meta(case_id))


@pytest.mark.parametrize("case_id", ["01", "34", "36", "23"])
def test_same_confirmation_instant_accepts_timezone_offsets_and_null_agent_epoch(case_id):
    before, after = good(case_id)
    assert after["gateway"]["plans"][0]["authorization_epoch"] is None
    after["gateway"]["approvals"][0]["confirmed_at_wall"] = "2026-10-31T09:00:00+08:00"
    assert verify_case(case_id, before, after, meta(case_id))["verified"] is True


def test_bound_equal_confirmation_must_still_be_inside_original_authorization_lifetime():
    before, after = good("01")
    after["gateway"]["approvals"][0]["confirmed_at_wall"] = "2099-01-01T08:00:00+08:00"
    after["bank"]["executions"][0]["evidence"]["confirmed_at_wall"] = "2099-01-01T00:00:00Z"
    with pytest.raises(OracleError):
        verify_case("01", before, after, meta("01"))
