"""P2 production fix: G4 approval creation uses instance keys (not global configure).

Before this fix, ``create_for_evaluation`` relied on a module-level ``_config``
set by ``approval_service.configure()``.  Production has ``GatewayRuntime.install``
which places keys on ``request.app.state`` but never calls ``configure()``, so
G4 returned 503.  The old w3b ``conftest.py`` called ``configure()`` manually,
masking the bug.

These tests prove that:
- G4 works with app.state keys and NO global configure()
- Two apps with different keys do not interfere (binding MAC verified per-key)
- Instance config validation returns 503 (not 500) for bad inputs
- Global config is never a fallback when instance args are passed
"""

from __future__ import annotations

import asyncio

from safebank_contracts.codes import Code
from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.approval import service as approval_service
from services.gateway.app.approval.service import ApprovalBinding, verify_binding
from tests.gateway.w3b.db.conftest import (
    _seed_full,
    _seed_read,
    _transfer_step,
    build_app,
    make_body,
    make_plan_draft_dict,
)

EVALUATE_URL = "/plans/evaluate"


def _runtime_db(app):
    """Return a fresh engine sharing the app's event loop + connection pool."""
    from services.gateway.app.db import engine

    return engine(app.db_url, poolclass=NullPool)


def _app_state(app):
    """Shortcut to the FastAPI app state from a TestApp."""
    return app.client._transport.app.state


def _body(request_id: str, session_id: str, utterance_ids: list[str], steps=None) -> dict:
    pd = make_plan_draft_dict(
        request_id=request_id,
        session_id=session_id,
        utterance_ids=utterance_ids,
        steps=steps,
    )
    return make_body(request_id=request_id, plan_draft=pd)


