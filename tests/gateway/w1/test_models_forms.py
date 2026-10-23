import pytest
from pydantic import ValidationError

from services.gateway.app.errors import GatewayError
from services.gateway.app.policy.registry import action_kind
from services.gateway.app.utterance.models import ActionRequest, Registration
from services.gateway.app.utterance.service import form_tool
from tests.contracts.samples import WRITE_GOOD
from tests.gateway.w1.fixtures import bank_state


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "typed", "text": "给王五800", "hint_id": "hnt_x"},
        {"kind": "hint", "hint_id": "hnt_x", "text": "伪造标签"},
        {"kind": "action", "action_id": "act_x", "text": "把金额改大"},
        {"kind": "context", "text": "给王五800"},
        {"kind": "typed", "text": "", "clarification_id": None},
        {"kind": "hint", "hint_id": "hnt_x", "clarification_id": "clr_x"},
        {"kind": "typed", "text": "你好", "origin": "app_form"},
    ],
)
def test_kind_mutual_exclusion(body):
    with pytest.raises(ValidationError):
        Registration.model_validate({"request_id": "req_x", **body})


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "FORM", "action_kind": None, "fields": {}},
        {
            "kind": "FORM",
            "action_kind": "CARD_FREEZE",
            "fields": {},
            "parent_utterance_id": "utt_x",
        },
        {
            "kind": "CLARIFICATION",
            "action_kind": "TRANSFER_EXTERNAL",
            "fields": [],
            "parent_utterance_id": "utt_x",
        },
        {"kind": "CLARIFICATION", "action_kind": None, "fields": {}},
        {"kind": "CLARIFICATION", "action_kind": None, "fields": [], "parent_utterance_id": None},
    ],
)
def test_action_discriminator(body):
    with pytest.raises(ValidationError):
        ActionRequest.model_validate({"request_id": "req_x", **body})


@pytest.mark.parametrize("tool", WRITE_GOOD)
def test_form_uses_frozen_tools_and_registry(tool):
    bank = bank_state()
    args = {**WRITE_GOOD[tool]}
    for key in ("card_id", "from_account_id", "account_id", "payee_id"):
        if key in args:
            args[key] = {
                "card_id": "crd_zs",
                "from_account_id": "acc_zs",
                "account_id": "acc_zs",
                "payee_id": "pye_ww",
            }[key]
    kind = action_kind(tool, args, bank)
    actual_tool, normalized = form_tool(kind, args, bank)
    assert actual_tool == tool
    assert action_kind(actual_tool, normalized, bank) == kind
    with pytest.raises(GatewayError):
        form_tool(kind, {**args, "extra_step": "transfer"}, bank)


def test_card_direction_and_entity_fail_closed():
    bank = bank_state()
    with pytest.raises(GatewayError):
        form_tool(
            "CARD_LIMIT_TIGHTEN",
            {"card_id": "crd_zs", "single_limit_cents": 999999, "daily_limit_cents": None},
            bank,
        )
    with pytest.raises(GatewayError):
        form_tool(
            "CARD_LIMIT_TIGHTEN",
            {"card_id": "crd_other", "single_limit_cents": 999999, "daily_limit_cents": None},
            bank,
        )
