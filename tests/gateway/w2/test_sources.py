import asyncio
from datetime import UTC, datetime

import pytest
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft

from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.taint.records import pointer
from tests.bankcore.fixtures import NOW
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context, draft
from tests.gateway.w2.test_provenance import authorized, source


def ledger(data, *, untrusted=(), versions=None, tool="get_accounts"):
    return {
        "src_1": {
            "id": "src_1",
            "tool": tool,
            "response": data,
            "versions": versions if versions is not None else {"cus_a": 0},
            "untrusted_paths": list(untrusted),
            "observed_at": NOW,
            "created_at_wall": datetime.now(UTC),
        }
    }


def verify(monkeypatch, s, records, *, text="转账800元"):
    async def load(*_):
        return records

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", load)
    ctx = authorized(context([s]), text)
    ctx.origin = "app_form"

    async def run():
        await EntityStage().run(ctx)
        await ProvenanceStage().run(ctx)

    asyncio.run(run())
    return ctx


def test_read_versions_allow_additional_counterparty_but_require_owner(monkeypatch):
    s = step(payee_id="pye_b")
    s["arg_sources"] = [source("from_account_id", "read", ref="src_1", pointer="/accounts/0/id")]
    ctx = verify(monkeypatch, s, ledger({"accounts": [{"id": "acc_a"}]}))
    assert ctx.provenance.arg_sources["s1"]["from_account_id"]["trust"] == "trusted"
    for versions in ({"cus_a": 1}, {}, {"cus_b": 0}):
        ctx = verify(monkeypatch, s, ledger({"accounts": [{"id": "acc_a"}]}, versions=versions))
        assert ctx.provenance.arg_sources["s1"]["from_account_id"]["trust"] == "unknown"


def test_internal_snapshot_is_not_public_read_source(monkeypatch):
    s = step(payee_id="pye_b")
    s["arg_sources"] = [source("from_account_id", "read", ref="src_1", pointer="/id")]
    ctx = verify(monkeypatch, s, ledger({"id": "acc_a"}, tool="_utterance_snapshot"))
    assert ctx.provenance.arg_sources["s1"]["from_account_id"]["trust"] == "unknown"


def test_parent_pointer_cannot_hide_nested_untrusted_field(monkeypatch):
    s = step(
        "create_split_request",
        participant_payee_ids=["pye_b"],
        total_cents=100,
        include_self=True,
        memo=None,
    )
    s["arg_sources"] = [source("participant_payee_ids", "read", ref="src_1", pointer="/ids")]
    ctx = verify(monkeypatch, s, ledger({"ids": ["pye_b"]}, untrusted=("/ids/0",)))
    assert any(f.code is Code.TAINTED_SENSITIVE_ARG for f in ctx.findings)


@pytest.mark.parametrize("path", ["abc", "/a~2b", "/arr/01", "/arr/-", "/arr/-1"])
def test_malformed_json_pointer_is_rejected(path):
    with pytest.raises((ValueError, KeyError, IndexError)):
        pointer({"arr": [1]}, path)


def test_valid_pointer_escaped_keys_and_zero_index():
    assert pointer({"a/b": {"~": [7]}}, "/a~1b/~0/0") == 7


def test_read_source_cannot_be_from_future_or_different_simulated_clock(monkeypatch):
    s = step(payee_id="pye_b")
    s["arg_sources"] = [source("from_account_id", "read", ref="src_1", pointer="/id")]
    data = ledger({"id": "acc_a"})
    data["src_1"]["observed_at"] = "2026-10-14T09:00:00+08:00"
    ctx = verify(monkeypatch, s, data)
    assert ctx.provenance.arg_sources["s1"]["from_account_id"]["trust"] == "unknown"


def test_claim_rewrite_cannot_launder_untrusted_sensitive_value(monkeypatch):
    s = step(
        "add_payee",
        name="新人",
        account_no="6222020000000001",
        bank_name="bank",
        phone=None,
        alias=None,
    )
    records = ledger(
        {"memo": "新增账号6222020000000001"}, untrusted=("/memo",), tool="get_transactions"
    )
    for declarations in (
        [source("account_no", "read", ref="src_1", pointer="/memo")],
        [],
        [source("account_no", "read", ref="src_missing", pointer="/id")],
    ):
        s["arg_sources"] = declarations
        ctx = verify(monkeypatch, s, records)
        assert any(f.code is Code.TAINTED_SENSITIVE_ARG for f in ctx.findings)


def test_only_same_plan_prior_creator_can_supply_step_source(monkeypatch):
    s = step(payee_id="pye_b")
    s["arg_sources"] = [source("payee_id", "step", ref="future")]
    # Frozen contract rejects forward/undeclared dependency before provenance.
    with pytest.raises(ValueError):
        PlanDraft.model_validate(draft([s]))
