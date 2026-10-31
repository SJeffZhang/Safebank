"""AA self inclusion: pure field checks and coordinator-owned real G3/PG lineage gates."""

import asyncio
from copy import deepcopy
from datetime import timedelta

import pytest
from safebank_bankcore.ledger import replace_state
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft
from sqlalchemy import update

from services.gateway.app import tables as t
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.taint.authorization import AuthorizationEvidence
from tests.contracts.samples import step
from tests.gateway.w1.db.support import create, harness, login, utter
from tests.gateway.w1.fixtures import bank_state
from tests.gateway.w2.db.test_real_w1_integration import authorization_for, run_stages
from tests.gateway.w2.test_provenance import source

TEXT = "昨晚聚餐400元，我和小林、小周、小陈AA"
PARTICIPANTS = ["pye_ww", "pye_wu", "pye_zl"]
SCOPE = {
    "action_kinds": ["SPLIT_CREATE"],
    "fields": {
        "account_id": "acc_zs",
        "total_cents": 40000,
        "participant_payee_ids": PARTICIPANTS,
        "include_self": True,
    },
}


def aa_state():
    state = bank_state()
    return replace_state(
        state,
        payees={
            id_: state.payees[id_].model_copy(update={"name": name})
            for id_, name in zip(PARTICIPANTS, ("小林", "小周", "小陈"), strict=True)
        },
    )


