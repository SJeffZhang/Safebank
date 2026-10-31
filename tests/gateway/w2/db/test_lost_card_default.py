"""Only a live typed CARD_LOST scope proves the no-replacement default."""

import asyncio
from datetime import timedelta

import pytest

from tests.contracts.samples import step
from tests.gateway.w1.db.support import create, harness, login, utter
from tests.gateway.w2.db.test_real_w1_integration import authorization_for, run_stages
from tests.gateway.w2.test_provenance import source


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("valid", "user"),
        ("true", "unknown"),
        ("unrelated_span", "unknown"),
        ("expired", "unknown"),
        ("other_session", "unknown"),
    ],
)
def test_no_replacement_default_bound_to_typed_loss_scope(
    gateway_db, monkeypatch, mutation, expected
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            token = await login(client)
            sid = await create(client, token, "req_lost_session_" + mutation)
            text = "我的卡丢了，帮我挂失"
            body = (await utter(client, token, sid, text, "req_lost_" + mutation)).json()
            assert body["utterance"]["scope"]["fields"]["replace"] is False
            caller, auth = await authorization_for(db, public, body["assistant_token"], sid)
            ref = body["utterance"]["id"]
            if mutation == "expired":
                clock[0] += timedelta(hours=1)
            if mutation == "other_session":
                other_sid = await create(client, token, "req_lost_other_session")
                other = (await utter(client, token, other_sid, text, "req_lost_other")).json()
                ref = other["utterance"]["id"]
            loss = step("report_card_lost", card_id="crd_zs", replace=mutation == "true")
            loss["arg_sources"] = [
                source("replace", ref=ref, span=[0, 2] if mutation == "unrelated_span" else [8, 10])
            ]
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [loss])
            assert ctx.provenance.arg_sources["s1"]["replace"]["trust"] == expected

    asyncio.run(run())
