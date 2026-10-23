"""Shared fixtures for the W3a pipeline-stage unit tests (no database).

``w3_state`` is ``banking_state()`` plus a complete (non-null) obligation
snapshot carrying one 4,500-cent monthly billing on ``acc_a``, so transfer
preflight can exercise the 7-day reserve projection with the exact scene-2
numbers (available 600,000, reserve 4,500, so step 2 max outflow 295,500).
"""

from __future__ import annotations

import asyncio
from datetime import time, timedelta

from safebank_bankcore.ledger import replace_state
from safebank_contracts.obligations import (
    Merchant,
    MerchantBilling,
    MerchantMandate,
    ObligationSnapshot,
)
from safebank_contracts.plan import PlanDraft
from safebank_contracts.state import BankState

from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.pipeline.entity import EntityStage
from tests.bankcore.fixtures import NOW
from tests.bankcore.test_banking import banking_state
from tests.contracts.samples import step


def _obligations(reserve: int) -> ObligationSnapshot:
    return ObligationSnapshot(
        covered_customer_ids=("cus_a", "cus_b"),
        merchants={
            "mer_x": Merchant(
                id="mer_x", name="会员", mcc="5816", category="娱乐", usage_expected=False
            )
        },
        billings={
            "bill_x": MerchantBilling(
                id="bill_x",
                customer_id="cus_a",
                account_id="acc_a",
                merchant_id="mer_x",
                amount_cents=reserve,
                period="MONTHLY",
                anchor_day=20,
                weekday=None,
                time_of_day=time(6, 0),
                next_due_at=(NOW + timedelta(days=5)).replace(hour=6, minute=0),
                active=True,
                created_at=NOW,
            )
        },
        standing_instructions={
            "si_x": MerchantMandate(
                id="si_x",
                customer_id="cus_a",
                status="ACTIVE",
                from_account_id="acc_a",
                merchant_id="mer_x",
                billing_id="bill_x",
                max_amount_cents=reserve + 100,
                end_at=None,
                priority="HIGH",
                grant_scope_hash="0" * 64,
                source_plan_id="pln_seed",
                created_at=NOW,
            )
        },
        runs=(),
        instruction_targets={},
    )


def w3_state(reserve: int = 4500) -> BankState:
    return replace_state(banking_state(), obligations=_obligations(reserve))


def draft(steps):
    return dict(
        request_id="req_1",
        session_id="sid_1",
        utterance_ids=["utt_1"],
        intents=[],
        rationale="",
        steps=steps,
    )


def build_ctx(steps, *, plan_id="pln_1", origin="app_form", state=None):
    ctx = EvalContext(
        plan_draft=PlanDraft.model_validate(draft(steps)),
        customer_id="cus_a",
        session_id="sid_1",
        plan_id=plan_id,
        origin=origin,
    )
    ctx._lazy_state = state or w3_state()
    return ctx


def run_entity(ctx):
    async def run():
        await EntityStage().run(ctx)

    asyncio.run(run())
    return ctx


def transfer(payee_id, amount_cents, step_id="s1", memo=None):
    return step(
        payee_id=payee_id,
        to_account_id=None,
        amount_cents=amount_cents,
        memo=memo,
        step_id=step_id,
    )
