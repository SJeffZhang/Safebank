"""Root-run PG gate for G4 -> G22a -> one-time G3 adoption.

Collected by the worker only. Real PG execution belongs to the coordinator.
L3 is the existing snapshot fake; this is not real-bank financial execution.
"""

import asyncio

import pytest
from sqlalchemy import func, select

from services.gateway.app import tables as t
from tests.gateway.w3b.db.conftest import (
    AGENT_TOKEN,
    SERVICE_TOKEN,
    WALL,
    _low_balance_bank_state,
    _seed_full,
    build_app,
)
from tests.gateway.w3b.db.test_g4_evaluate import (
    _body,
    _runtime_db,
    _seed_provenance_data,
    _step_with_sources,
)


async def prepared(app_config):
    app = await build_app(app_config, bank_state_fn=_low_balance_bank_state)
    db = _runtime_db(app)
    sid, jti = "ses_nov1_hint", "jti-nov1-hint"
    async with db.begin() as conn:
        await _seed_full(conn, sid=sid, jti_auth=jti)
        account, payee = await _seed_provenance_data(conn, sid, amount_cents=700000)
    body = _body(
        "req_hint_evaluate",
        sid,
        [f"utt_{sid}_1"],
        steps=[_step_with_sources(sid, account, payee, amount_cents=700000)],
    )
    agent_headers = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])
    response = await app.client.post("/plans/evaluate", json=body, headers=agent_headers)
    assert response.status_code == 200
    decision = response.json()
    assert decision["decision"] == "REPLAN" and len(decision["replan_hints"]) == 1
    frontend = {
        "X-Service-Token": SERVICE_TOKEN,
        "Authorization": "Bearer " + app.app_jwt(customer_id="cus_a", jti=jti),
    }
    return app, db, sid, decision, frontend, body, agent_headers


def test_g4_hint_is_readable_adoptable_once_and_evaluation_retry_is_idempotent(app_config):
    async def run():
        app, db, sid, decision, headers, body, agent = await prepared(app_config)
        try:
            hint = decision["replan_hints"][0]
            replay = await app.client.post("/plans/evaluate", json=body, headers=agent)
            assert replay.status_code == 200 and replay.json() == decision
            view = await app.client.get(f"/sessions/{sid}/hints/{hint['id']}", headers=headers)
            assert view.status_code == 200 and view.json() == hint
            assert "view_hash" not in view.json()
            async with db.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(t.hints)) == 1
                stored = (await conn.execute(select(t.hints))).mappings().one()
                assert (
                    len(stored["view_hash"]) == 64
                    and stored["parent_plan_id"] == decision["plan_id"]
                )
                assert stored["expires_at_wall"] > WALL and stored["used_by"] is None
            request = {"request_id": "req_adopt_hint", "kind": "hint", "hint_id": hint["id"]}
            adopted = await app.client.post(
                f"/sessions/{sid}/utterances", headers=headers, json=request
            )
            assert adopted.status_code == 201
            utterance = adopted.json()["utterance"]
            assert utterance["scope"] == hint["scope"] and utterance["kind"] == "hint"
            repeated = await app.client.post(
                f"/sessions/{sid}/utterances", headers=headers, json=request
            )
            assert (
                repeated.status_code == 201
                and repeated.json()["utterance"]["id"] == utterance["id"]
            )
            reused = await app.client.post(
                f"/sessions/{sid}/utterances",
                headers=headers,
                json={**request, "request_id": "req_adopt_hint_again"},
            )
            assert reused.status_code == 409
            assert (
                await app.client.get(f"/sessions/{sid}/hints/{hint['id']}", headers=headers)
            ).status_code == 409
            # The adopted scope must authorize a new proposal and reach the
            # ordinary confirmation panel; adoption itself never executes it.
            maximum = hint["scope"]["transfers"][0]["amount_cents"]
            step = _step_with_sources(sid, "unused", "unused", amount_cents=maximum)
            step["arg_sources"] = [
                {"param": param, "source": {"kind": "hint", "ref": hint["id"]}}
                for param in ("amount_cents", "from_account_id", "payee_id")
            ]
            next_decision = await app.client.post(
                "/plans/evaluate",
                json=_body("req_adopted_evaluate", sid, [utterance["id"]], steps=[step]),
                headers={
                    "X-Service-Token": AGENT_TOKEN,
                    "Authorization": "Bearer " + adopted.json()["assistant_token"],
                },
            )
            assert next_decision.status_code == 200
            approved_proposal = next_decision.json()
            assert approved_proposal["decision"] == "REQUIRE_APPROVAL"
            panel = await app.client.get(
                f"/approvals/{approved_proposal['approval_id']}", headers=headers
            )
            assert panel.status_code == 200
            assert panel.json()["plan_id"] == approved_proposal["plan_id"]
            fields = {field["key"]: field["value"] for field in panel.json()["steps"][0]["fields"]}
            assert fields["amount"] == f"{maximum // 100:,}.{maximum % 100:02d}"
            async with db.connect() as conn:
                stored = (await conn.execute(select(t.hints))).mappings().one()
                assert stored["used_by"] == utterance["id"]
                assert await conn.scalar(select(func.count()).select_from(t.execution_jobs)) == 0
        finally:
            await db.dispose()
            await app.client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["epoch", "root_status", "scope", "parent", "evaluation", "ttl"])
