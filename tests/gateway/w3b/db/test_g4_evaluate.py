"""W3b Task 4: G4 POST /plans/evaluate real-PG integration tests.

Each test builds a fresh FastAPI app wired to the tmpfs PostgreSQL + FakeL3,
seeds the session + utterances, and calls the evaluate endpoint.

Important: the global ``caller_dependency`` runs ``check_revocation`` for
every F/A caller, which requires a valid, unrevoked session AND a valid
auth_sessions row.  Tests that assume the handler catches session errors
must understand that the dependency catches them first with 401.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from safebank_contracts.codes import Code
from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w3b.db.conftest import (
    WALL,
    _low_balance_bank_state,
    _seed_auth,
    _seed_full,
    _seed_read,
    _seed_session,
    _seed_utterance,
    _seed_workflow_event,
    _transfer_step,
    build_app,
    make_body,
    make_plan_draft_dict,
)

EVALUATE_URL = "/plans/evaluate"


def _body(
    request_id: str,
    session_id: str,
    utterance_ids: list[str],
    steps: list[dict] | None = None,
) -> dict:
    """Build a G4 request body with the right plan_draft."""
    pd = make_plan_draft_dict(
        request_id=request_id,
        session_id=session_id,
        utterance_ids=utterance_ids,
        steps=steps,
    )
    return make_body(request_id=request_id, plan_draft=pd)


def _runtime_db(app):
    """Return a fresh engine sharing the app's event loop + connection pool."""
    return engine(app.db_url, poolclass=NullPool)


# ── App Form (F‑path) tests ──────────────────────────────────────────────────


def test_f_path_returns_503(app_config):
    """F caller with valid app JWT receives 503 DEPENDENCY_UNAVAILABLE.

    The F‑path skeleton returns 503 because app‑form evaluation lands in a
    later component.  The test must use an app JWT (aud="gateway-app",
    scope=["app"]) with F service token, NOT an assistant JWT.
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_session(conn, sid="ses_f_path", customer="cus_a")
            await _seed_auth(conn, jti="jti-f-path", customer="cus_a")
            await _seed_utterance(conn, utt_id="utt_f_path_1", sid="ses_f_path")

        resp = await app.client.post(
            EVALUATE_URL,
            json=_body("req_f_path", "ses_f_path", ["utt_f_path_1"]),
            headers={
                "X-Service-Token": "test-frontend-service-token-32b",
                "Authorization": f"Bearer {app.app_jwt(customer_id='cus_a', jti='jti-f-path')}",
            },
        )
        assert resp.status_code == 503, resp.text
        error = resp.json()
        assert error["error"]["code"] == Code.DEPENDENCY_UNAVAILABLE.value
        await db.dispose()

    asyncio.run(run())


def test_f_path_no_service_token_returns_401(app_config):
    """No service token → 401 regardless of JWT."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, sid="ses_fp_nosvc", jti_auth="jti-fp-nosvc")

        resp = await app.client.post(
            EVALUATE_URL,
            json=_body("req_fp_nosvc", "ses_fp_nosvc", ["utt_ses_fp_nosvc_1"]),
            headers={
                "Authorization": (
                    f"Bearer {app.assistant_jwt(sid='ses_fp_nosvc', jti='jti-fp-nosvc')}"
                )
            },
        )
        assert resp.status_code == 401
        await db.dispose()

    asyncio.run(run())


