"""T-1: only a complete current-customer saved phone identifies a payee."""

import pytest

from services.gateway.app.taint.text import matches
from services.gateway.app.utterance.parser import parse
from tests.gateway.w1.fixtures import bank_state

PHONE = "13800001111"


def phone_state():
    state = bank_state()
    payees = dict(state.payees)
    payees["pye_ww"] = payees["pye_ww"].model_copy(update={"phone": PHONE})
    return state.model_copy(update={"payees": payees})


@pytest.mark.parametrize(
    "text",
    [
        f"给{PHONE}转100元",
        f"向{PHONE}转账100元",
        f"把100元转给{PHONE}",
        f"给手机号{PHONE}转100元",
        f"给手机号码{PHONE}转100元",
        "给１３８００００１１１１转100元",
    ],
)
def test_typed_transfer_exact_phone_uses_saved_payee_fingerprint(text):
    result = parse(text, phone_state(), "cus_zs")
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("TRANSFER_EXTERNAL",)
    assert [
        (t.from_account_id, t.target_fingerprint, t.amount_cents) for t in result.scope.transfers
    ] == [("acc_zs", "fp_ww", 10000)]
    assert not result.required_fields


def test_known_phone_can_be_a_scheduled_transfer_target():
    result = parse(f"每月20号给{PHONE}转100元", phone_state(), "cus_zs")
    assert result.status == "SCOPED" and result.scope.action_kinds == ("SCHEDULE_CREATE",)
    assert result.scope.transfers[0].target_fingerprint == "fp_ww"
    assert result.scope.fields["first_run_at"] == "2026-10-20T08:00:00+08:00"


def test_duplicate_phone_requires_owned_payee_clarification():
    state = phone_state()
    payees = dict(state.payees)
    payees["pye_wu"] = payees["pye_wu"].model_copy(update={"phone": PHONE})
    result = parse(f"给{PHONE}转100元", state.model_copy(update={"payees": payees}), "cus_zs")
    assert result.status == "CLARIFYING"
    assert result.required_fields == ["payee_id"] and not result.scope.transfers
    assert {c["entity_id"] for c in result.candidates} == {"pye_ww", "pye_wu"}


@pytest.mark.parametrize(
    "number",
    ["13800002222", "3800001111", "138000011110", "138****1111", "138 0000 1111", "138-0000-1111"],
)
def test_unregistered_partial_masked_or_reformatted_number_does_not_authorize(number):
    assert parse(f"给{number}转100元", phone_state(), "cus_zs").status != "SCOPED"


@pytest.mark.parametrize(
    "text",
    [
        f"不要给{PHONE}转100元",
        f"别给{PHONE}转100元",
        f"给{PHONE}转100元吗",
        f"如果余额够就给{PHONE}转100元",
        f"“给{PHONE}转100元”",
        f"他说给{PHONE}转100元",
        f"取消给{PHONE}转100元",
        "取消这笔转账",
        "算了",
    ],
)
def test_phone_matching_keeps_quote_question_negation_and_cancellation_guards(text):
    result = parse(text, phone_state(), "cus_zs")
    assert result.status != "SCOPED" and not result.scope.transfers


@pytest.mark.parametrize(
    "mutation", ["no_saved_phone", "other_customer", "numeric_alias", "account_number"]
)
def test_phone_never_uses_unowned_or_other_contact_fields(mutation):
    state = phone_state()
    payees = dict(state.payees)
    updates = {"phone": None}
    if mutation == "other_customer":
        updates = {"customer_id": "cus_ls"}
    elif mutation == "numeric_alias":
        updates["alias"] = PHONE
    elif mutation == "account_number":
        updates["account_no"] = PHONE
    payees["pye_ww"] = payees["pye_ww"].model_copy(update=updates)
    state = state.model_copy(update={"payees": payees})
    assert parse(f"给{PHONE}转100元", state, "cus_zs").status != "SCOPED"


def test_phone_span_proves_only_the_unique_owned_payee_id():
    state = phone_state()
    text = f"给{PHONE}转100元"
    assert matches("pye_ww", text[1:12], "payee_id", state, "cus_zs")
    assert not matches("pye_wu", text[1:12], "payee_id", state, "cus_zs")
    assert not matches("pye_ww", text[1:12], "payee_id", state, "cus_ls")
    for param in ("account_id", "product_id", "participant_payee_ids", "phone"):
        assert not matches("pye_ww", text[1:12], param, state, "cus_zs")
    for span in (text[1:11], text[1:13], "手机号" + PHONE, "138 0000 1111"):
        assert not matches("pye_ww", span, "payee_id", state, "cus_zs")
    payees = dict(state.payees)
    payees["pye_wu"] = payees["pye_wu"].model_copy(update={"phone": PHONE})
    duplicate = state.model_copy(update={"payees": payees})
    assert not matches("pye_ww", PHONE, "payee_id", duplicate, "cus_zs")