def test_hint_read_and_adoption_reject_stale_or_tampered_binding(app_config, fault):
    async def run():
        app, db, sid, decision, headers, _, _ = await prepared(app_config)
        try:
            hint = decision["replan_hints"][0]
            async with db.begin() as conn:
                if fault == "epoch":
                    await conn.execute(
                        t.sessions.update()
                        .where(t.sessions.c.id == sid)
                        .values(authorization_epoch=2)
                    )
                elif fault == "root_status":
                    await conn.execute(
                        t.utterances.update()
                        .where(t.utterances.c.sid == sid)
                        .values(status="SUPERSEDED")
                    )
                elif fault == "scope":
                    scope = {
                        **hint["scope"],
                        "transfers": [{**hint["scope"]["transfers"][0], "amount_cents": 9999999}],
                    }
                    await conn.execute(
                        t.hints.update().where(t.hints.c.id == hint["id"]).values(scope_json=scope)
                    )
                elif fault == "parent":
                    await conn.execute(
                        t.plans.update()
                        .where(t.plans.c.id == decision["plan_id"])
                        .values(status="SUPERSEDED")
                    )
                elif fault == "evaluation":
                    await conn.execute(
                        t.evaluations.update()
                        .where(t.evaluations.c.id == decision["evaluation_id"])
                        .values(policy_version="changed")
                    )
                else:
                    await conn.execute(
                        t.hints.update()
                        .where(t.hints.c.id == hint["id"])
                        .values(expires_at_wall=WALL)
                    )
                before = await conn.scalar(select(func.count()).select_from(t.utterances))
            assert (
                await app.client.get(f"/sessions/{sid}/hints/{hint['id']}", headers=headers)
            ).status_code == 409
            adopted = await app.client.post(
                f"/sessions/{sid}/utterances",
                headers=headers,
                json={"request_id": "req_bad_hint", "kind": "hint", "hint_id": hint["id"]},
            )
            assert adopted.status_code == 409
            async with db.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(t.utterances)) == before
                assert (await conn.execute(select(t.hints.c.used_by))).scalar_one() is None
                assert await conn.scalar(select(func.count()).select_from(t.execution_jobs)) == 0
        finally:
            await db.dispose()
            await app.client.aclose()

    asyncio.run(run())


def test_concurrent_adoption_of_one_published_hint_has_one_winner(app_config):
    async def run():
        app, db, sid, decision, headers, _, _ = await prepared(app_config)
        try:
            hint = decision["replan_hints"][0]
            replies = await asyncio.gather(
                *[
                    app.client.post(
                        f"/sessions/{sid}/utterances",
                        headers=headers,
                        json={
                            "request_id": f"req_concurrent_{i}",
                            "kind": "hint",
                            "hint_id": hint["id"],
                        },
                    )
                    for i in range(2)
                ]
            )
            assert sorted(reply.status_code for reply in replies) == [201, 409]
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(t.utterances)
                        .where(t.utterances.c.kind == "hint")
                    )
                    == 1
                )
                assert await conn.scalar(select(func.count()).select_from(t.execution_jobs)) == 0
        finally:
            await db.dispose()
            await app.client.aclose()

    asyncio.run(run())
