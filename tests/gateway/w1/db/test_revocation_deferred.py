"""Real PG: committed revocation, strict unresolved jobs and cached terminal 409."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from safebank_contracts.codes import Code
from sqlalchemy import event, func, select, update

from services.gateway.app import tables as t
from services.gateway.app.audit.chain import verify_chain
from services.gateway.app.auth.tokens import ASSISTANT_AUDIENCE, verify_jwt
from services.gateway.app.utterance.service import new_id
from tests.gateway.w1.db.bound_hint_fixture import bound_transfer_hint
from tests.gateway.w1.db.support import create, harness, headers, login, utter
from tests.gateway.w1.db.test_live_job_revocation import revoke, seed_job
from tests.gateway.w1.db.test_revocation_cas_races import remove_test_plan


def deferred(response):
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == Code.STATE_CHANGED.value
    assert error["details"] == {"authorization_revoked": True, "cleanup_pending": True}
    assert "assistant_token" not in response.json() and "decision" not in error["details"]
    return error


async def corrupt(conn, plan, root, mode):
    if mode in {"empty", "duplicate", "object"}:
        scopes = {"empty": [], "duplicate": [root, root], "object": {"wrong": root}}[mode]
        await conn.execute(update(t.plans).where(t.plans.c.id == plan).values(scope_ids=scopes))
    elif mode == "missing":
        await conn.execute(
            t.authorization_claims.delete().where(t.authorization_claims.c.plan_id == plan)
        )
    elif mode == "extra":
        await conn.execute(
            t.authorization_claims.insert().values(
                scope_id=new_id("utt"), plan_id=plan, status="CLAIMED"
            )
        )
    else:
        await conn.execute(
            update(t.authorization_claims)
            .where(t.authorization_claims.c.plan_id == plan)
            .values(status="CONSUMED")
        )


async def submission(conn, plan):
    values = []
    for table in (t.plans, t.execution_jobs, t.authorization_claims, t.evaluations):
        key = table.c.id if table is t.plans else table.c.plan_id
        values.append((await conn.execute(select(table).where(key == plan))).mappings().all())
    return values


async def count(conn, table, where):
    return await conn.scalar(select(func.count()).select_from(table).where(where))


@pytest.mark.parametrize("action", ["cancel", "correction", "replacement", "logout"])
@pytest.mark.parametrize("mode", ["missing", "empty", "duplicate", "object", "extra", "consumed"])
def test_stable_conflict_commits_only_revocation_and_one_atomic_error_audit(
    gateway_db, monkeypatch, action, mode
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(
                db, sid, old, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            try:
                async with db.begin() as conn:
                    await corrupt(conn, plan, old["utterance"]["id"], mode)
                    before = await submission(conn, plan)
                    jwt_count = await count(
                        conn, t.auth_sessions, t.auth_sessions.c.customer_id == "cus_zs"
                    )
                request_id = new_id("req")
                response = await revoke(client, token, sid, action, request_id)
                deferred(response)
                async with db.connect() as conn:
                    assert await submission(conn, plan) == before
                    assert await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == old["utterance"]["id"]
                        )
                    ) == ("CANCELLED" if action in {"cancel", "logout"} else "SUPERSEDED")
                    assert await count(conn, t.utterances, t.utterances.c.sid == sid) == 1
                    assert (
                        await count(
                            conn, t.pending_clarifications, t.pending_clarifications.c.sid == sid
                        )
                        == 0
                    )
                    assert (
                        await count(
                            conn, t.auth_sessions, t.auth_sessions.c.customer_id == "cus_zs"
                        )
                        == jwt_count
                    )
                    epoch = await conn.scalar(
                        select(t.sessions.c.authorization_epoch).where(t.sessions.c.id == sid)
                    )
                    assert epoch == (1 if action in {"cancel", "logout"} else 0)
                    event_type = "AUTH_LOGOUT" if action == "logout" else "AUTHORIZATION_REVOKED"
                    events = (
                        (
                            await conn.execute(
                                select(t.audit_events).where(
                                    t.audit_events.c.event_type == event_type,
                                    t.audit_events.c.payload["cleanup_pending"]
                                    .as_boolean()
                                    .is_(True),
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    own_events = [
                        row for row in events if plan in row["payload"]["pending_plan_ids"]
                    ]
                    assert len(own_events) == 1
                    assert (await verify_chain(conn, "cus_zs"))[0]
                if action == "logout":
                    assert (await revoke(client, token, sid, action, request_id)).status_code == 401
                    token = await login(client)
                    fresh = await create(client, token, new_id("req"))
                else:
                    visible = await client.get(
                        f"/sessions/{sid}/utterances",
                        headers=headers(old["assistant_token"], "a-test"),
                    )
                    assert visible.status_code == 200 and visible.json()["items"] == []
                # The cached body has the original trace; middleware rebuilds this request's.
                route = "/auth/logout" if action == "logout" else f"/sessions/{sid}/utterances"
                payload = {"request_id": request_id}
                if action != "logout":
                    payload.update(
                        kind="typed",
                        text={
                            "cancel": "停止本次",
                            "correction": "不对；改成给赵六九百",
                            "replacement": "给赵六900",
                        }[action],
                    )
                replay = await client.post(
                    route,
                    headers={**headers(token), "X-Request-ID": "replayed-trace"},
                    json=payload,
                )
                assert deferred(replay)["trace_id"] == "replayed-trace"
                if action != "logout":
                    changed = await utter(client, token, sid, "给赵六901", request_id)
                    assert (
                        changed.status_code == 409
                        and changed.json()["error"]["code"] == Code.IDEMPOTENCY_CONFLICT.value
                    )
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.sessions.c.authorization_epoch).where(t.sessions.c.id == sid)
                        )
                        == epoch
                    )
                    if action == "logout":
                        assert (
                            await conn.scalar(
                                select(t.sessions.c.revoked_at_wall).where(t.sessions.c.id == fresh)
                            )
                            is None
                        )
                    assert (
                        await count(
                            conn, t.audit_events, t.audit_events.c.id == own_events[0]["id"]
                        )
                        == 1
                    )
            finally:
                await remove_test_plan(db, plan)

    asyncio.run(run())


async def ui_sources(db, sid, scope, now, *, existing_hint=None):
    hint, action = existing_hint or new_id("hnt"), new_id("act")
    async with db.begin() as conn:
        if existing_hint is None:
            await conn.execute(
                t.hints.insert().values(
                    id=hint,
                    sid=sid,
                    scope_json=scope,
                    label="transfer",
                    created_at_wall=now,
                    expires_at_wall=now + timedelta(minutes=5),
                )
            )
        await conn.execute(
            t.form_actions.insert().values(
                id=action,
                sid=sid,
                kind="FORM",
                action_kind="CARD_FREEZE",
                normalized_fields={"card_id": "card_zs_1"},
                created_at_wall=now,
                expires_at_wall=now + timedelta(minutes=5),
            )
        )
    return hint, action


@pytest.mark.parametrize("source", ["typed", "hint", "action", "slots"])
def test_new_request_cannot_skip_unresolved_job_or_publish_replacement(
    gateway_db, monkeypatch, source
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            # Keep a valid clarification parent to exercise delayed RESOLVED.
            parent = (await utter(client, token, sid, "给王吾800")).json()
            plan, _, _ = await seed_job(
                db, sid, parent, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            try:
                async with db.begin() as conn:
                    await corrupt(conn, plan, parent["utterance"]["id"], "missing")
                    original_submission = await submission(conn, plan)
                # The old CLARIFYING root cannot issue a hint. A separate,
                # explicit test root supplies valid input to the job barrier.
                bound = await bound_transfer_hint(db, sid, clock[0]) if source == "hint" else None
                expected_roots = {parent["utterance"]["id"]}
                if bound is not None:
                    expected_roots.add(bound.root_id)
                hint, action = await ui_sources(
                    db,
                    sid,
                    parent["utterance"]["scope"],
                    clock[0],
                    existing_hint=bound.id if bound is not None else None,
                )
                payload = {
                    "request_id": new_id("req"),
                    "kind": source if source in {"hint", "action"} else "typed",
                }
                if source == "typed":
                    payload["text"] = "给赵六900"
                elif source == "slots":
                    payload.update(text="王五", clarification_id=parent["clarification_id"])
                else:
                    payload[source + "_id"] = hint if source == "hint" else action
                response = await client.post(
                    f"/sessions/{sid}/utterances", headers=headers(token), json=payload
                )
                deferred(response)
                async with db.connect() as conn:
                    if source == "hint":
                        rows = (
                            (
                                await conn.execute(
                                    select(t.utterances).where(t.utterances.c.sid == sid)
                                )
                            )
                            .mappings()
                            .all()
                        )
                        assert {row["id"] for row in rows} == expected_roots
                        assert all(row["status"] == "SUPERSEDED" for row in rows)
                        assert await submission(conn, plan) == original_submission
                    else:
                        assert await count(conn, t.utterances, t.utterances.c.sid == sid) == 1
                    assert (
                        await conn.scalar(
                            select(t.pending_clarifications.c.status).where(
                                t.pending_clarifications.c.id == parent["clarification_id"]
                            )
                        )
                        == "CANCELLED"
                    )
                    for table, id_ in ((t.hints, hint), (t.form_actions, action)):
                        row = (
                            (await conn.execute(select(table).where(table.c.id == id_)))
                            .mappings()
                            .one()
                        )
                        assert row["used_by"] is None and row["expires_at_wall"] == clock[0]
                # After the previous root is no longer replaceable, every kind
                # of newly issued source must still scan the unresolved job.
                if source in {"hint", "action"}:
                    fresh_bound = (
                        await bound_transfer_hint(db, sid, clock[0]) if source == "hint" else None
                    )
                    if fresh_bound is not None:
                        assert fresh_bound.root_id not in expected_roots
                        expected_roots.add(fresh_bound.root_id)
                    new_hint, new_action = await ui_sources(
                        db,
                        sid,
                        {"action_kinds": ["CARD_FREEZE"], "fields": {"card_id": "card_zs_1"}},
                        clock[0],
                        existing_hint=fresh_bound.id if fresh_bound is not None else None,
                    )
                    again = await client.post(
                        f"/sessions/{sid}/utterances",
                        headers=headers(token),
                        json={
                            "request_id": new_id("req"),
                            "kind": source,
                            source + "_id": new_hint if source == "hint" else new_action,
                        },
                    )
                else:
                    again = await utter(client, token, sid, "给赵六900", new_id("req"))
                deferred(again)
                if source == "hint":
                    async with db.connect() as conn:
                        rows = (
                            (
                                await conn.execute(
                                    select(t.utterances).where(t.utterances.c.sid == sid)
                                )
                            )
                            .mappings()
                            .all()
                        )
                        assert {row["id"] for row in rows} == expected_roots
                        assert all(row["status"] == "SUPERSEDED" for row in rows)
                        assert await submission(conn, plan) == original_submission
                        for table, id_ in ((t.hints, new_hint), (t.form_actions, new_action)):
                            row = (
                                (await conn.execute(select(table).where(table.c.id == id_)))
                                .mappings()
                                .one()
                            )
                            assert row["used_by"] is None and row["expires_at_wall"] == clock[0]
                renewed = await client.post(
                    f"/sessions/{sid}/assistant-token",
                    headers=headers(token),
                    json={"request_id": new_id("req")},
                )
                assert renewed.status_code == 200
                claims = verify_jwt(
                    renewed.json()["assistant_token"], public, expected_aud=ASSISTANT_AUDIENCE
                )
                assert claims.utts == ()
            finally:
                await remove_test_plan(db, plan)

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "logout"])
def test_bad_plan_does_not_stop_other_plans_sessions_approvals_or_ui(
    gateway_db, monkeypatch, action
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            roots = [
                (await utter(client, token, sid, text, new_id("req"))).json()
                for text in ("给王五800", "给赵六900", "给王五700")
            ]
            bad, _, _ = await seed_job(
                db, sid, roots[0], clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            good, _, _ = await seed_job(db, sid, roots[1], clock[0], "CONFIRMED", "PAUSED")
            idle, _, _ = await seed_job(db, sid, roots[2], clock[0], "CONFIRMED", "PAUSED")
            plans = [bad, good, idle]
            hint, form = await ui_sources(db, sid, roots[-1]["utterance"]["scope"], clock[0])
            second = await create(client, token, new_id("req"))
            other_root = (await utter(client, token, second, "给王五800", new_id("req"))).json()
            other, _, _ = await seed_job(db, second, other_root, clock[0], "EXECUTING", "FENCING")
            plans.append(other)
            try:
                async with db.begin() as conn:
                    await corrupt(conn, bad, roots[0]["utterance"]["id"], "missing")
                    await conn.execute(
                        t.execution_jobs.delete().where(t.execution_jobs.c.plan_id == idle)
                    )
                    await conn.execute(
                        t.authorization_claims.delete().where(
                            t.authorization_claims.c.plan_id == idle
                        )
                    )
                    before = await submission(conn, bad)
                    clr = new_id("clr")
                    await conn.execute(
                        t.pending_clarifications.insert().values(
                            id=clr,
                            sid=sid,
                            kind="FORM_REQUIRED",
                            authorization_epoch=0,
                            parent_utterance_ids=[roots[-1]["utterance"]["id"]],
                            status="OPEN",
                            expires_at_wall=clock[0] + timedelta(minutes=5),
                        )
                    )
                deferred(await revoke(client, token, sid, action, new_id("req")))
                async with db.connect() as conn:
                    assert await submission(conn, bad) == before
                    assert (
                        await conn.scalar(
                            select(t.execution_jobs.c.status).where(
                                t.execution_jobs.c.plan_id == good
                            )
                        )
                        == "UNKNOWN"
                    )
                    assert (
                        await conn.scalar(select(t.plans.c.status).where(t.plans.c.id == idle))
                        == "SUPERSEDED"
                    )
                    assert set(
                        (
                            await conn.execute(
                                select(t.approvals.c.status).where(t.approvals.c.sid == sid)
                            )
                        ).scalars()
                    ) == {"SUPERSEDED"}
                    assert (
                        await conn.scalar(
                            select(t.pending_clarifications.c.status).where(
                                t.pending_clarifications.c.id == clr
                            )
                        )
                        == "CANCELLED"
                    )
                    for table, id_ in ((t.hints, hint), (t.form_actions, form)):
                        row = (
                            (await conn.execute(select(table).where(table.c.id == id_)))
                            .mappings()
                            .one()
                        )
                        assert row["used_by"] is None and row["expires_at_wall"] == clock[0]
                    assert await conn.scalar(
                        select(t.execution_jobs.c.status).where(t.execution_jobs.c.plan_id == other)
                    ) == ("UNKNOWN" if action == "logout" else "FENCING")
                if action == "logout":
                    for selected in (sid, second):
                        assert (
                            await client.get(
                                f"/sessions/{selected}/utterances", headers=headers(token)
                            )
                        ).status_code == 401
                        async with db.connect() as conn:
                            row = (
                                (
                                    await conn.execute(
                                        select(t.sessions).where(t.sessions.c.id == selected)
                                    )
                                )
                                .mappings()
                                .one()
                            )
                            assert (
                                row["authorization_epoch"] == 1
                                and row["revoked_at_wall"] == clock[0]
                            )
                    assert (
                        await client.get(
                            f"/sessions/{second}/utterances",
                            headers=headers(other_root["assistant_token"], "a-test"),
                        )
                    ).status_code == 401
            finally:
                for plan in plans:
                    await remove_test_plan(db, plan)

    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["missing_source", "expired_source", "bad_slots"])
def test_prevalidation_failure_does_not_commit_revocation(gateway_db, monkeypatch, invalid):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (
                await utter(
                    client, token, sid, "给王吾800" if invalid == "bad_slots" else "给王五800"
                )
            ).json()
            plan, _, _ = await seed_job(
                db, sid, old, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            try:
                async with db.begin() as conn:
                    await corrupt(conn, plan, old["utterance"]["id"], "missing")
                hint, _ = await ui_sources(db, sid, old["utterance"]["scope"], clock[0])
                payload = {"request_id": new_id("req"), "kind": "hint", "hint_id": new_id("hnt")}
                if invalid == "expired_source":
                    async with db.begin() as conn:
                        await conn.execute(
                            update(t.hints)
                            .where(t.hints.c.id == hint)
                            .values(expires_at_wall=clock[0])
                        )
                    payload["hint_id"] = hint
                elif invalid == "bad_slots":
                    payload = {
                        "request_id": new_id("req"),
                        "kind": "typed",
                        "text": "not a candidate",
                        "clarification_id": old["clarification_id"],
                    }
                response = await client.post(
                    f"/sessions/{sid}/utterances", headers=headers(token), json=payload
                )
                assert response.status_code == (404 if invalid == "missing_source" else 409)
                assert response.json()["error"]["details"] != {
                    "authorization_revoked": True,
                    "cleanup_pending": True,
                }
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.utterances.c.status).where(
                                t.utterances.c.id == old["utterance"]["id"]
                            )
                        )
                        == "ACTIVE"
                    )
                    if invalid == "bad_slots":
                        assert (
                            await conn.scalar(
                                select(t.pending_clarifications.c.status).where(
                                    t.pending_clarifications.c.id == old["clarification_id"]
                                )
                            )
                            == "OPEN"
                        )
            finally:
                await remove_test_plan(db, plan)

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["revocation", "audit", "dedup", "commit"])
def test_actual_sql_failure_rolls_back_revocation_audit_and_dedup(gateway_db, monkeypatch, stage):
    async def run():
        async with harness(gateway_db, monkeypatch) as (app, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(
                db, sid, old, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            request_id = new_id("req")
            armed = [True]
            commit_ready = [False]

            def sql_failure(conn, cursor, statement, parameters, context, executemany):
                normalized = statement.lower()
                target = {
                    "revocation": "update sessions set authorization_epoch",
                    "audit": "insert into audit_events",
                    "dedup": "update request_dedup",
                    "commit": "update request_dedup",
                }[stage]
                if armed[0] and target in normalized:
                    armed[0] = False
                    if stage == "commit":
                        commit_ready[0] = True
                    else:
                        return "SELECT 1/0", ()  # PostgreSQL itself aborts this transaction.
                return statement, parameters

            def commit_failure(conn):
                if commit_ready[0]:
                    commit_ready[0] = False
                    conn.exec_driver_sql("SELECT 1/0")

            try:
                async with db.begin() as conn:
                    await corrupt(conn, plan, old["utterance"]["id"], "missing")
                event.listen(db.sync_engine, "before_cursor_execute", sql_failure, retval=True)
                event.listen(db.sync_engine, "commit", commit_failure)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app, raise_app_exceptions=False),
                    base_url="http://gateway",
                ) as observer:
                    response = await revoke(observer, token, sid, "cancel", request_id)
                assert response.status_code == 503, response.text
                assert not armed[0]
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.utterances.c.status).where(
                                t.utterances.c.id == old["utterance"]["id"]
                            )
                        )
                        == "ACTIVE"
                    )
                    assert (
                        await conn.scalar(
                            select(t.sessions.c.authorization_epoch).where(t.sessions.c.id == sid)
                        )
                        == 0
                    )
                    assert (
                        await count(
                            conn, t.request_dedup, t.request_dedup.c.request_id == request_id
                        )
                        == 0
                    )
                    events = (
                        (
                            await conn.execute(
                                select(t.audit_events.c.payload).where(
                                    t.audit_events.c.event_type == "AUTHORIZATION_REVOKED"
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert not any(plan in payload["pending_plan_ids"] for payload in events)
            finally:
                if event.contains(db.sync_engine, "before_cursor_execute", sql_failure):
                    event.remove(db.sync_engine, "before_cursor_execute", sql_failure)
                    event.remove(db.sync_engine, "commit", commit_failure)
                await remove_test_plan(db, plan)

    asyncio.run(run())


def test_committed_error_lost_response_replays_once_with_current_trace(gateway_db, monkeypatch):
    class LoseResponse(httpx.AsyncBaseTransport):
        def __init__(self, app):
            self.inner = httpx.ASGITransport(app)

        async def handle_async_request(self, request):
            await self.inner.handle_async_request(request)
            raise httpx.ReadError("response lost after server commit", request=request)

    async def run():
        async with harness(gateway_db, monkeypatch) as (app, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(
                db, sid, old, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            request_id = new_id("req")
            try:
                async with db.begin() as conn:
                    await corrupt(conn, plan, old["utterance"]["id"], "missing")
                async with httpx.AsyncClient(
                    transport=LoseResponse(app), base_url="http://gateway"
                ) as lost:
                    with pytest.raises(httpx.ReadError):
                        await revoke(lost, token, sid, "cancel", request_id)
                deferred(await revoke(client, token, sid, "cancel", request_id))
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.sessions.c.authorization_epoch).where(t.sessions.c.id == sid)
                        )
                        == 1
                    )
                    events = (
                        (
                            await conn.execute(
                                select(t.audit_events).where(
                                    t.audit_events.c.event_type == "AUTHORIZATION_REVOKED"
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    assert sum(plan in row["payload"]["pending_plan_ids"] for row in events) == 1
            finally:
                await remove_test_plan(db, plan)

    asyncio.run(run())


def test_cleanup_recovery_allows_new_id_but_keeps_original_cached_error(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(
                db, sid, old, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            request_id = new_id("req")
            try:
                async with db.begin() as conn:
                    await corrupt(conn, plan, old["utterance"]["id"], "missing")
                deferred(await revoke(client, token, sid, "replacement", request_id))
                # Test-only external repair models explicit operator action;
                # production W1 never inserts or consumes this missing claim.
                async with db.begin() as conn:
                    await conn.execute(
                        t.authorization_claims.insert().values(
                            scope_id=old["utterance"]["id"], plan_id=plan, status="CLAIMED"
                        )
                    )
                fresh = await revoke(client, token, sid, "replacement", new_id("req"))
                assert fresh.status_code == 201, fresh.text
                assert fresh.json()["utterance"]["scope"]["transfers"][0]["amount_cents"] == 90000
                deferred(await revoke(client, token, sid, "replacement", request_id))
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.execution_jobs.c.status).where(
                                t.execution_jobs.c.plan_id == plan
                            )
                        )
                        == "UNKNOWN"
                    )
                    assert (
                        await conn.scalar(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.plan_id == plan
                            )
                        )
                        == "CLAIMED"
                    )
                    assert (
                        await conn.scalar(
                            select(t.utterances.c.status).where(
                                t.utterances.c.id == fresh.json()["utterance"]["id"]
                            )
                        )
                        == "ACTIVE"
                    )
            finally:
                await remove_test_plan(db, plan)

    asyncio.run(run())


def test_concurrent_same_id_deferred_cancel_has_one_epoch_and_audit(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            plan, _, _ = await seed_job(
                db, sid, old, clock[0], "EXECUTING", "EXECUTING", evaluation=False
            )
            request_id = new_id("req")
            try:
                async with db.begin() as conn:
                    await corrupt(conn, plan, old["utterance"]["id"], "missing")
                responses = await asyncio.gather(
                    *(revoke(client, token, sid, "cancel", request_id) for _ in range(2))
                )
                for response in responses:
                    deferred(response)
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.sessions.c.authorization_epoch).where(t.sessions.c.id == sid)
                        )
                        == 1
                    )
                    events = (
                        (
                            await conn.execute(
                                select(t.audit_events.c.payload).where(
                                    t.audit_events.c.event_type == "AUTHORIZATION_REVOKED"
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert sum(plan in payload["pending_plan_ids"] for payload in events) == 1
            finally:
                await remove_test_plan(db, plan)

    asyncio.run(run())