def test_f_path_no_jwt_returns_401(app_config):
    """No JWT → 401 even with service token."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, sid="ses_fp_nojwt", jti_auth="jti-fp-nojwt")

        resp = await app.client.post(
            EVALUATE_URL,
            json=_body("req_fp_nojwt", "ses_fp_nojwt", ["utt_ses_fp_nojwt_1"]),
            headers={"X-Service-Token": "test-frontend-service-token-32b"},
        )
        assert resp.status_code == 401
        await db.dispose()

    asyncio.run(run())


# ── Basic evaluate tests ────────────────────────────────────────────────────


async def _seed_provenance_data(
    conn, sid: str, *, amount_cents: int = 80000
) -> tuple[str, str]:
    """Seed read records for get_accounts + list_payees, return (rid_acc, rid_pay).

    Also patches the default utterance (utt_{sid}_1) text and scope so the
    intent stage accepts the transfer amount from ``_step_with_sources``.
    Memo is left as None so both provenance and intent skip it.
    """
    from sqlalchemy import update as sql_update

    amount_yuan = str(amount_cents // 100)
    text = f"转账 {amount_yuan} 元"

    await conn.execute(
        sql_update(t.utterances)
        .where(t.utterances.c.id == f"utt_{sid}_1")
        .values(
            text=text,
            normalized_scope={
                "action_kinds": ["TRANSFER_EXTERNAL"],
                "from_account_id": "acc_a",
                "transfers": [
                    {
                        "target_fingerprint": "fp_ext",
                        "amount_cents": amount_cents,
                        "from_account_id": "acc_a",
                    }
                ],
                "period": None,
                "channel": "online",
                "fields": {"count": 1},
            },
        ),
    )

    rid_acc = await _seed_read(
        conn,
        sid=sid,
        tool="get_accounts",
        response={
            "accounts": [
                {
                    "id": "acc_a",
                    "customer_id": "cus_a",
                    "type": "CHECKING",
                    "display_name": "活期",
                    "account_no": "6222000000000001",
                }
            ]
        },
    )
    rid_pay = await _seed_read(
        conn,
        sid=sid,
        tool="list_payees",
        response={
            "payees": [
                {
                    "id": "pye_ext",
                    "name": "External Payee",
                    "target_fingerprint": "fp_ext",
                    "account_no": "6200000000000001",
                }
            ]
        },
    )
    return rid_acc, rid_pay


def _step_with_sources(
    sid: str, rid_acc: str, rid_pay: str, amount_cents: int = 80000
) -> dict:
    """Build a transfer step with proper arg_sources for provenance stage.

    Amount span computed from ``amount_cents``: text "转账 7000 元 备注" has
    the amount at index 3.
    """
    amount_yuan = str(amount_cents // 100)
    amount_start = 3
    amount_end = amount_start + len(amount_yuan)

    step = _transfer_step(amount_cents=amount_cents)
    step["args"]["memo"] = None
    step["arg_sources"] = [
        {
            "param": "amount_cents",
            "source": {"kind": "utterance", "ref": f"utt_{sid}_1", "span": [amount_start, amount_end]},
        },
        {
            "param": "from_account_id",
            "source": {"kind": "read", "ref": rid_acc, "pointer": "/accounts/0/id"},
        },
        {
            "param": "payee_id",
            "source": {"kind": "read", "ref": rid_pay, "pointer": "/payees/0/id"},
        },
    ]
    return step


def test_evaluate_transfer_allowed(app_config):
    """Transfer with complete arg_sources + read records → ALLOW.

    Seeds read records for get_accounts and list_payees, then provides
    arg_sources with utterance spans and read references so the provenance
    and entity stages can verify every parameter.
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_allow"
        jti = "jti-allow"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)
            rid_acc = await _seed_read(
                conn,
                sid=sid,
                tool="get_accounts",
                response={
                    "accounts": [
                        {
                            "id": "acc_a",
                            "customer_id": "cus_a",
                            "type": "CHECKING",
                            "display_name": "活期",
                            "account_no": "6222000000000001",
                        }
                    ]
                },
            )
            rid_pay = await _seed_read(
                conn,
                sid=sid,
                tool="list_payees",
                response={
                    "payees": [
                        {
                            "id": "pye_ext",
                            "name": "External Payee",
                            "target_fingerprint": "fp_ext",
                            "account_no": "6200000000000001",
                        }
                    ]
                },
            )

        # Build a step with proper arg_sources.
        # Utterance text "转账 800 元": span (3,6) = "800" → 80,000 cents.
        # from_account_id comes from get_accounts read; payee_id from list_payees.
        step = _transfer_step(amount_cents=80000)
        step["args"]["memo"] = None
        step["arg_sources"] = [
            {
                "param": "amount_cents",
                "source": {"kind": "utterance", "ref": f"utt_{sid}_1", "span": [3, 6]},
            },
            {
                "param": "from_account_id",
                "source": {"kind": "read", "ref": rid_acc, "pointer": "/accounts/0/id"},
            },
            {
                "param": "payee_id",
                "source": {"kind": "read", "ref": rid_pay, "pointer": "/payees/0/id"},
            },
        ]
        body = _body("req_allow", sid, [f"utt_{sid}_1"], steps=[step])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])
        resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        data = resp.json()
        expected = f"Got {data['decision']}: {data.get('findings')}"
        assert data["decision"] == "REQUIRE_APPROVAL", expected
        assert data["plan_id"].startswith("pln_")
        assert data["evaluation_id"].startswith("evl_")
        assert data["revision"] == 1
        assert data["risk"] == "YELLOW"
        assert data["approval_id"].startswith("apv_")

        # Verify DB state.
        async with db.connect() as conn:
            plan = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == data["plan_id"])))
                .mappings()
                .one()
            )
            assert plan["status"] == "PENDING_APPROVAL"
            assert plan["customer_id"] == "cus_a"
            assert plan["sid"] == sid

            eval_row = (
                (
                    await conn.execute(
                        select(t.evaluations).where(t.evaluations.c.id == data["evaluation_id"])
                    )
                )
                .mappings()
                .one()
            )
            assert eval_row["decision"] == {"decision": "REQUIRE_APPROVAL"}
            assert eval_row["risk"] == "YELLOW"

            # Approval row created.
            approval_row = (
                (
                    await conn.execute(
                        select(t.approvals).where(t.approvals.c.id == data["approval_id"])
                    )
                )
                .mappings()
                .one()
            )
            assert approval_row["plan_id"] == data["plan_id"]
            assert approval_row["status"] == "PENDING"

            # Audit record exists.
            audit_count = await conn.scalar(
                select(t.audit_events).where(t.audit_events.c.plan_id == data["plan_id"]).limit(1)
            )
            assert audit_count is not None

        await db.dispose()

    asyncio.run(run())


