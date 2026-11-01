"""Read projections preserve business dates and keep catalogs compact; no DB."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from app.reads import read
from app.state import load_bank_state
from safebank_contracts.state import StateScope
from safebank_contracts.tools import READ_ARGS
from safebank_contracts.views import NavPoint, ProductView

from tests.bankcore.fixtures import base_state


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def __iter__(self):
        return iter(self.rows)


class ReadConnection:
    """Only the SQL execute boundary is replaced; real state loading still runs."""

    def __init__(self, order_time):
        state = base_state()
        self.rows = {
            "clock": [{"id": 1, "version": 1, "dataset_revision": "synthetic"}],
            "accounts": [state.accounts["acc_a"].model_dump()],
            "customers": [state.customers["cus_a"].model_dump()],
            "state_versions": [("cus_a", 0)],
            "fund_orders": [
                {
                    "id": "ord_a",
                    "customer_id": "cus_a",
                    "account_id": "acc_a",
                    "product_id": "prd_a",
                    "type": "SUB",
                    "status": "PENDING_CONFIRM",
                    "amount_cents": 200000,
                    "units": Decimal("0.0000"),
                    "nav": None,
                    "reserved_cost_cents": 0,
                    "pending_transfer_id": "tr_pending",
                    "plan_id": "pln_a",
                    "step_id": "stp_a",
                    "trade_date": order_time,
                    "confirm_at": datetime(2026, 10, 16, 1, tzinfo=UTC),
                    "settle_at": None,
                    "expires_at": None,
                    "failure_code": None,
                }
            ],
        }

    async def execute(self, statement):
        return Rows(self.rows.get(statement.get_final_froms()[0].name, []))


def test_loaded_fund_order_restores_the_business_trade_date_from_utc_storage():
    loaded = asyncio.run(
        load_bank_state(
            ReadConnection(datetime(2026, 10, 14, 16, tzinfo=UTC)),
            StateScope(customer_ids=("cus_a",)),
            datetime(2026, 10, 15, 1, tzinfo=UTC),
            customer_id="cus_a",
        )
    )
    assert loaded.fund_orders[0].trade_date == date(2026, 10, 15)
    public = read(
        loaded, "get_holdings", READ_ARGS["get_holdings"](account_id=None, product_id=None), "cus_a"
    )
    assert public.orders[0].trade_date == date(2026, 10, 15)


def test_catalog_omits_history_while_product_detail_keeps_it():
    product = ProductView(
        id="prd_a",
        name="合成产品",
        product_type="BOND",
        risk_level=2,
        risk_label="稳健",
        nav="1.0520",
        nav_date=date(2026, 10, 15),
        recent_return_bp=12,
        min_amount_cents=10000,
        confirm_days=1,
        settle_days=2,
        risk_disclosure="合成揭示",
        disclosure_version="v1",
        nav_history=(NavPoint(date=date(2026, 10, 15), nav="1.0520"),),
    )
    state = base_state().model_copy(update={"products": (product,)})
    catalog = read(
        state,
        "list_products",
        READ_ARGS["list_products"](max_risk_level=None, product_type=None),
        "cus_a",
    )
    assert catalog.items[0].name == "合成产品"
    assert catalog.items[0].nav_history == ()
    detail = read(state, "get_product", READ_ARGS["get_product"](product_id="prd_a"), "cus_a")
    assert len(detail.nav_history) == 1
    assert state.products[0].nav_history == product.nav_history
