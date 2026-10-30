"""Calendar evidence must remain bound to the persisted typed authorization."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from safebank_contracts.obligations import ObligationSnapshot
from safebank_contracts.state import BankState

from tests.contracts.samples import step
from tests.gateway.w1.db.support import create, harness, login, utter
from tests.gateway.w2.db.test_real_w1_integration import authorization_for, run_stages
from tests.gateway.w2.test_provenance import source


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("valid", "user"),
        ("minimal_spans", "user"),
        ("utc_snapshot", "user"),
        ("weekly_sunday", "user"),
        ("weekly_tian", "user"),
        ("equivalent_offset", "user"),
        ("wrong_period", "unknown"),
        ("wrong_date", "unknown"),
        ("out_of_range_span", "unknown"),
        ("amount_span", "unknown"),
        ("other_utterance", "unknown"),
        ("expired", "unknown"),
    ],
)
def test_calendar_sources_are_narrow_and_credential_bound(
    gateway_db, monkeypatch, mutation, expected
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            state = BankState.model_validate(fake._snapshot)
            updates = {
                "obligations": ObligationSnapshot(
                    covered_customer_ids=("cus_zs",),
                    merchants={},
                    billings={},
                    standing_instructions={},
                    runs=(),
                    instruction_targets={},
                )
            }
            if mutation == "utc_snapshot":
                updates["sim_now"] = state.sim_now.astimezone(UTC)
            fake.set_snapshot(state.model_copy(update=updates))
            token = await login(client)
            sid = await create(client, token, "req_calendar_session_" + mutation)
            text = {
                "weekly_sunday": "每周日给赵六转200元",
                "weekly_tian": "每周天给赵六转200元",
            }.get(mutation, "每月20号给赵六转200元")
            response = await utter(client, token, sid, text, "req_calendar_typed_" + mutation)
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["utterance"]["parse_status"] == "SCOPED"
            scope = body["utterance"]["scope"]
            expected_first = (
                "2026-10-18T08:00:00+08:00"
                if mutation.startswith("weekly")
                else "2026-10-20T08:00:00+08:00"
            )
            assert datetime.fromisoformat(
                scope["fields"]["first_run_at"]
            ) == datetime.fromisoformat(expected_first)
            caller, auth = await authorization_for(db, public, body["assistant_token"], sid)
            ref = body["utterance"]["id"]
            span = [0, 5]
            if mutation == "other_utterance":
                other_sid = await create(client, token, "req_calendar_other_session")
                other = (await utter(client, token, other_sid, text, "req_calendar_other")).json()
                ref = other["utterance"]["id"]
            elif mutation == "out_of_range_span":
                span = [0, 999]
            elif mutation == "amount_span":
                span = [9, len(text)]
            elif mutation == "expired":
                clock[0] += timedelta(hours=1)
            period = "WEEKLY" if mutation == "wrong_period" else scope["period"]
            first = scope["fields"]["first_run_at"]
            if mutation == "equivalent_offset":
                first = datetime.fromisoformat(first).astimezone(UTC).isoformat()
            if mutation == "wrong_date":
                first = (datetime.fromisoformat(first) + timedelta(days=1)).isoformat()
            scheduled = step(
                "schedule_transfer",
                from_account_id="acc_zs",
                payee_id="pye_zl",
                to_account_id=None,
                amount_cents=20000,
                period=period,
                first_run_at=first,
                end_at=None,
            )
            scheduled["arg_sources"] = [
                source("period", ref=ref, span=[0, 2] if mutation == "minimal_spans" else span),
                source(
                    "first_run_at", ref=ref, span=[2, 5] if mutation == "minimal_spans" else span
                ),
            ]
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [scheduled])
            results = ctx.provenance.arg_sources["s1"]
            if expected == "user":
                assert ctx.intent.matched, (
                    "calendar provenance must also fit the same intent instant"
                )
            if mutation == "wrong_period":
                assert results["period"]["trust"] == expected
            elif mutation == "wrong_date":
                assert results["first_run_at"]["trust"] == expected
            else:
                assert results["period"]["trust"] == expected
                assert results["first_run_at"]["trust"] == expected

    asyncio.run(run())