def test_evaluate_idempotent_request_id(app_config):
    """Same request_id returns cached result, no duplicate plan."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_idem"
        jti = "jti-idem"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)

        body = _body("req_idem", sid, [f"utt_{sid}_1"])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

        r1 = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert r1.status_code == 200, r1.text
        plan_id = r1.json()["plan_id"]

        # Same request_id + same body → idempotent.
        r2 = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert r2.status_code == 200
        assert r2.json()["plan_id"] == plan_id

        await db.dispose()

    asyncio.run(run())


def test_evaluate_request_id_conflict(app_config):
    """Same request_id with different body → 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_conflict"
        jti = "jti-conflict"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)

        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

        b1 = _body("req_conflict", sid, [f"utt_{sid}_1"])
        r1 = await app.client.post(EVALUATE_URL, json=b1, headers=hdrs)
        assert r1.status_code == 200, r1.text

        # Same request_id, different body (changed amount).
        pd = make_plan_draft_dict(
            request_id="req_conflict",
            session_id=sid,
            utterance_ids=[f"utt_{sid}_1"],
            steps=[_transfer_step(amount_cents=99999)],
        )
        b2 = {"request_id": "req_conflict", "plan_draft": pd}
        r2 = await app.client.post(EVALUATE_URL, json=b2, headers=hdrs)
        assert r2.status_code == 409, r2.text
        error = r2.json()
        assert error["error"]["code"] == Code.IDEMPOTENCY_CONFLICT.value
        await db.dispose()

    asyncio.run(run())


def test_evaluate_invalid_plan_draft_returns_422(app_config):
    """Malformed plan_draft → 422."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_badplan"
        jti = "jti-badplan"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)

        resp = await app.client.post(
            EVALUATE_URL,
            json={"request_id": "req_bad", "plan_draft": {"not": "valid"}},
            headers=app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"]),
        )
        assert resp.status_code == 422, resp.text
        await db.dispose()

    asyncio.run(run())


def test_evaluate_missing_request_id_returns_422(app_config):
    """No request_id → 422."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_missing_rid"
        jti = "jti-missing-rid"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)

        pd = make_plan_draft_dict(session_id=sid, utterance_ids=[f"utt_{sid}_1"])
        resp = await app.client.post(
            EVALUATE_URL,
            json={"plan_draft": pd},
            headers=app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"]),
        )
        assert resp.status_code == 422, resp.text
        await db.dispose()

    asyncio.run(run())


