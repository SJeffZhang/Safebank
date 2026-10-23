"""Stage ③ derived reserves must use the sole bankcore projection by default.

The production ``ProvenanceStage`` never falls back to ``None``: a default
instance calls ``safebank_bankcore.projected_shortfall``, and incomplete
obligation data fails closed as a dependency error instead of yielding a
zero reserve or a bare ``KeyError``.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.errors import GatewayError
from services.gateway.app.pipeline.provenance import ProvenanceStage
from tests.bankcore.fixtures import NOW
from tests.bankcore.obligations_fixtures import (
    raw_billing,
    raw_mandate,
    raw_merchant,
    raw_projection_state,
)
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context
from tests.gateway.w2.test_provenance import authorized, source


def subscribe_step(amount_cents, account_id="acc_a"):
    s = step(
        "subscribe_product", account_id=account_id, product_id="prd_a", amount_cents=amount_cents
    )
    s["arg_sources"] = [
        source(
            "amount_cents",
            "derived",
            template="available_less_7d_charges",
            inputs=[{"kind": "read", "ref": "src_acct", "pointer": "/accounts/0/id"}],
        )
    ]
    return s


def reserve_state(balance=300_000):
    """acc_a available=balance, 7-day obligations = bill_video(3000) + bill_music(1500)."""
    return raw_projection_state(
        balance=balance,
        mandates=(
            raw_mandate(),
            raw_mandate(
                id="si_music",
                billing_id="bill_music",
                merchant_id="mer_music",
                max_amount_cents=1800,
            ),
        ),
        billings=(
            raw_billing(),
            raw_billing(
                id="bill_music",
                merchant_id="mer_music",
                amount_cents=1500,
                anchor_day=20,
                next_due_at="2026-10-20T06:00:00+08:00",
            ),
        ),
        merchants=(raw_merchant(), raw_merchant(id="mer_music", name="乐听音乐")),
    )


def _records_for(state):
    return {
        "src_acct": {
            "tool": "get_accounts",
            "response": {"accounts": [{"id": "acc_a"}]},
            "versions": dict(state.versions),
            "observed_at": state.sim_now,
            "untrusted_paths": [],
            "created_at_wall": datetime.now(UTC),
        }
    }


def _trust(monkeypatch, state, amount_cents, account_id="acc_a"):
    ctx = authorized(context([subscribe_step(amount_cents, account_id)]))
    ctx._lazy_state = state

    async def records(*_a, **_k):
        return _records_for(state)

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", records)
    asyncio.run(ProvenanceStage().run(ctx))
    return ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"]


def test_default_instance_uses_real_bankcore_projection(monkeypatch):
    # balance 300000 - reserve 4500 = 295500; the sole bankcore function decides.
    state = reserve_state()
    assert _trust(monkeypatch, state, 295500) == "derived"
    assert _trust(monkeypatch, state, 295501) == "unknown"


def test_fail_closed_when_obligations_not_loaded(monkeypatch):
    ctx = authorized(context([subscribe_step(100)]))

    async def records(*_a, **_k):
        return {
            "src_acct": {
                "tool": "get_accounts",
                "response": {"accounts": [{"id": "acc_a"}]},
                "versions": {"cus_a": 0, "cus_b": 0},
                "observed_at": NOW,
                "untrusted_paths": [],
                "created_at_wall": datetime.now(UTC),
            }
        }

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", records)
    with pytest.raises(GatewayError) as exc:
        asyncio.run(ProvenanceStage().run(ctx))
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE


def test_fail_closed_on_incomplete_obligation_coverage(monkeypatch):
    state = raw_projection_state(
        balance=300_000,
        covered=("cus_a",),  # missing cus_b from versions
        mandates=(raw_mandate(),),
        billings=(raw_billing(),),
        merchants=(raw_merchant(),),
    )
    with pytest.raises(GatewayError) as exc:
        _trust(monkeypatch, state, 100)
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE


def test_fail_closed_on_unknown_reserve_target_account(monkeypatch):
    state = reserve_state()
    with pytest.raises(GatewayError) as exc:
        _trust(monkeypatch, state, 100, account_id="acc_missing")
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