def _step_with_sources(sid: str, rid_acc: str, rid_pay: str, amount_cents: int = 80000) -> dict:
    """A transfer step with arg_sources referencing read records."""
    amount_yuan = str(amount_cents // 100)
    amount_start = 3
    amount_end = amount_start + len(amount_yuan)
    step = _transfer_step(amount_cents=amount_cents)
    step["args"]["memo"] = None
    step["arg_sources"] = [
        {
            "param": "amount_cents",
            "source": {
                "kind": "utterance",
                "ref": f"utt_{sid}_1",
                "span": [amount_start, amount_end],
            },
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


async def _seed_basic(app, db, sid: str, jti: str, amount_cents: int = 80000) -> tuple[str, str]:
    """Seed session, auth, read records. Returns (rid_acc, rid_pay)."""
    amount_yuan = str(amount_cents // 100)
    text = f"转账 {amount_yuan} 元"
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
    return rid_acc, rid_pay


async def _verify_binding_from_db(db, approval_id: str) -> tuple[ApprovalBinding, str]:
    """Load approval, evaluation, plan from DB and construct ApprovalBinding."""
    async with db.connect() as conn:
        apv = (
            (await conn.execute(select(t.approvals).where(t.approvals.c.id == approval_id)))
            .mappings()
            .one()
        )
        evl = (
            (
                await conn.execute(
                    select(t.evaluations).where(t.evaluations.c.id == apv["evaluation_id"])
                )
            )
            .mappings()
            .one()
        )
        pln = (
            (await conn.execute(select(t.plans).where(t.plans.c.id == apv["plan_id"])))
            .mappings()
            .one()
        )
    binding = ApprovalBinding(
        customer_id=pln["customer_id"],
        sid=pln["sid"],
        plan_id=pln["id"],
        plan_hash=pln["plan_hash"],
        evaluation_id=evl["id"],
        versions=evl["versions"],
        clock_version=evl["clock_version"],
        policy_version=evl["policy_version"],
        profile="B2",
        view_hash=apv["view_hash"],
        expires_at_wall=apv["expires_at_wall"],
    )
    return binding, apv["binding_mac"]


# ── Test: G4 uses instance keys, not global configure ─────────────────────


def test_g4_without_global_configure_uses_instance_keys(app_config):
    """P2 fix RED→GREEN: G4 works with app.state keys, no global configure()."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_noglobal"
        jti = "jti-noglobal"
        rid_acc, rid_pay = await _seed_basic(app, db, sid, jti)

        # Simulate production: keys are on app.state but NO global configure().
        approval_service._config = None  # type: ignore[attr-defined]

        step = _step_with_sources(sid, rid_acc, rid_pay)
        body = _body("req_noglobal", sid, [f"utt_{sid}_1"], steps=[step])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

        resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert resp.status_code == 200, (
            f"Expected 200 YELLOW/REQUIRE_APPROVAL with instance keys, "
            f"got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["decision"] == "REQUIRE_APPROVAL"
        assert data["risk"] == "YELLOW"
        assert data["approval_id"].startswith("apv_")

        # Verify the MAC from DB with the app.state key.
        binding, mac = await _verify_binding_from_db(db, data["approval_id"])
        assert verify_binding(_app_state(app).approval_hmac_key, binding, mac), (
            "MAC should verify with app.state key"
        )

        await db.dispose()

    asyncio.run(run())


# ── Test: two apps with different keys, real binding verification ────────


def test_two_apps_different_keys_no_cross_interference(app_config):
    """Two apps with different HMAC keys: each creates valid approvals.

    After building each app with the shared conftest (which calls global
    configure), we: (a) clear the global config so the instance path is used,
    (b) set each app's state key independently.

    Both approvals must verify with their own key and FAIL with the other's.
    """

    async def run():
        app1 = await build_app(app_config)
        app2 = await build_app(app_config)

        # Clear global — instance keys take over.
        approval_service._config = None  # type: ignore[attr-defined]

        # Set different keys on each app's state.
        _app_state(app1).approval_hmac_key = b"\x01" * 32
        _app_state(app2).approval_hmac_key = b"\x02" * 32

        sid1, sid2 = "ses_twoapp_1", "ses_twoapp_2"
        db1, db2 = _runtime_db(app1), _runtime_db(app2)
        rid_acc1, rid_pay1 = await _seed_basic(app1, db1, sid1, "jti-twoapp-1")
        rid_acc2, rid_pay2 = await _seed_basic(app2, db2, sid2, "jti-twoapp-2")

        resp1 = await app1.client.post(
            EVALUATE_URL,
            json=_body(
                "req_twoapp_1",
                sid1,
                [f"utt_{sid1}_1"],
                steps=[_step_with_sources(sid1, rid_acc1, rid_pay1)],
            ),
            headers=app1.auth_headers(sid=sid1, jti="jti-twoapp-1", utts=[f"utt_{sid1}_1"]),
        )
        assert resp1.status_code == 200, f"app1: {resp1.status_code} {resp1.text}"
        data1 = resp1.json()
        assert data1["decision"] == "REQUIRE_APPROVAL"
        assert data1["approval_id"].startswith("apv_")

        resp2 = await app2.client.post(
            EVALUATE_URL,
            json=_body(
                "req_twoapp_2",
                sid2,
                [f"utt_{sid2}_1"],
                steps=[_step_with_sources(sid2, rid_acc2, rid_pay2)],
            ),
            headers=app2.auth_headers(sid=sid2, jti="jti-twoapp-2", utts=[f"utt_{sid2}_1"]),
        )
        assert resp2.status_code == 200, f"app2: {resp2.status_code} {resp2.text}"
        data2 = resp2.json()
        assert data2["decision"] == "REQUIRE_APPROVAL"
        assert data2["approval_id"].startswith("apv_")

        # Real dual-key binding: each verifies with own key, fails with other.
        binding1, mac1 = await _verify_binding_from_db(db1, data1["approval_id"])
        binding2, mac2 = await _verify_binding_from_db(db2, data2["approval_id"])

        key1 = b"\x01" * 32
        key2 = b"\x02" * 32

        assert verify_binding(key1, binding1, mac1), "app1's MAC must verify with key1"
        assert verify_binding(key2, binding2, mac2), "app2's MAC must verify with key2"
        assert not verify_binding(key2, binding1, mac1), "app1's MAC must NOT verify with key2"
        assert not verify_binding(key1, binding2, mac2), "app2's MAC must NOT verify with key1"

        # MACs themselves differ — not because eval IDs differ, but because keys differ.
        assert mac1 != mac2, "Different keys should produce different MACs"

        await db1.dispose()
        await db2.dispose()

    asyncio.run(run())


# ── Test: stale global config does not affect instance-keyed creation ─────


def test_g4_bad_global_configure_does_not_affect_instance_keyed_create(app_config):
    """A stale/wrong global configure() is ignored when instance keys are passed."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_stale"
        jti = "jti-stale"
        rid_acc, rid_pay = await _seed_basic(app, db, sid, jti)

        st = _app_state(app)

        # Set a WRONG global config (different key + B0 profile).
        approval_service.configure(
            approval_hmac_key=b"\xff" * 32,
            profile="B0",
            wall_now=st.wall_now,
        )

        step = _step_with_sources(sid, rid_acc, rid_pay)
        body = _body("req_stale", sid, [f"utt_{sid}_1"], steps=[step])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

        resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert resp.status_code == 200, (
            f"Expected 200 with instance keys, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["decision"] == "REQUIRE_APPROVAL"
        assert data["approval_id"].startswith("apv_")

        # Verify the MAC was computed with the correct app.state key, not the stale global key.
        binding, mac = await _verify_binding_from_db(db, data["approval_id"])
        assert verify_binding(st.approval_hmac_key, binding, mac), (
            "MAC should verify with app.state key"
        )
        assert not verify_binding(b"\xff" * 32, binding, mac), (
            "MAC should NOT verify with stale global key"
        )

        await db.dispose()

    asyncio.run(run())


# ── Test: missing instance key → 503 (RED: global valid but not used) ──────


def test_instance_missing_key_returns_503(app_config):
    """When app.state has no approval_hmac_key, G4 returns 503 even if global is valid."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_misskey"
        jti = "jti-misskey"
        rid_acc, rid_pay = await _seed_basic(app, db, sid, jti)

        # Clear both global and instance keys.
        approval_service._config = None  # type: ignore[attr-defined]
        _app_state(app).approval_hmac_key = None  # remove instance key

        step = _step_with_sources(sid, rid_acc, rid_pay)
        body = _body("req_misskey", sid, [f"utt_{sid}_1"], steps=[step])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

        resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert resp.status_code == 503, (
            f"Expected 503 when approval_hmac_key is missing, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["error"]["code"] == Code.DEPENDENCY_UNAVAILABLE.value

        await db.dispose()

    asyncio.run(run())


# ── Test: short instance key → 503 ────────────────────────────────────────


def test_instance_short_key_returns_503(app_config):
    """A key shorter than 32 bytes returns 503, not 500."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_short"
        jti = "jti-short"
        rid_acc, rid_pay = await _seed_basic(app, db, sid, jti)

        approval_service._config = None  # type: ignore[attr-defined]
        _app_state(app).approval_hmac_key = b"short"  # 5 bytes

        step = _step_with_sources(sid, rid_acc, rid_pay)
        body = _body("req_short", sid, [f"utt_{sid}_1"], steps=[step])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

        resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert resp.status_code == 503, (
            f"Expected 503 for short key, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["error"]["code"] == Code.DEPENDENCY_UNAVAILABLE.value

        await db.dispose()

    asyncio.run(run())


# ── Test: non-bytes key → 503 ─────────────────────────────────────────────


def test_instance_non_bytes_key_returns_503(app_config):
    """A key that is not bytes (e.g. str) returns 503, not 500."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_strkey"
        jti = "jti-strkey"
        rid_acc, rid_pay = await _seed_basic(app, db, sid, jti)

        approval_service._config = None  # type: ignore[attr-defined]
        _app_state(app).approval_hmac_key = "A" * 32  # str, not bytes

        step = _step_with_sources(sid, rid_acc, rid_pay)
        body = _body("req_strkey", sid, [f"utt_{sid}_1"], steps=[step])
        hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

        resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
        assert resp.status_code == 503, (
            f"Expected 503 for non-bytes key, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["error"]["code"] == Code.DEPENDENCY_UNAVAILABLE.value

        await db.dispose()

    asyncio.run(run())


# ── Cleanup: reset global config after tests that call configure() ────────


def test_reset_global_configure():
    """Reset global config so later tests aren't polluted by test_g4_bad_global_configure."""
    approval_service._config = None  # type: ignore[attr-defined]