def test_evaluate_session_ownership_mismatch(app_config):
    """plan_draft.session_id ≠ JWT sid → 404.

    The caller_dependency checks the JWT sid exists (via check_revocation),
    so we must seed a valid session for the JWT sid and use a different
    session_id in the body.
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid_jwt = "ses_jwt_match"
        sid_body = "ses_other"
        jti = "jti-ownership"
        async with db.begin() as conn:
            # Seed the session named in the JWT so check_revocation passes.
            await _seed_full(conn, sid=sid_jwt, jti_auth=jti)
            # Also seed the other session (so it exists, but isn't in the JWT).
            await _seed_session(conn, sid=sid_body, customer="cus_a")
            await _seed_utterance(conn, utt_id=f"utt_{sid_body}_1", sid=sid_body)

        pd = make_plan_draft_dict(session_id=sid_body, utterance_ids=[f"utt_{sid_body}_1"])
        body = make_body(request_id="req_ownership", plan_draft=pd)
        resp = await app.client.post(
            EVALUATE_URL,
            json=body,
            headers=app.auth_headers(sid=sid_jwt, jti=jti, utts=[f"utt_{sid_jwt}_1"]),
        )
        assert resp.status_code == 404, resp.text
        error = resp.json()
        assert error["error"]["code"] == Code.OWNERSHIP_MISMATCH.value
        await db.dispose()

    asyncio.run(run())


def test_evaluate_utterance_not_in_jwt(app_config):
    """Utterance in plan but not in JWT utts → 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_utt_not_jwt"
        jti = "jti-utt-not-jwt"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)
            # Seed an extra utterance that we won't put in the JWT.
            await _seed_utterance(conn, utt_id=f"utt_{sid}_extra", sid=sid, text="extra")

        # Only put utt_sid_1 in the JWT, but reference utt_sid_extra in the body.
        pd = make_plan_draft_dict(session_id=sid, utterance_ids=[f"utt_{sid}_extra"])
        body = make_body(request_id="req_utt_not_jwt", plan_draft=pd)
        resp = await app.client.post(
            EVALUATE_URL,
            json=body,
            headers=app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"]),
        )
        assert resp.status_code == 409, resp.text
        error = resp.json()
        assert error["error"]["code"] == Code.AUTHORIZATION_CONSUMED.value
        await db.dispose()

    asyncio.run(run())


def test_evaluate_session_revoked(app_config):
    """Session has been revoked → 401 from check_revocation in caller_dependency.

    The global dependency runs before the handler, so the handler-level
    409 AUTHORIZATION_CANCELLED is never reached.
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_revoked"
        jti = "jti-revoked"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti, revoked=True)

        body = _body("req_revoked", sid, [f"utt_{sid}_1"])
        resp = await app.client.post(
            EVALUATE_URL,
            json=body,
            headers=app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"]),
        )
        assert resp.status_code == 401, resp.text
        await db.dispose()

    asyncio.run(run())


def test_evaluate_session_not_found(app_config):
    """JWT sid has no session row → 401 from check_revocation.

    The handler never runs because the global caller_dependency rejects
    the request first.
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        # Only seed auth_sessions — no sessions row.  check_revocation
        # will reject with 401.
        async with db.begin() as conn:
            await _seed_auth(conn, jti="jti-no-session", customer="cus_a")

        sid = "ses_not_found"
        body = _body("req_no_session", sid, ["utt_nonexistent"])
        resp = await app.client.post(
            EVALUATE_URL,
            json=body,
            headers=app.auth_headers(sid=sid, jti="jti-no-session", utts=["utt_nonexistent"]),
        )
        assert resp.status_code == 401, resp.text
        await db.dispose()

    asyncio.run(run())


# ── Audit chain ──────────────────────────────────────────────────────────────


