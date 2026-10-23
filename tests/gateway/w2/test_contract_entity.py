import asyncio

import pytest
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft

from services.gateway.app.errors import GatewayError
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.pipeline.contract import ContractStage
from services.gateway.app.pipeline.entity import EntityStage
from tests.bankcore.test_banking import banking_state
from tests.contracts.samples import step


def draft(steps=None):
    return dict(
        request_id="req_1",
        session_id="sid_1",
        utterance_ids=["utt_1"],
        intents=[],
        rationale="",
        steps=steps or [step(payee_id="pye_b")],
    )


def context(steps=None):
    ctx = EvalContext(
        plan_draft=PlanDraft.model_validate(draft(steps)),
        customer_id="cus_a",
        session_id="sid_1",
        plan_id="pln_1",
        origin="agent",
    )
    ctx._lazy_state = banking_state()
    return ctx


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(extra="forged"),
        lambda p: p.update(steps=[]),
        lambda p: p["steps"][0].update(depends_on=["future"]),
        lambda p: p["steps"][0].update(
            arg_sources=[
                {
                    "param": "amount_cents",
                    "source": {"kind": "utterance", "ref": "utt_1", "span": [0, 1]},
                },
                {
                    "param": "amount_cents",
                    "source": {"kind": "utterance", "ref": "utt_1", "span": [0, 1]},
                },
            ]
        ),
    ],
)
def test_contract_revalidates_raw_inputs(mutate):
    body = draft()
    mutate(body)
    ctx = EvalContext(plan_draft=body)
    with pytest.raises(GatewayError) as exc:
        asyncio.run(ContractStage().run(ctx))
    assert exc.value.code is Code.CONTRACT_INVALID
    assert exc.value.status == 422


def test_contract_accepts_valid_and_does_not_read_llm_claims():
    ctx = EvalContext(plan_draft=draft())
    asyncio.run(ContractStage().run(ctx))
    assert isinstance(ctx.plan_draft, PlanDraft)


def test_entity_rejects_other_customer_without_exposing_entity():
    ctx = context([step(from_account_id="acc_b", payee_id="pye_b")])
    asyncio.run(EntityStage().run(ctx))
    assert any(f.code is Code.NOT_FOUND for f in ctx.findings)
    assert ctx.steps == ()


def test_entity_requires_read_ledger_and_gives_agent_read_hint():
    ctx = context()
    asyncio.run(EntityStage().run(ctx))
    assert any(f.code is Code.ENTITY_UNSEEN and f.resolver == "agent" for f in ctx.findings)
    assert ctx.steps[0].action_kind == "TRANSFER_EXTERNAL"
    assert ctx.steps[0].outflow is True


def test_entity_app_form_skips_read_visibility_only():
    ctx = context()
    ctx.origin = "app_form"
    asyncio.run(EntityStage().run(ctx))
    assert not ctx.findings
    assert ctx.steps[0].base_risk == "YELLOW"


def test_reference_reuses_existing_fingerprint_and_creation_cannot_throw():
    add = step(
        "add_payee",
        name="乙",
        account_no="6222020000000003",
        bank_name="bank",
        phone=None,
        alias=None,
    )
    add["step_id"] = "s1"
    move = step(payee_id={"ref": "s1"})
    move.update(step_id="s2", depends_on=["s1"])
    ctx = context([add, move])
    ctx.origin = "app_form"
    asyncio.run(EntityStage().run(ctx))
    assert ctx.steps[1].args["payee_id"] == "pye_b"


def test_card_metadata_uses_cumulative_bankcore_state():
    a = step(
        "update_card_limits", card_id="crd_a", single_limit_cents=100000, daily_limit_cents=None
    )
    b = step(
        "update_card_limits", card_id="crd_a", single_limit_cents=200000, daily_limit_cents=None
    )
    a.update(step_id="s1")
    b.update(step_id="s2", depends_on=["s1"])
    ctx = context([a, b])
    ctx.origin = "app_form"
    asyncio.run(EntityStage().run(ctx))
    assert [s.action_kind for s in ctx.steps] == ["CARD_LIMIT_TIGHTEN", "CARD_LIMIT_LOOSEN"]


def test_app_form_contract_rejects_extra_steps_and_too_many_actions():
    from services.gateway.app.schemas.requests import AppFormPlanRequest

    for body in (
        {"request_id": "r", "session_id": "s", "form_action_ids": [], "steps": []},
        {"request_id": "r", "session_id": "s", "form_action_ids": ["a"] * 6},
    ):
        with pytest.raises(ValueError):
            AppFormPlanRequest.model_validate(body)


def test_failed_add_payee_does_not_crash_or_create_reference():
    from safebank_bankcore.ledger import replace_state
    from safebank_contracts.state import DirectoryEntry

    add = step(
        "add_payee",
        name="自己",
        account_no="6222020000000001",
        bank_name="bank",
        phone=None,
        alias=None,
    )
    ctx = context([add])
    ctx.origin = "app_form"
    ctx._lazy_state = replace_state(
        ctx._lazy_state,
        directory={
            "number:6222020000000001": DirectoryEntry(
                target_fingerprint="int:acc_a", target_account_id="acc_a", customer_id="cus_a"
            )
        },
    )
    asyncio.run(EntityStage().run(ctx))
    assert len(ctx.steps) == 1  # business conflict remains for preflight, no crash


def test_origin_policy_rejects_agent_assessment_but_accepts_form():
    for origin, denied in (("agent", True), ("app_form", False)):
        ctx = context([step("submit_risk_assessment")])
        ctx.origin = origin
        asyncio.run(EntityStage().run(ctx))
        assert any(f.code is Code.ORIGIN_NOT_ALLOWED for f in ctx.findings) is denied


def test_agent_visible_owned_ids_do_not_trigger_unseen(monkeypatch):
    async def records(*_):
        return {"src_1": {"response": {"ids": ["acc_a", "pye_b"]}, "untrusted_paths": []}}

    monkeypatch.setattr("services.gateway.app.pipeline.entity.load_records", records)
    ctx = context()
    asyncio.run(EntityStage().run(ctx))
    assert not ctx.findings
