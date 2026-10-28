"""L1 exhausted -> clarify: when PlanAdapter retries are exhausted and all
remaining problems are "user-only missing" params, produce a clarification
instead of raising ReferenceError.
"""

from __future__ import annotations

import pytest

from services.agent.app.graph.graph import _route_plan_after
from services.agent.app.graph.state import AgentContext, Understanding, UtteranceRef
from services.agent.app.llm.client import make_llm as _make_llm
from services.agent.app.llm.plan import (
    ExhaustedClarify,  # stub — RED until PlanAdapter raises it
    PlanAdapter,
    ReferenceError,
)
from tests.agent.llm_mock import ScriptedLLM, tool_response

API_KEY = "test-key"


def ctx(customer="cus_a", session="ses_a") -> AgentContext:
    return AgentContext(assistant_token="SECRET_TOKEN", customer_id=customer, session_id=session)


def _add_payee_state():
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [
            UtteranceRef(
                id="utt_typed", kind="typed", text="把李阿姨加为收款人，卡号6222020000004444"
            ),
            UtteranceRef(id="utt_ctx", kind="context", text=""),
        ],
        "understanding": Understanding(intents=["add_payee"], needs_write=True),
        "reads": [],
    }


def _transfer_state():
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [
            UtteranceRef(id="utt_typed", kind="typed", text="给王五转800元"),
            UtteranceRef(id="utt_ctx", kind="context", text="页面位置"),
        ],
        "understanding": Understanding(intents=["transfer"], needs_write=True),
        "reads": [],
    }


def _strict_add_payee(**overrides) -> dict:
    """Strict-plan dict for add_payee.  Default bank_name=__NULL__."""
    base = {
        "intents": ["add_payee"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "add_payee",
                "args": {
                    "name": "李阿姨",
                    "account_no": "6222020000004444",
                    "bank_name": "__NULL__",
                    "phone": "__NULL__",
                    "alias": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        "rationale": "用户要求新增收款人",
    }
    for k, v in overrides.items():
        base["steps"][0]["args"][k] = v
    return base


def _strict_transfer(from_account_id="fake_", **overrides) -> dict:
    """Strict-plan dict for transfer with a deliberately wrong id prefix."""
    base = {
        "intents": ["transfer"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "transfer",
                "args": {
                    "from_account_id": from_account_id,
                    "payee_id": {"kind": "id", "id": "pye_b"},
                    "to_account_id": {"kind": "none"},
                    "amount_cents": 80000,
                    "memo": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        "rationale": "test",
    }
    for k, v in overrides.items():
        base["steps"][0]["args"][k] = v
    return base


# ═══════════════════════════════════════════════════════════════════════════
# RED: bank_name=__NULL__ exhausted → ExhaustedClarify
# ═══════════════════════════════════════════════════════════════════════════


def test_bank_name_null_exhausted_raises_exhausted_clarify():
    """Two consecutive add_payee plans with bank_name=__NULL__ → ExhaustedClarify."""
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", _strict_add_payee()),
            tool_response("submit_action_plan", _strict_add_payee()),
        ]
    )
    llm = _make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ExhaustedClarify) as exc_info:
        adapter(_add_payee_state(), ctx())
    assert "bank_name" in exc_info.value.fields
    assert "开户银行" in exc_info.value.labels


def test_name_null_exhausted_raises_exhausted_clarify():
    """Two add_payee plans with name=__NULL__ → ExhaustedClarify."""
    plan = _strict_add_payee(bank_name="中国银行")
    plan["steps"][0]["args"]["name"] = "__NULL__"
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", plan),
            tool_response("submit_action_plan", plan),
        ]
    )
    llm = _make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ExhaustedClarify) as exc_info:
        adapter(_add_payee_state(), ctx())
    assert "name" in exc_info.value.fields
    assert "收款人姓名" in exc_info.value.labels


# ═══════════════════════════════════════════════════════════════════════════
# RED: ID prefix error exhausted → still ReferenceError (original path)
# ═══════════════════════════════════════════════════════════════════════════


def test_id_prefix_error_exhausted_still_raises_reference_error():
    """Two transfer plans with wrong from_account_id prefix → ReferenceError (not clarify)."""
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", _strict_transfer(from_account_id="crd_zs_debit")),
            tool_response("submit_action_plan", _strict_transfer(from_account_id="crd_zs_debit")),
        ]
    )
    llm = _make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ReferenceError) as exc_info:
        adapter(_transfer_state(), ctx())
    assert "from_account_id" in str(exc_info.value)
    # Must NOT be ExhaustedClarify
    assert not isinstance(exc_info.value, ExhaustedClarify)


# ═══════════════════════════════════════════════════════════════════════════
# RED: mixed problems (user-only + other) → still ReferenceError
# ═══════════════════════════════════════════════════════════════════════════


def test_mixed_id_prefix_and_null_yields_reference_error():
    """Plan has a wrong id prefix (NOT user-only) → ReferenceError even if another field is null.

    Uses a transfer plan with from_account_id='crd_zs_debit' (wrong prefix, caught by
    _required_value_problems as an ID prefix error) and adds a separate add_payee
    step with bank_name=__NULL__ so both val_problem types coexist."""
    plan = {
        "intents": ["transfer"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "transfer",
                "args": {
                    "from_account_id": "crd_zs_debit",  # wrong prefix → not user-only
                    "payee_id": {"kind": "id", "id": "pye_b"},
                    "to_account_id": {"kind": "none"},
                    "amount_cents": 80000,
                    "memo": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        "rationale": "test",
    }
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", plan),
            tool_response("submit_action_plan", plan),
        ]
    )
    llm = _make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ReferenceError) as exc_info:
        adapter(_transfer_state(), ctx())
    # Must be ReferenceError, not ExhaustedClarify
    assert "from_account_id" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: amount_cents / account_no null → ExhaustedClarify (fixed)
