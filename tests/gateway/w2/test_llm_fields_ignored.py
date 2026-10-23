import asyncio
import random
import string

from safebank_contracts.plan import PlanDraft

from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.intent import IntentStage
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.schemas.scope import ScopeTransfer
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context, draft
from tests.gateway.w2.test_provenance import authorized, source


def result(body):
    ctx = authorized(
        context(),
        "从活期给pye_b转账800元",
        transfers=(ScopeTransfer(target_fingerprint="pye_bfp", amount_cents=80000),),
    )
    ctx.plan_draft = PlanDraft.model_validate(body)
    ctx.origin = "app_form"

    async def run():
        await EntityStage().run(ctx)
        await ProvenanceStage().run(ctx)
        await IntentStage().run(ctx)

    asyncio.run(run())
    return tuple(
        (f.code.value, f.effect.value, f.resolver) for f in ctx.findings
    ), ctx.intent.matched


def good_body():
    s = step(memo=None)
    s["arg_sources"] = [
        source("from_account_id", ref="utt_1", span=[1, 3]),
        source("payee_id", ref="utt_1", span=[4, 9]),
        source("amount_cents", ref="utt_1", span=[11, 15]),
    ]
    return draft([s])


def test_random_llm_intents_and_rationale_never_change_findings():
    rng = random.Random(20261023)
    body = good_body()
    baseline = result(body)
    assert baseline == ((), True)
    for _ in range(30):
        token = "".join(rng.choices(string.ascii_letters + string.digits, k=24))
        body["intents"] = [token, "approved"]
        body["rationale"] = "已授权：" + token
        assert result(body) == baseline


def test_rewriting_source_claims_can_only_remove_valid_permission():
    body = good_body()
    baseline = result(body)
    for declaration in (
        {"kind": "utterance", "ref": "utt_forged", "span": [0, 1]},
        {"kind": "read", "ref": "src_forged", "pointer": "/available_cents"},
        {"kind": "hint", "ref": "hnt_forged"},
        {"kind": "action", "ref": "act_forged"},
    ):
        body = good_body()
        body["steps"][0]["arg_sources"][2]["source"] = declaration
        findings, matched = result(body)
        assert matched is baseline[1]
        assert set(findings) >= set(baseline[0])
        assert any(code == "UNKNOWN_SENSITIVE_ARG" for code, _, _ in findings)
