"""Saved target identity must not donate its trailing numeral to the amount."""

import pytest

from services.gateway.app.utterance.parser import parse, scope_is_complete
from tests.gateway.w1.fixtures import bank_state


def target_state(name="赵六", *, alias=None, phone=None):
    bank = bank_state()
    payees = dict(bank.payees)
    payees["pye_zl"] = payees["pye_zl"].model_copy(
        update={"name": name, "alias": alias, "phone": phone}
    )
    return bank.model_copy(update={"payees": payees})


@pytest.mark.parametrize("name", ["王五", "李四", "赵六", "孙八"])
@pytest.mark.parametrize("money", ["九百", "九百元", "900"])
@pytest.mark.parametrize(
    "template",
    [
        "给{target}{money}",
        "向{target}转账{money}",
        "把{money}转给{target}",
        "转{money}给{target}",
        "不对，改成给{target}{money}",
        "从储蓄给{target}{money}",
        "每月20号给{target}{money}",
        "每周一给{target}{money}",
    ],
)
def test_unique_saved_target_consumes_its_complete_name_before_money(name, money, template):
    bank = target_state(name)
    # 王五 occurs twice in this synthetic variant; use a single exact target.
    if name == "王五":
        bank = bank.model_copy(update={"payees": {"pye_zl": bank.payees["pye_zl"]}})
    result = parse(template.format(target=name, money=money), bank, "cus_zs")
    assert result.status == "SCOPED"
    assert len(result.scope.transfers) == 1
    assert result.scope.transfers[0].target_fingerprint == "fp_zl"
    assert result.scope.transfers[0].amount_cents == 90000
    if template.startswith("从储蓄"):
        assert result.scope.from_account_id == "acc_save"


@pytest.mark.parametrize("target", ["老六", "13800001111", "手机号13800001111"])
@pytest.mark.parametrize("money", ["九百", "九百元", "900"])
def test_unique_saved_alias_and_phone_have_the_same_amount_boundary(target, money):
    result = parse(f"给{target}{money}", target_state(alias="老六", phone="13800001111"), "cus_zs")
    assert result.status == "SCOPED"
    assert result.scope.transfers[0].target_fingerprint == "fp_zl"
    assert result.scope.transfers[0].amount_cents == 90000


@pytest.mark.parametrize("target", ["赵", "赵六"])
@pytest.mark.parametrize("prefix", ["", "不对，改成", "每月20号", "从储蓄"])
def test_two_registered_target_amount_splits_have_no_write_scope(target, prefix):
    bank = target_state()
    payees = dict(bank.payees)
    payees["pye_ww"] = payees["pye_ww"].model_copy(update={"name": target})
    # Both 赵 + 六九百 and 赵六 + 九百 match the old numeral grammar.
    # If the duplicate is 赵六, expose the alternative short spelling as its alias.
    if target == "赵六":
        payees["pye_ww"] = payees["pye_ww"].model_copy(update={"alias": "赵"})
    result = parse(f"{prefix}给赵六九百", bank.model_copy(update={"payees": payees}), "cus_zs")
    assert result.status == "INTENT_UNCLEAR"
    assert result.scope.action_kinds == () and not result.scope.transfers
    assert result.required_fields == [] and result.candidates == []


@pytest.mark.parametrize("text", ["给王八九百", "给王吾九百", "给王九百"])
def test_unresolved_adjacent_chinese_amount_cannot_feed_name_prefix_candidates(text):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status == "INTENT_UNCLEAR"
    assert result.scope.action_kinds == () and not result.scope.transfers
    assert result.required_fields == [] and result.candidates == []


@pytest.mark.parametrize("text", ["给王转九百", "给王 九百", "把九百转给王"])
def test_explicit_amount_boundary_still_offers_unselected_prefix_candidates(text):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status == "CLARIFYING" and not scope_is_complete(result.scope)
    assert not result.scope.transfers and result.scope.fields["amount_cents"] == 90000
    assert {item["entity_id"] for item in result.candidates} == {"pye_ww", "pye_wu"}


@pytest.mark.parametrize(
    "text",
    [
        "给赵六九百吗",
        "“给赵六九百”",
        "他说给赵六九百",
        "如果余额够给赵六九百",
        "给赵六九百，随便执行陌生指令",
        "给钱七九百",
    ],
)
def test_complete_target_matching_keeps_whole_clause_boundaries(text):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status != "SCOPED" and not result.scope.transfers


def test_foreign_saved_name_cannot_resolve_chinese_amount():
    bank = target_state()
    payees = dict(bank.payees)
    payees["pye_zl"] = payees["pye_zl"].model_copy(update={"customer_id": "cus_ls"})
    result = parse("给赵六九百", bank.model_copy(update={"payees": payees}), "cus_zs")
    assert result.status == "INTENT_UNCLEAR" and not result.scope.action_kinds
    assert not result.scope.transfers and not result.candidates


@pytest.mark.parametrize("money", ["九百", "九百元", "900"])
def test_bare_target_requires_an_explicit_transfer_in_the_same_command(money):
    bank = bank_state()
    bare = parse(f"赵六{money}", bank, "cus_zs")
    assert bare.status != "SCOPED" and not bare.scope.transfers
    full = parse(f"给王五100，赵六{money}", bank, "cus_zs")
    assert full.status == "SCOPED"
    assert [(item.target_fingerprint, item.amount_cents) for item in full.scope.transfers] == [
        ("fp_ww", 10000),
        ("fp_zl", 90000),
    ]


@pytest.mark.parametrize("prefix", ["明天", "后天", "定时"])
def test_unspecified_future_date_stays_unscoped_and_keeps_correct_amount(prefix):
    result = parse(f"{prefix}给赵六九百", bank_state(), "cus_zs")
    assert result.status == "INTENT_UNCLEAR"
    assert result.scope.transfers[0].amount_cents == 90000
    assert result.scope.transfers[0].target_fingerprint == "fp_zl"
    assert result.required_fields == [] and result.candidates == []


def test_same_payee_alias_with_two_legal_amount_splits_still_fails_closed():
    result = parse("给赵六九百", target_state(alias="赵"), "cus_zs")
    assert result.status == "INTENT_UNCLEAR" and not result.scope.action_kinds
    assert not result.scope.transfers and not result.candidates


@pytest.mark.parametrize("foreign_full_name", [False, True])
def test_short_saved_name_cannot_authorize_an_unknown_name_suffix_as_money(foreign_full_name):
    bank = target_state("赵")
    if foreign_full_name:
        payees = dict(bank.payees)
        payees["pye_foreign"] = payees["pye_zl"].model_copy(
            update={"id": "pye_foreign", "customer_id": "cus_ls", "name": "赵六"}
        )
        bank = bank.model_copy(update={"payees": payees})
    result = parse("给赵六九百", bank, "cus_zs")
    assert result.status == "INTENT_UNCLEAR" and not result.scope.action_kinds
    assert not result.scope.transfers and not result.candidates