def aa_step(ref, *, include_self=True, span=(9, 11), total_cents=40000):
    split = step(
        "create_split_request",
        account_id="acc_zs",
        total_cents=total_cents,
        participant_payee_ids=PARTICIPANTS,
        include_self=include_self,
        memo=None,
    )
    split["arg_sources"] = [
        source("total_cents", ref=ref, span=[4, 8]),
        source("participant_payee_ids", ref=ref, span=[11, 19]),
        source("include_self", ref=ref, span=list(span)),
    ]
    return split


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("valid", "user"),
        ("false", "unknown"),
        ("other_tool", "unknown"),
        ("channel", "unknown"),
        ("unrelated_span", "unknown"),
        ("broader_span", "unknown"),
        ("missing_scope", "unknown"),
        ("unscoped", "unknown"),
        ("wrong_action", "unknown"),
        ("mixed_actions", "unknown"),
        ("scope_false", "unknown"),
        ("scope_number", "unknown"),
        ("scope_string", "unknown"),
        ("context", "unknown"),
    ],
)
def test_aa_self_field_proof_is_narrow_without_database(monkeypatch, mutation, expected):
    """Exercises the real provenance stage; only its two IO loaders are replaced."""
    scope = deepcopy(SCOPE)
    row = {
        "kind": "typed",
        "text": TEXT,
        "parse_status": "SCOPED",
        "normalized_scope": scope,
    }
    split = aa_step("utt_aa", include_self=mutation != "false")
    checked_param = "include_self"
    if mutation in ("other_tool", "channel"):
        checked_param = "replace" if mutation == "other_tool" else "online"
        split = step(
            "report_card_lost" if mutation == "other_tool" else "update_card_controls",
            card_id="crd_zs",
            **{checked_param: True},
        )
        split["arg_sources"] = [source(checked_param, ref="utt_aa", span=[9, 11])]
    if mutation == "unrelated_span":
        split["arg_sources"][-1]["source"]["span"] = [11, 13]
    elif mutation == "broader_span":
        split["arg_sources"][-1]["source"]["span"] = [9, 19]
    elif mutation == "missing_scope":
        row.pop("normalized_scope")
    elif mutation == "unscoped":
        row["parse_status"] = "INTENT_UNCLEAR"
    elif mutation == "wrong_action":
        scope["action_kinds"] = ["CARD_LOST"]
    elif mutation == "mixed_actions":
        scope["action_kinds"] = ["SPLIT_CREATE", "CARD_LOST"]
    elif mutation in ("scope_false", "scope_number", "scope_string"):
        scope["fields"]["include_self"] = {
            "scope_false": False,
            "scope_number": 1,
            "scope_string": "true",
        }[mutation]
    elif mutation == "context":
        row["kind"] = "context"

    async def evidence(_ctx):
        return AuthorizationEvidence(utterances={"utt_aa": row})

    async def records(*_args):
        return {}

    monkeypatch.setattr("services.gateway.app.pipeline.provenance.authorization_evidence", evidence)
    monkeypatch.setattr("services.gateway.app.pipeline.provenance.load_records", records)
    ctx = EvalContext(
        plan_draft=PlanDraft.model_validate(
            {
                "request_id": "req_aa_pure",
                "session_id": "ses_aa",
                "utterance_ids": ["utt_aa"],
                "intents": ["split"],
                "rationale": "",
                "steps": [split],
            }
        ),
        customer_id="cus_zs",
        session_id="ses_aa",
        plan_id="pln_aa",
        origin="agent",
    )
    ctx._lazy_state = aa_state()
    asyncio.run(ProvenanceStage().run(ctx))
    assert ctx.provenance.arg_sources["s1"][checked_param]["trust"] == expected


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("valid", "user"),
        ("missing", "unknown"),
        ("false", "unknown"),
        ("unrelated_span", "unknown"),
        ("scope_false", "unknown"),
        ("scope_number", "unknown"),
        ("wrong_action", "unknown"),
        ("stale_scope_ref", "unknown"),
        ("expired", "unknown"),
        ("other_session", "unknown"),
    ],
)
def test_aa_self_source_through_real_g3_and_lineage(gateway_db, monkeypatch, mutation, expected):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            fake.set_snapshot(aa_state())
            token = await login(client)
            sid = await create(client, token, "req_aa_session_" + mutation)
            response = await utter(client, token, sid, TEXT, "req_aa_" + mutation)
            assert response.status_code == 201, response.text
            body = response.json()
            registered = body["utterance"]
            assert registered["parse_status"] == "SCOPED"
            assert registered["scope"]["action_kinds"] == ["SPLIT_CREATE"]
            assert registered["scope"]["fields"]["include_self"] is True
            caller, auth = await authorization_for(db, public, body["assistant_token"], sid)
            ref = registered["id"]
            if mutation in ("scope_false", "scope_number", "wrong_action", "stale_scope_ref"):
                saved_scope = deepcopy(registered["scope"])
                if mutation == "wrong_action":
                    saved_scope["action_kinds"] = ["CARD_LOST"]
                else:
                    saved_scope["fields"]["include_self"] = (
                        1 if mutation == "scope_number" else False
                    )
                async with db.begin() as conn:
                    await conn.execute(
                        update(t.utterances)
                        .where(t.utterances.c.id == ref)
                        .values(normalized_scope=saved_scope)
                    )
                if mutation != "stale_scope_ref":
                    caller, auth = await authorization_for(db, public, body["assistant_token"], sid)
            if mutation == "expired":
                clock[0] += timedelta(minutes=10)
            if mutation == "other_session":
                other_sid = await create(client, token, "req_aa_other_session")
                other = (await utter(client, token, other_sid, TEXT, "req_aa_other")).json()
                ref = other["utterance"]["id"]
            split = aa_step(
                ref,
                include_self=mutation != "false",
                span=(11, 13) if mutation == "unrelated_span" else (9, 11),
            )
            if mutation == "missing":
                split["arg_sources"].pop()
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [split])
            assert ctx.provenance.arg_sources["s1"]["include_self"]["trust"] == expected
            if mutation == "valid":
                assert ctx.intent.matched is True
                assert ctx.provenance.arg_sources["s1"]["total_cents"]["trust"] == "user"
                assert ctx.provenance.arg_sources["s1"]["participant_payee_ids"]["trust"] == "user"
                assert ctx.provenance.arg_sources["s1"]["memo"]["trust"] == "null"
            else:
                assert any(f.code is Code.UNKNOWN_SENSITIVE_ARG for f in ctx.findings)

    asyncio.run(run())


def test_aa_self_evidence_cannot_authorize_a_different_total(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            fake.set_snapshot(aa_state())
            token = await login(client)
            sid = await create(client, token, "req_aa_quantity_session")
            body = (await utter(client, token, sid, TEXT, "req_aa_quantity")).json()
            caller, auth = await authorization_for(db, public, body["assistant_token"], sid)
            split = aa_step(body["utterance"]["id"], total_cents=50000)
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [split])
            assert ctx.provenance.arg_sources["s1"]["total_cents"]["trust"] == "unknown"
            assert ctx.intent.matched is False
            assert any(f.code is Code.INTENT_SCOPE_EXCEEDED for f in ctx.findings)

    asyncio.run(run())
