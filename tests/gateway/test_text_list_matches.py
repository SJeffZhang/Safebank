"""List-valued entity params (AA participants) match a span naming each one."""

from types import SimpleNamespace

from services.gateway.app.taint.text import matches


def _payee(pid, name, customer="cus_zs"):
    return SimpleNamespace(id=pid, customer_id=customer, name=name, alias=None, display_name=None)


STATE = SimpleNamespace(
    accounts={},
    payees={
        "pye_lin": _payee("pye_lin", "小林"),
        "pye_zhou": _payee("pye_zhou", "小周"),
        "pye_chen": _payee("pye_chen", "小陈"),
        "pye_other": _payee("pye_other", "小王", customer="cus_ls"),
    },
    products=[],
)
P = "participant_payee_ids"


def test_span_naming_each_participant_matches_in_any_order():
    ids = ["pye_lin", "pye_zhou", "pye_chen"]
    assert matches(ids, "小林、小周、小陈", P, STATE, "cus_zs")
    assert matches(tuple(reversed(ids)), "小林、小周和小陈", P, STATE, "cus_zs")


def test_extra_missing_or_foreign_names_do_not_match():
    assert not matches(["pye_lin", "pye_zhou"], "小林、小周、小陈", P, STATE, "cus_zs")
    assert not matches(["pye_lin", "pye_zhou", "pye_chen"], "小林、小周", P, STATE, "cus_zs")
    assert not matches(["pye_lin", "pye_other"], "小林、小王", P, STATE, "cus_zs")
    assert not matches(["pye_lin", "pye_lin"], "小林、小林", P, STATE, "cus_zs")
    assert not matches([], "", P, STATE, "cus_zs")