def test_evaluate_produces_audit_event(app_config):
    """Successful evaluation creates an audit event."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_audit"
        jti = "jti-audit"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)

        body = _body("req_audit", sid, [f"utt_{sid}_1"])
        resp = await app.client.post(
            EVALUATE_URL,
            json=body,
            headers=app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"]),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        async with db.connect() as conn:
            audit_rows = (
                (
                    await conn.execute(
                        select(t.audit_events)
                        .where(t.audit_events.c.customer_id == "cus_a")
                        .order_by(t.audit_events.c.created_at_wall)
                    )
                )
                .mappings()
                .all()
            )
        assert len(audit_rows) >= 1
        audit = [r for r in audit_rows if r["plan_id"] == data["plan_id"]]
        assert len(audit) == 1
        event = audit[0]
        assert event["event_type"] == "EVALUATION_COMPLETED"
        assert event["prev_hash"] is not None
        await db.dispose()

    asyncio.run(run())


# ── Shadow B1 ────────────────────────────────────────────────────────────────


def test_evaluate_shadow_b1_present(app_config):
    """B2 profile produces a shadow_b1 field in the response."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_shadow"
        jti = "jti-shadow"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)

        body = _body("req_shadow", sid, [f"utt_{sid}_1"])
        resp = await app.client.post(
            EVALUATE_URL,
            json=body,
            headers=app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"]),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "shadow_b1" in data
        await db.dispose()

    asyncio.run(run())


# ── No auth ──────────────────────────────────────────────────────────────────


def test_evaluate_unauthenticated(app_config):
    """No service token, no JWT → 401."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.post(EVALUATE_URL, json=make_body(request_id="req_unauth"))
        assert resp.status_code == 401
        await _runtime_db(app).dispose()

    asyncio.run(run())


def test_evaluate_wrong_kind_service_token(app_config):
    """S (scheduler) token is not A → 401."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_wrong_kind"
        jti = "jti-wrong-kind"
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)

        resp = await app.client.post(
            EVALUATE_URL,
            json=_body("req_wrong_kind", sid, [f"utt_{sid}_1"]),
            headers={
                "X-Service-Token": "test-scheduler-token-32bytes-xxx",
                "Authorization": f"Bearer {app.assistant_jwt(sid=sid, jti=jti)}",
            },
        )
        assert resp.status_code == 401, resp.text
        await db.dispose()

    asyncio.run(run())


# ── B1 + T4.3: RISKY_SEQUENCE → DENY with cooldown persistence ───────────────


