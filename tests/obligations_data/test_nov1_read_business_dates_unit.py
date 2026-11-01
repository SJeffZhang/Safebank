"""Independent private audit: actual loader/read boundary, no DB/provider."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.reads import read
from app.state import load_bank_state
from safebank_bankcore.risk import apply_risk_assessment
from safebank_contracts.state import StateScope
from safebank_contracts.tools import READ_ARGS

from tests.bankcore.obligations_fixtures import raw_instruction, raw_projection_state
from tests.obligations_data.test_query_read_projection_unit import ReadConnection

SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 14, 16, 30, tzinfo=UTC)


def connection():
    c = ReadConnection(NOW)
    c.rows["fund_orders"] = []
    return c


def loaded(c):
    return asyncio.run(
        load_bank_state(c, StateScope(customer_ids=("cus_a",)), NOW, customer_id="cus_a")
    )


def test_real_loader_normalizes_clock_for_balance_history():
    state = loaded(connection())
    result = read(state, "get_accounts", READ_ARGS["get_accounts"](history_days=1), "cus_a")
    assert state.sim_now.date() == date(2026, 10, 15)
    assert [h.date for h in result.balance_history] == [date(2026, 10, 15)]


def test_real_loader_normalizes_clock_for_assessment_scoring():
    state = loaded(connection())
    result = apply_risk_assessment(
        "rq_v1", tuple((f"q0{i}", "a") for i in range(1, 7)), state.sim_now
    )
    assert result.valid_until == date(2027, 10, 15)


def test_r13_loaded_assessment_retains_business_expiry_date():
    c = connection()
    c.rows["customers"][0].update(risk_level=1, risk_valid_until="2027-10-15")
    c.rows["risk_assessments"] = [
        dict(
            id="asm_new",
            customer_id="cus_a",
            questionnaire_id="rq_v1",
            answers=[[f"q0{i}", "a"] for i in range(1, 7)],
            score=6,
            level="R1",
            assessed_at=NOW,
            valid_until=datetime(2027, 10, 14, 16, 30, tzinfo=UTC),
            plan_id="pln_new",
        )
    ]
    state = loaded(c)
    r13 = read(state, "get_risk_profile", READ_ARGS["get_risk_profile"](), "cus_a")
    r18 = read(state, "get_profile", READ_ARGS["get_profile"](), "cus_a")
    assert r13.is_valid is r18.risk_profile.is_valid is True
    assert r13.valid_until == r18.risk_profile.valid_until == date(2027, 10, 15)


def test_product_loader_retains_business_nav_anchor_date():
    c = connection()
    c.rows["products"] = [
        dict(
            id="prd_a",
            name="合成债券",
            product_type="BOND",
            risk_level=1,
            risk_label="低风险",
            base_nav=Decimal("1.0000"),
            base_date=datetime(2026, 10, 14, 16, tzinfo=UTC),
            confirm_days=1,
            settle_days=2,
            min_amount_cents=10000,
            risk_disclosure="合成风险揭示",
            disclosure_version="v1",
        )
    ]
    state = loaded(c)
    detail = read(state, "get_product", READ_ARGS["get_product"](product_id="prd_a"), "cus_a")
    assert detail.nav_date == date(2026, 10, 15)
    assert detail.nav == Decimal("1.0000")


def test_upcoming_external_scheduled_target_does_not_disappear():
    state = raw_projection_state(instructions=(raw_instruction(),))
    result = read(
        state, "upcoming_charges", READ_ARGS["upcoming_charges"](days=7, account_id=None), "cus_a"
    )
    assert len(result.scheduled_items) == 1
    assert result.scheduled_transfers_total_cents == 1000
    assert result.scheduled_items[0].target_display.strip()