#
# When the model fills amount_cents="__NULL__" or account_no="__NULL__" the
# Pydantic coercion/regex fails BEFORE _required_value_problems can inspect
# the value.  PlanAdapter now inspects the ValidationError.errors() on the
# final attempt and converts it to ExhaustedClarify when every error targets
# a _USER_ONLY_PARAMS field with null-ish input.
# ═══════════════════════════════════════════════════════════════════════════


def test_amount_cents_null_exhausted_raises_exhausted_clarify():
    """Two transfer plans with amount_cents=__NULL__ → ExhaustedClarify."""
    plan1 = _strict_transfer(from_account_id="acc_zs_debit", amount_cents="__NULL__")
    plan2 = _strict_transfer(from_account_id="acc_zs_debit", amount_cents="__NULL__")
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", plan1),
            tool_response("submit_action_plan", plan2),
        ]
    )
    llm = _make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ExhaustedClarify) as exc_info:
        adapter(_transfer_state(), ctx())
    assert "amount_cents" in exc_info.value.fields
    assert "转账金额" in exc_info.value.labels


def test_account_no_null_exhausted_raises_exhausted_clarify():
    """Two add_payee plans with account_no=__NULL__ → ExhaustedClarify."""
    plan = _strict_add_payee(bank_name="中国银行")
    plan["steps"][0]["args"]["account_no"] = "__NULL__"
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", plan),
            tool_response("submit_action_plan", plan),
        ]
    )
    llm = _make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ExhaustedClarify) as exc_info:
        adapter(_add_payee_state(), ctx())
    assert "account_no" in exc_info.value.fields
    assert "收款账号" in exc_info.value.labels


# ═══════════════════════════════════════════════════════════════════════════
# Anti-example: non-null invalid value still re-raises ValidationError
# ═══════════════════════════════════════════════════════════════════════════


def test_amount_cents_invalid_non_null_still_raises_validation_error():
    """amount_cents="abc" (real garbage, not __NULL__) → ValidationError on final attempt.

    The model attempted to provide a value, it was just malformed — this is NOT
    a missing-info case and should stay a hard error.
    """
    from pydantic import ValidationError

    plan1 = _strict_transfer(from_account_id="acc_zs_debit", amount_cents="abc")
    plan2 = _strict_transfer(from_account_id="acc_zs_debit", amount_cents="abc")
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", plan1),
            tool_response("submit_action_plan", plan2),
        ]
    )
    llm = _make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ValidationError):
        adapter(_transfer_state(), ctx())


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: non-user field exhausted → ReferenceError (not ExhaustedClarify)
# ═══════════════════════════════════════════════════════════════════════════


def test_non_user_field_null_exhausted_still_raises_reference_error():
    """Two transfer plans with from_account_id=__NULL__ (not user-only)
    → ReferenceError, NOT ExhaustedClarify."""
    plan1 = _strict_transfer(from_account_id="__NULL__")
    plan2 = _strict_transfer(from_account_id="__NULL__")
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", plan1),
            tool_response("submit_action_plan", plan2),
        ]
    )
    llm = _make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ReferenceError) as exc_info:
        adapter(_transfer_state(), ctx())
    assert "from_account_id" in str(exc_info.value)
    assert not isinstance(exc_info.value, ExhaustedClarify)


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: _route_plan_after routing
# ═══════════════════════════════════════════════════════════════════════════


def test_route_plan_after_l1_exhausted_returns_respond():
    """_route_plan_after: source="l1_exhausted" → "respond"."""
    state = {"clarification": {"required_fields": ["转账金额"], "source": "l1_exhausted"}}
    assert _route_plan_after(state) == "respond"


def test_route_plan_after_no_source_returns_evaluate():
    """_route_plan_after: no clarification → "evaluate"."""
    state = {}
    assert _route_plan_after(state) == "evaluate"


def test_route_plan_after_non_exhausted_source_returns_evaluate():
    """_route_plan_after: source != "l1_exhausted" → "evaluate"."""
    state = {"clarification": {"source": "l2_form_required"}}
    assert _route_plan_after(state) == "evaluate"


class _FakeVE:
    def __init__(self, errors):
        self._errors = errors

    def errors(self):
        return self._errors


def test_other_structural_error_beside_user_only_null_does_not_clarify():
    """Review P2-1: only the ('steps',) too_short cascade may accompany the null field."""
    from services.agent.app.llm.plan import _exhausted_clarify_fields_from_ve

    null_amount = {
        "loc": ("steps", 0, "transfer", "args", "amount_cents"),
        "type": "value_error",
        "input": "__NULL__",
    }
    cascade = {"loc": ("steps",), "type": "too_short", "input": []}
    structural = {"loc": ("steps", 1), "type": "union_tag_invalid", "input": {}}
    assert _exhausted_clarify_fields_from_ve(_FakeVE([null_amount, cascade])) == ["amount_cents"]
    assert _exhausted_clarify_fields_from_ve(_FakeVE([null_amount, structural])) is None