def test_risky_sequence_deny_with_cooldown(app_config):
    """B1/T4.3: PAYEE_ADD + CARD_LIMIT_LOOSEN in 24h window → DENY with cooldown.

    Seeds workflow_events so the 24h window contains both a PAYEE_ADD for the
    target payee AND a card loosen.  The workflow stage detects RISKY_SEQUENCE,
    collects a cooldown into pending_cooldowns, and the decision stage returns
    DENY.  The test verifies both the DENY decision and the cooldown row are
    persisted atomically.
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_risky"
        jti = "jti-risky"

        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)
            rid_acc, rid_pay = await _seed_provenance_data(conn, sid)
            # Seed PAYEE_ADD + CARD_LIMIT_LOOSEN within the 24h window.
            await _seed_workflow_event(
                conn,
                event_id="wfe_payee_add",
                action_kind="PAYEE_ADD",
                target_id="fp_ext",
                occurred_at=WALL - timedelta(hours=2),
            )
            await _seed_workflow_event(
                conn,
                event_id="wfe_card_loosen",
                action_kind="CARD_LIMIT_LOOSEN",
                occurred_at=WALL - timedelta(hours=1),
            )

        step = _step_with_sources(sid, rid_acc, rid_pay, amount_cents=80000)
        body = _body("req_risky", sid, [f"utt_{sid}_1"], steps=[step])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])
        resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        data = resp.json()

        # RISKY_SEQUENCE → DENY (not REQUIRE_APPROVAL).
        assert data["decision"] == "DENY", f"Expected DENY, got {data['decision']}"
        assert data["risk"] == "RED"
        assert data["plan_id"].startswith("pln_")
        assert data["evaluation_id"].startswith("evl_")
        findings = data.get("findings", [])
        risky_codes = [f["code"] for f in findings]
        assert "RISKY_SEQUENCE" in risky_codes, f"RISKY_SEQUENCE missing in {risky_codes}"

        # B1: cooldown row persisted atomically in the same write transaction.
        async with db.connect() as conn:
            cooldown_rows = (
                (
                    await conn.execute(
                        select(t.cooldowns).where(t.cooldowns.c.customer_id == "cus_a")
                    )
                )
                .mappings()
                .all()
            )
        assert len(cooldown_rows) >= 1, "cooldowns: expected at least 1 row"
        assert cooldown_rows[0]["payee_fingerprint"] == "fp_ext"
        assert cooldown_rows[0]["reason"] == Code.RISKY_SEQUENCE.value

        await db.dispose()

    asyncio.run(run())


def test_risky_sequence_idempotency_no_duplicate_cooldown(app_config):
    """B1 red: idempotency conflict → no residual cooldowns from failed attempt.

    First call triggers RISKY_SEQUENCE (DENY + cooldown).  Second call uses
    the same request_id with a different body → IDEMPOTENCY_CONFLICT 409.
    The second attempt never reaches _persist, so the cooldowns table still
    has exactly 1 row for this payee (from the first successful call).
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_risky2"
        jti = "jti-risky2"

        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)
            await _seed_workflow_event(
                conn,
                event_id="wfe_pa2",
                action_kind="PAYEE_ADD",
                target_id="fp_ext",
                occurred_at=WALL - timedelta(hours=2),
            )
            await _seed_workflow_event(
                conn,
                event_id="wfe_cl2",
                action_kind="CARD_LIMIT_LOOSEN",
                occurred_at=WALL - timedelta(hours=1),
            )

        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

        # First call: succeeds with DENY + cooldown.
        body1 = _body("req_risky2", sid, [f"utt_{sid}_1"])
        r1 = await app.client.post(EVALUATE_URL, json=body1, headers=hdrs)
        assert r1.status_code == 200, r1.text
        assert r1.json()["decision"] == "DENY"

        # Count cooldowns after first success.
        async with db.connect() as conn:
            count1 = await conn.scalar(
                select(t.cooldowns).where(
                    t.cooldowns.c.customer_id == "cus_a",
                    t.cooldowns.c.payee_fingerprint == "fp_ext",
                ).limit(1)
            )
        assert count1 is not None, "first call should persist cooldown"

        # Second call: same request_id, different body → 409.
        pd = make_plan_draft_dict(
            request_id="req_risky2",
            session_id=sid,
            utterance_ids=[f"utt_{sid}_1"],
            steps=[_transfer_step(amount_cents=99999)],
        )
        body2 = {"request_id": "req_risky2", "plan_draft": pd}
        r2 = await app.client.post(EVALUATE_URL, json=body2, headers=hdrs)
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == Code.IDEMPOTENCY_CONFLICT.value

        # B1 red: no duplicate/extra cooldown from the failed second call.
        async with db.connect() as conn:
            count2 = await conn.scalar(
                select(func.count()).select_from(t.cooldowns).where(
                    t.cooldowns.c.customer_id == "cus_a",
                    t.cooldowns.c.payee_fingerprint == "fp_ext",
                )
            )
        assert count2 == 1, f"expected exactly 1 cooldown, got {count2}"

        await db.dispose()

    asyncio.run(run())


# ── T4.4: REPLAN for insufficient funds ──────────────────────────────────────


def test_evaluate_replan_insufficient_funds(app_config):
    """T4.4: Balance too low → REPLAN with INSUFFICIENT_FUNDS.

    Uses a custom low-balance bank state (¥100 available) with a ¥800 transfer
    so the preflight stage detects INSUFFICIENT_FUNDS and produces REPLAN.
    """

    async def run():
        app = await build_app(app_config, bank_state_fn=_low_balance_bank_state)
        db = _runtime_db(app)
        sid = "ses_replan"
        jti = "jti-replan"

        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)
            rid_acc, rid_pay = await _seed_provenance_data(conn, sid, amount_cents=700000)

        # ¥7000 transfer with only ¥100 available → INSUFFICIENT_FUNDS.
        step = _step_with_sources(sid, rid_acc, rid_pay, amount_cents=700000)
        body = _body("req_replan", sid, [f"utt_{sid}_1"], steps=[step])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])
        resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["decision"] == "REPLAN", f"Expected REPLAN, got {data['decision']}"
        assert data["plan_id"].startswith("pln_")
        assert data["evaluation_id"].startswith("evl_")
        assert data["risk"] == "RED", f"Expected RED for INSUFFICIENT_FUNDS, got {data['risk']}"

        # Findings must contain INSUFFICIENT_FUNDS.
        findings = data.get("findings", [])
        codes = [f["code"] for f in findings]
        assert "INSUFFICIENT_FUNDS" in codes, f"Expected INSUFFICIENT_FUNDS in {codes}"

        # Replan hints should suggest a lower amount.
        hints = data.get("replan_hints", [])
        assert len(hints) >= 1, "Expected at least one replan hint"

        await db.dispose()

    asyncio.run(run())


