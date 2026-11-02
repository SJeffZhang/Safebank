"""REPLAN amounts remain attached to the current gateway hint, never global facts."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.respond import RespondAdapter
from services.agent.app.llm.verify import OutputVerifier
from tests.agent.test_execution_receipt_facts import transfer_step


class RepairToDailyLimit:
    def __init__(self):
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return AIMessage(content="超出单笔限额，最多50,000.00元")


def replan_state():
    step = transfer_step(amount=5000000)
    return {
        "session_id": "ses_current",
        "plan_id": "pln_current",
        "plan_draft": {
            "request_id": "req_replan",
            "session_id": "ses_current",
            "utterance_ids": ["utt_one"],
            "intents": ["transfer"],
            "rationale": "",
            "steps": [step],
        },
        "reads": [
            ReadRecord(
                tool="get_accounts",
                source_ref="src_accounts",
                data={
                    "daily_outflow_limit_cents": 5000000,
                    "accounts": [{"available_cents": 610000}],
                },
            ),
            ReadRecord(
                tool="list_payees",
                source_ref="src_payees",
                data={"items": [{"id": "pye_wang", "target_fingerprint": "fp_wang"}]},
            ),
        ],
        "decision": {
            "plan_id": "pln_current",
            "evaluation_id": "evl_current",
            "revision": 1,
            "decision": "REPLAN",
            "risk": "RED",
            "approval_id": None,
            "findings": [
                {
                    "code": "CARD_SINGLE_LIMIT",
                    "effect": "REPLAN",
                    "resolver": "user",
                    "step_id": "step_one",
                }
            ],
            "replan_hints": [
                {
                    "id": "hnt_current",
                    "label": "最多 1,000.00 元",
                    "expires_at_wall": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    "scope": {
                        "action_kinds": ["TRANSFER_EXTERNAL"],
                        "from_account_id": "acc_checking",
                        "transfers": [{"target_fingerprint": "fp_wang", "amount_cents": 100000}],
                    },
                }
            ],
        },
    }


def verify(state, llm):
    state["reply_draft"] = RespondAdapter(llm)(state, None)
    return OutputVerifier(llm)(state, None)


def test_current_hint_cannot_be_repaired_into_an_unrelated_daily_limit():
    state = replan_state()
    before = deepcopy(state)
    llm = RepairToDailyLimit()
    reply = verify(state, llm)
    assert "1000.00元" in reply.replace(",", "").replace(" ", "")
    assert "50000" not in reply.replace(",", "")
    assert llm.calls == 0
    assert {key: value for key, value in state.items() if key != "reply_draft"} == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "wrong_plan",
        "wrong_session",
        "other_target",
        "other_source",
        "enlarge",
        "label_swap",
    ],
)
def test_unbound_or_fabricated_hint_never_produces_a_maximum(fault):
    state = replan_state()
    decision = state["decision"]
    hint = decision["replan_hints"][0]
    if fault == "missing":
        decision["replan_hints"] = []
    elif fault == "wrong_plan":
        decision["plan_id"] = "pln_old"
    elif fault == "wrong_session":
        state["plan_draft"]["session_id"] = "ses_old"
    elif fault == "other_target":
        hint["scope"]["transfers"][0]["target_fingerprint"] = "fp_someone_else"
    elif fault == "other_source":
        hint["scope"]["from_account_id"] = "acc_other"
    elif fault == "enlarge":
        hint["scope"]["transfers"][0]["amount_cents"] = 5000001
    else:
        hint["label"] = "最多50,000.00元"
    llm = RepairToDailyLimit()
    reply = verify(state, llm)
    if fault == "label_swap":
        assert "1000.00元" in reply.replace(",", "").replace(" ", "")
    else:
        assert "最多" not in reply
    assert "50000" not in reply.replace(",", "")
    assert llm.calls == 0


def test_multi_step_hint_amounts_stay_with_their_original_step_even_if_label_is_swapped():
    state = replan_state()
    second = transfer_step(step_id="step_two", amount=7000000, payee="pye_zhao")
    state["plan_draft"]["steps"].append(second)
    state["decision"]["findings"].append(
        {"code": "CARD_SINGLE_LIMIT", "effect": "REPLAN", "resolver": "user", "step_id": "step_two"}
    )
    state["reads"][1].data["items"].append({"id": "pye_zhao", "target_fingerprint": "fp_zhao"})
    hint = state["decision"]["replan_hints"][0]
    hint["label"] = "第一笔300元，第二笔1000元"
    hint["scope"]["transfers"].append({"target_fingerprint": "fp_zhao", "amount_cents": 30000})
    llm = RepairToDailyLimit()
    reply = verify(state, llm)
    lines = reply.splitlines()
    assert any("第1步" in line and "1000.00元" in line for line in lines)
    assert any("第2步" in line and "300.00元" in line for line in lines)
    assert llm.calls == 0


@pytest.mark.parametrize(
    "fault", ["zero", "float", "bool", "unknown_action", "ambiguous_payee", "scope_exceeded"]
)
def test_structurally_invalid_or_unsafe_hints_are_not_reported_as_valid_maxima(fault):
    state = replan_state()
    hint = state["decision"]["replan_hints"][0]
    transfer = hint["scope"]["transfers"][0]
    if fault in {"zero", "float", "bool"}:
        transfer["amount_cents"] = {"zero": 0, "float": 100000.0, "bool": True}[fault]
    elif fault == "unknown_action":
        hint["scope"]["action_kinds"].append("PAYEE_ADD")
    elif fault == "ambiguous_payee":
        state["reads"][1].data["items"].append({"id": "pye_wang", "target_fingerprint": "fp_other"})
    else:
        state["decision"]["findings"][0]["code"] = "INTENT_SCOPE_EXCEEDED"
    llm = RepairToDailyLimit()
    reply = verify(state, llm)
    assert "最多" not in reply
    assert llm.calls == 0


def test_gateway_preflight_hint_reaches_the_final_reply_without_amount_repair():
    """Exercise production stages, not a prebuilt hint or its free-text label."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from safebank_bankcore.ledger import replace_state

    from services.gateway.app.pipeline.decision import DecisionStage
    from services.gateway.app.schemas.enums import ParseStatus, UtteranceStatus
    from services.gateway.app.schemas.scope import (
        AuthorizationInputs,
        NormalizedScope,
        ScopeTransfer,
        UtteranceRef,
    )
    from tests.gateway.w3a.conftest import build_ctx, run_entity, transfer, w3_state
    from tests.gateway.w3a.test_preflight import run

    before = w3_state(reserve=23000)
    card = before.cards["crd_a"].model_copy(update={"single_limit_cents": 100000})
    bank = replace_state(before, cards=dict(before.cards) | {card.id: card})
    ctx = run_entity(build_ctx([transfer("pye_ext", 5000000)], state=bank, plan_id="pln_current"))
    ctx.snapshot = bank
    now = datetime.now(UTC)
    utterance = UtteranceRef(
        id="utt_one",
        kind="typed",
        text="给王五转50000元",
        scope=NormalizedScope(
            action_kinds=("TRANSFER_EXTERNAL",),
            from_account_id="acc_a",
            transfers=(
                ScopeTransfer(
                    target_fingerprint="pye_extfp", amount_cents=5000000, from_account_id="acc_a"
                ),
            ),
        ),
        parse_status=ParseStatus.SCOPED,
        created_at_wall=now.isoformat(),
        expires_at_wall=(now + timedelta(minutes=10)).isoformat(),
    )
    ctx.authorization = AuthorizationInputs(
        sid="sid_1",
        customer_id="cus_a",
        utterance_ids=("utt_one",),
        utterances={"utt_one": utterance},
        scope_ids=("utt_one",),
        statuses={"utt_one": UtteranceStatus.ACTIVE},
    )
    run(ctx)
    asyncio.run(DecisionStage().run(ctx))
    assert ctx.decision.replan_hints[0].scope.transfers[0].amount_cents == 100000
    state = {
        "session_id": "sid_1",
        "plan_id": "pln_current",
        "plan_draft": ctx.plan_draft,
        "decision": ctx.decision.model_dump(mode="json"),
        "reads": [
            ReadRecord(
                tool="get_accounts",
                source_ref="src_accounts",
                data={"daily_outflow_limit_cents": 5000000},
            ),
            ReadRecord(
                tool="list_payees",
                source_ref="src_payees",
                data={"items": [{"id": "pye_ext", "target_fingerprint": "pye_extfp"}]},
            ),
        ],
    }
    llm = RepairToDailyLimit()
    reply = verify(state, llm)
    assert "1000.00元" in reply
    assert "50000" not in reply.replace(",", "")
    assert llm.calls == 0


