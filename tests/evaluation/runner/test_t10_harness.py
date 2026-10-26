"""E0b T10: login caching, 429 → StopRun, 401 → re-login."""

from __future__ import annotations

import asyncio
from pathlib import Path

import evaluation.runner.harness as harness_mod
from evaluation.runner.harness import GatewayRateLimited, eval_harness
from evaluation.runner.loop import StopRun, run_corpus

FIXTURE = Path(__file__).parent / "fixtures" / "mini_seed.json"


# ── fake ports factory (reuse same pattern as cli.py) ────────────────────────


def _fake_ports(case):
    from services.agent.app.graph import Understanding

    return dict(
        understand=lambda s, c: Understanding(intents=[], needs_write=False),
        gather=lambda s, c: [],
        plan=lambda s, c: None,
        respond=lambda s, c: "ok",
        verify_output=lambda s, c: "ok",
    )


# ── minimal corpus case ──────────────────────────────────────────────────────


def _minimal_case() -> dict:
    return {
        "case_id": "t10-minimal",
        "category": "transfer",
        "variant": "simple",
        "expected": {"outcome": "NO_PLAN", "risk": None, "params": {}},
        "turns": [{"text": "hello"}],
    }


# ── tests ────────────────────────────────────────────────────────────────────


class TestLoginCaching:
    def test_same_customer_logs_in_once(self, gateway_db, obligations_db, monkeypatch):
        """5 new_session calls for same customer → only 1 /auth/login POST."""
        login_count = [0]
        _orig = harness_mod._login

        async def _counting_login(client, customer_id):
            login_count[0] += 1
            return await _orig(client, customer_id)

        monkeypatch.setattr(harness_mod, "_login", _counting_login)

        async def run():
            async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
                for _ in range(5):
                    sid = await h.new_session("cus_zs")
                    assert sid
                assert login_count[0] == 1

        asyncio.run(run())


class TestRateLimitedStopRun:
    def test_429_on_create_session_stops_with_runs(self, gateway_db, obligations_db, monkeypatch):
        """/sessions returns 429 → StopRun raised with completed runs."""
        call_count = [0]
        _orig_create = harness_mod._create_session

        async def _flaky_session(client, token):
            call_count[0] += 1
            if call_count[0] <= 1:
                # First call (iteration 0 login) goes through real session creation.
                return await _orig_create(client, token)
            raise GatewayRateLimited(code="RATE_LIMITED", detail="too many")

        monkeypatch.setattr(harness_mod, "_create_session", _flaky_session)

        async def run():
            async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
                try:
                    await run_corpus(
                        h,
                        [_minimal_case()],
                        ports_factory=_fake_ports,
                        repeat=2,
                        customer_id="cus_zs",
                    )
                except StopRun as exc:
                    assert "RATE_LIMITED" in str(exc)
                    # At least the first iteration completed (one CaseRun).
                    assert len(exc.runs) >= 1
                    return
                raise AssertionError("StopRun was not raised")

        asyncio.run(run())


class TestTokenExpiryRelogin:
    def test_401_on_cached_session_triggers_relogin(self, gateway_db, obligations_db, monkeypatch):
        """Cached token gets 401 → clear cache → re-login → success."""
        login_count = [0]
        session_count = [0]
        _orig_login = harness_mod._login

        async def _counting_login(client, customer_id):
            login_count[0] += 1
            return await _orig_login(client, customer_id)

        async def _flaky_session(client, token):
            session_count[0] += 1
            if session_count[0] == 2:
                # Second _create_session call (cached token) → 401 simulation.
                # The real new_session catches AssertionError from the real function;
                # we raise AssertionError here to simulate the same path.
                raise AssertionError("401 Unauthorized (simulated)")
            return "sid-from-session-" + str(session_count[0])

        monkeypatch.setattr(harness_mod, "_login", _counting_login)
        monkeypatch.setattr(harness_mod, "_create_session", _flaky_session)

        async def run():
            async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
                # First call: no cache → login (count 1) → session (count 1) → ok.
                sid1 = await h.new_session("cus_zs")
                assert sid1 == "sid-from-session-1"
                # Second call: cache hit → session (count 2) → 401 → clear cache
                # → login (count 2) → session (count 3) → ok.
                sid2 = await h.new_session("cus_zs")
                assert sid2 == "sid-from-session-3"
                # Login called twice: once initially, once after 401.
                assert login_count[0] == 2
                # Sessions: 1 (initial) + 1 (cache 401) + 1 (retry) = 3
                assert session_count[0] == 3

        asyncio.run(run())