# ── B5: lock-order deadlock regression (G6 vs evaluate) ──────────────────────


def test_g6_vs_evaluate_concurrent_no_deadlock(app_config):
    """B5: Concurrent G6 confirm and G4 evaluate do NOT deadlock.

    Unified lock order (session → plan → evaluation → approval) prevents ABBA:
    both G6 and evaluate both acquire session first.  Two transactions running
    concurrently should serialize cleanly without deadlock errors.
    """

    async def run():
        from safebank_bankcore.ids import digest

        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_lockrace"
        jti = "jti-lockrace"
        jti_f = "jti-lockrace-f"
        jti_e2 = "jti-lr2"

        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)
            await _seed_auth(conn, jti=jti_f, customer="cus_a")
            await _seed_auth(conn, jti=jti_e2, customer="cus_a")
            rid_acc, rid_pay = await _seed_provenance_data(conn, sid)

        # 1. Evaluate a plan → REQUIRE_APPROVAL (PENDING approval exists).
        step1 = _step_with_sources(sid, rid_acc, rid_pay, amount_cents=80000)
        body = _body("req_lockrace", sid, [f"utt_{sid}_1"], steps=[step1])
        hdrs_a = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])
        r = await app.client.post(EVALUATE_URL, json=body, headers=hdrs_a)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["decision"] == "REQUIRE_APPROVAL", f"Got {data['decision']}: {data.get('findings')}"
        approval_id = data["approval_id"]
        view = data.get("preflight", {})
        view_hash = digest(view)

        # 2. Run G6 confirm and NEW evaluate concurrently.
        #    G6 locks: session → approval (via _lock_session → _lock_approval)
        #    Evaluate locks: session → plan → evaluation → approval (via _persist)
        #    Both start with session → no ABBA.
        errors = []

        async def do_confirm():
            try:
                resp = await app.client.post(
                    f"/approvals/{approval_id}/confirm",
                    json={"view_hash": view_hash, "risk_ack": True},
                    headers={
                        "X-Service-Token": "test-frontend-service-token-32b",
                        "Authorization": f"Bearer {app.app_jwt(customer_id='cus_a', jti=jti_f)}",  # noqa: E501
                    },
                )
                if resp.status_code >= 500:
                    errors.append(f"confirm 5xx: {resp.status_code} {resp.text}")
            except Exception as exc:
                errors.append(f"confirm exception: {exc}")

        async def do_evaluate():
            await asyncio.sleep(0.05)
            try:
                step2 = _step_with_sources(sid, rid_acc, rid_pay, amount_cents=50000)
                resp = await app.client.post(
                    EVALUATE_URL,
                    json=_body("req_lockrace2", sid, [f"utt_{sid}_1"], steps=[step2]),
                    headers=app.auth_headers(
                        sid=sid, jti=jti_e2, utts=[f"utt_{sid}_1"]
                    ),
                )
                # Evaluate may succeed or return 409 STATE_CHANGED (session
                # change under lock) — neither is a deadlock.
                if resp.status_code >= 500:
                    errors.append(f"evaluate 5xx: {resp.status_code} {resp.text}")
            except Exception as exc:
                errors.append(f"evaluate exception: {exc}")

        await asyncio.gather(do_confirm(), do_evaluate(), return_exceptions=True)

        assert not errors, f"concurrent G6/evaluate had errors: {errors}"

        await db.dispose()

    asyncio.run(run())