def test_self_transfer_hint_preserves_the_bound_destination_field():
    state = replan_state()
    args = state["plan_draft"]["steps"][0]["args"]
    args.update(payee_id=None, to_account_id="acc_savings")
    state["decision"]["findings"][0]["code"] = "INSUFFICIENT_FUNDS"
    scope = state["decision"]["replan_hints"][0]["scope"]
    scope.update(action_kinds=["TRANSFER_SELF"], fields={"to_account_id": "acc_savings"})
    scope["transfers"][0]["target_fingerprint"] = "int:acc_savings"
    llm = RepairToDailyLimit()
    reply = verify(state, llm)
    assert "1000.00元" in reply
    assert llm.calls == 0


def test_malformed_self_destination_field_cannot_crash_or_create_a_maximum():
    state = replan_state()
    args = state["plan_draft"]["steps"][0]["args"]
    args.update(payee_id=None, to_account_id="acc_savings")
    scope = state["decision"]["replan_hints"][0]["scope"]
    scope.update(action_kinds=["TRANSFER_SELF"], fields={"to_account_id": ["acc_savings"]})
    scope["transfers"][0]["target_fingerprint"] = "int:acc_savings"
    llm = RepairToDailyLimit()
    reply = verify(state, llm)
    assert "最多" not in reply
    assert llm.calls == 0


