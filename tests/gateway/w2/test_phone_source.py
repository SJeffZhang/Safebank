"""The new phone scalar matcher still needs the existing typed-root proof."""

import asyncio

import pytest
from safebank_contracts.plan import PlanDraft

from services.gateway.app.api.plans import _load_authorization
from services.gateway.app.pipeline.context import EvalContext, ResolvedStep
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.schemas.enums import ServiceKind
from services.gateway.app.schemas.identity import Caller
from services.gateway.app.utterance.parser import parse
from tests.gateway.w1.test_phone_transfer import PHONE, phone_state
from tests.gateway.w3b.test_authorization_roots import WALL, MemoryConnection, _row


@pytest.mark.parametrize(
    "source_ref,span,expected",
    [
        ("utt_current", [1, 12], "user"),
        ("utt_old", [1, 12], "unknown"),
        ("utt_current", [1, 13], "unknown"),
        ("utt_current", [2, 12], "unknown"),
    ],
)
def test_phone_source_must_be_the_exact_span_of_current_bound_typed_root(
    source_ref, span, expected, monkeypatch
):
    async def run():
        # No read-backed parameter participates in this phone-span test.
        async def no_read_records(*_):
            return {}

        monkeypatch.setattr(
            "services.gateway.app.pipeline.provenance.load_records", no_read_records
        )
        state = phone_state()
        text = f"给{PHONE}转100元"
        scope = parse(text, state, "cus_zs").scope.model_dump(mode="json")
        conn = MemoryConnection(
            {
                "sessions": [
                    {
                        "id": "ses_current",
                        "customer_id": "cus_zs",
                        "authorization_epoch": 3,
                        "revoked_at_wall": None,
                    }
                ],
                "utterances": [
                    _row(id_, text=text, normalized_scope=scope)
                    for id_ in ("utt_old", "utt_current")
                ],
                "read_records": [],
            }
        )
        args = {
            "from_account_id": "acc_zs",
            "payee_id": "pye_ww",
            "to_account_id": None,
            "amount_cents": 10000,
            "memo": None,
        }
        step = {
            "step_id": "s1",
            "tool": "transfer",
            "args": args,
            "depends_on": [],
            "arg_sources": [
                {
                    "param": "payee_id",
                    "source": {"kind": "utterance", "ref": source_ref, "span": span},
                }
            ],
        }
        draft = PlanDraft.model_validate(
            {
                "request_id": "req_phone",
                "session_id": "ses_current",
                "utterance_ids": ["utt_current"],
                "intents": [],
                "rationale": "",
                "steps": [step],
            }
        )
        ctx = EvalContext(
            conn=conn,
            plan_draft=draft,
            plan_id="pln_phone",
            origin="agent",
            customer_id="cus_zs",
            session_id="ses_current",
            wall_now=lambda: WALL,
            identity=Caller(
                ServiceKind.A,
                customer_id="cus_zs",
                sid="ses_current",
                scopes=frozenset({"assistant"}),
                utts=("utt_current",),
            ),
        )
        ctx.authorization = await _load_authorization(
            conn, "ses_current", "cus_zs", WALL, source_ids=("utt_current",)
        )
        ctx._lazy_state = state
        ctx.steps = (ResolvedStep("s1", "transfer", args, "TRANSFER_EXTERNAL", "YELLOW", True),)
        await ProvenanceStage().run(ctx)
        assert ctx.provenance.arg_sources["s1"]["payee_id"]["trust"] == expected

    asyncio.run(run())
