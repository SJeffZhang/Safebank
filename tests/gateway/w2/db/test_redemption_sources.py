"""Credential-bound user quantity, never data-selected redemption units."""

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
        ("decimal", "user"),
        ("wrong_quantity", "unknown"),
        ("product_span", "unknown"),
        ("expired", "unknown"),
        ("other_session", "unknown"),
    ],
)
def test_redemption_units_match_typed_quantity(gateway_db, monkeypatch, mutation, expected):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            token = await login(client)
            sid = await create(client, token, "req_redeem_session_" + mutation)
            number = "1000.1250" if mutation == "decimal" else "1000"
            text = "赎回稳盈短债" + number + "份"
            response = await utter(client, token, sid, text, "req_redeem_" + mutation)
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["utterance"]["parse_status"] == "SCOPED", body
            caller, auth = await authorization_for(db, public, body["assistant_token"], sid)
            ref = body["utterance"]["id"]
            if mutation == "expired":
                clock[0] += timedelta(hours=1)
            if mutation == "other_session":
                other_sid = await create(client, token, "req_other_redeem_session")
                other = (await utter(client, token, other_sid, text, "req_other_redeem")).json()
                ref = other["utterance"]["id"]
            redeem = step(
                "redeem_product",
                account_id="acc_zs",
                product_id="prd_a",
                units="2000.0000" if mutation == "wrong_quantity" else number,
            )
            redeem["arg_sources"] = [
                source(
                    "units",
                    ref=ref,
                    span=[2, 6] if mutation == "product_span" else [6, 6 + len(number)],
                )
            ]
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [redeem])
            assert ctx.provenance.arg_sources["s1"]["units"]["trust"] == expected
            if mutation in ("valid", "decimal"):
                assert ctx.intent.matched
            if mutation == "wrong_quantity":
                assert not ctx.intent.matched

    asyncio.run(run())