@pytest.mark.parametrize("step_id", [None, "not_in_this_plan"])
def test_replan_maximum_requires_a_finding_for_an_actual_transfer_step(step_id):
    current = replan_state()
    current["decision"]["findings"][0]["step_id"] = step_id
    llm = RepairToDailyLimit()
    assert "最多" not in verify(current, llm)
    assert llm.calls == 0


def test_hint_cannot_shrink_an_unfailed_step_instead_of_the_actual_failed_step():
    current = replan_state()
    current["plan_draft"]["steps"].append(
        transfer_step(step_id="step_two", amount=7000000, payee="pye_zhao")
    )
    current["reads"][1].data["items"].append({"id": "pye_zhao", "target_fingerprint": "fp_zhao"})
    scope = current["decision"]["replan_hints"][0]["scope"]
    scope["transfers"][0]["amount_cents"] = 5000000
    scope["transfers"].append({"target_fingerprint": "fp_zhao", "amount_cents": 30000})
    before = deepcopy(current)
    assert "最多" not in verify(current, RepairToDailyLimit())
    assert {key: value for key, value in current.items() if key != "reply_draft"} == before


@pytest.mark.parametrize(
    "expires", ["2000-01-01T00:00:00Z", "2099-01-01T00:00:00", "invalid-date", None]
)
def test_missing_invalid_naive_or_expired_wall_ttl_never_yields_a_current_maximum(expires):
    current = replan_state()
    current["reads"].append(
        ReadRecord(
            tool="get_profile", source_ref="src_sim_clock", data={"sim_now": "1999-01-01T00:00:00Z"}
        )
    )
    hint = current["decision"]["replan_hints"][0]
    if expires is None:
        hint.pop("expires_at_wall")
    else:
        hint["expires_at_wall"] = expires
    llm = RepairToDailyLimit()
    assert "最多" not in verify(current, llm)
    assert llm.calls == 0


def test_valid_nullable_source_self_hint_keeps_only_the_matching_failed_step_maximum():
    current = replan_state()
    current["plan_draft"]["steps"][0]["args"].update(payee_id=None, to_account_id="acc_savings")
    current["decision"]["findings"][0]["code"] = "INSUFFICIENT_FUNDS"
    scope = current["decision"]["replan_hints"][0]["scope"]
    scope.update(
        action_kinds=["TRANSFER_SELF"],
        from_account_id=None,
        fields={"to_account_id": "acc_savings"},
    )
    scope["transfers"][0].update(target_fingerprint="int:acc_savings", from_account_id=None)
    current["reads"].append(
        ReadRecord(
            tool="get_profile",
            source_ref="src_future_sim_clock",
            data={"sim_now": "2100-01-01T00:00:00Z"},
        )
    )
    llm = RepairToDailyLimit()
    assert "1000.00元" in verify(current, llm)
    assert llm.calls == 0
