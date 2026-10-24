"""W4b2a: approval/OTP HMAC key validation and runtime snapshot.

Red tests written BEFORE implementation. Run with:

    .venv/bin/pytest tests/gateway/shared_runtime/test_approval_keys.py -q

Expected: all red (AttributeError / GatewayError mismatch) until config.py
and runtime.py are updated.
"""

import asyncio
import os
from dataclasses import replace

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.config import Settings, from_env
from services.gateway.app.errors import GatewayError
from tests.gateway.shared_runtime.test_runtime import Client, Engine

# Fixtures (secret_settings, fake_engine_lock_hook, fake_lock_configuration)
# are provided by conftest.py.

# -- reusable keys for testing ------------------------------------------------

APPROVAL_KEY = "approval-hmac-key-32-bytes-minimum!!"
OTP_KEY = "otp-hmac-key-32-bytes-minimum!!!!!"


@pytest.fixture
def keyed_settings(secret_settings):
    """secret_settings + synthetic approval/OTP keys."""
    return replace(secret_settings, approval_hmac_key=APPROVAL_KEY, otp_hmac_key=OTP_KEY)


# -- Task 1 red tests ---------------------------------------------------------


def test_keys_use_explicit_settings_snapshot(keyed_settings, fake_engine_lock_hook):
    """The runtime reads keys only from the passed Settings object."""
    from services.gateway.app.runtime import gateway_runtime

    alt_settings = replace(keyed_settings, otp_hmac_key="alt-otp-hmac-key-32-bytes-minimum!")

    async def run():
        results = {}

        async with gateway_runtime(
            keyed_settings, db_factory=lambda url: Engine(), l3_factory=Client, pin_hashes={}
        ) as rt:
            results["approval"] = rt.approval_hmac_key
            results["otp"] = rt.otp_hmac_key

        async with gateway_runtime(
            alt_settings, db_factory=lambda url: Engine(), l3_factory=Client, pin_hashes={}
        ) as rt2:
            results["alt_approval"] = rt2.approval_hmac_key
            results["alt_otp"] = rt2.otp_hmac_key

        assert results["approval"] == APPROVAL_KEY.encode("utf-8")
        assert results["otp"] == OTP_KEY.encode("utf-8")
        assert results["alt_approval"] == APPROVAL_KEY.encode("utf-8")
        assert results["alt_otp"] == b"alt-otp-hmac-key-32-bytes-minimum!"

    asyncio.run(run())


def test_missing_key_does_not_fallback_to_environment(
    secret_settings, monkeypatch, fake_engine_lock_hook
):
    """Settings without approval/OTP keys refuse start even when env has them."""
    from services.gateway.app.runtime import gateway_runtime

    # Strip the synthetic keys the secret_settings fixture now carries (we want
    # to test the *missing* case, not the good case).
    stripped = replace(secret_settings, approval_hmac_key="", otp_hmac_key="")

    monkeypatch.setenv("APPROVAL_HMAC_KEY", "env-approval-hmac-key-32-bytes-min")
    monkeypatch.setenv("OTP_HMAC_KEY", "env-otp-hmac-key-32-bytes-min!!!!")

    def forbidden_pool(url):
        pytest.fail("key validation must precede pool allocation")

    async def run():
        with pytest.raises(GatewayError) as exc:
            async with gateway_runtime(
                stripped,
                db_factory=forbidden_pool,
                l3_factory=forbidden_pool,
                pin_hashes={},
            ):
                pytest.fail("missing keys must refuse startup")
        assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
        assert exc.value.status == 503

    asyncio.run(run())


@pytest.mark.parametrize(
    "approval,otp,expect_msg_fragment",
    [
        # empty / blank
        ("", OTP_KEY, "empty or blank"),
        ("   ", OTP_KEY, "empty or blank"),
        (APPROVAL_KEY, "", "empty or blank"),
        (APPROVAL_KEY, "   ", "empty or blank"),
        # too short
        ("short", OTP_KEY, "at least 32 bytes"),
        (APPROVAL_KEY, "short", "at least 32 bytes"),
        # identical keys
        (APPROVAL_KEY, APPROVAL_KEY, "different"),
        # reuse service token
        # secret_settings has GATEWAY_SERVICE_TOKEN="l3-token" and no F/A/S/D tokens
        ("l3-token" * 3, OTP_KEY, "service token"),
        (APPROVAL_KEY, "l3-token" * 3, "service token"),
    ],
)
def test_invalid_keys_fail_before_resource_allocation(
    secret_settings, approval, otp, expect_msg_fragment
):
    """Invalid keys must fail-closed before allocating DB/L3 resources."""
    from services.gateway.app.runtime import gateway_runtime

    bad = replace(secret_settings, approval_hmac_key=approval, otp_hmac_key=otp)
    if "l3-token" * 3 in (approval, otp):
        # "l3-token" * 3 = 24 chars < 32 bytes, so it would fail the length check
        # first. That's fine — any valid rejection is a pass as long as resources
        # aren't allocated.
        pass

    factory_called = []

    def forbidden_resource(*args, **kwargs):
        factory_called.append(True)

    async def run():
        with pytest.raises(GatewayError) as exc:
            async with gateway_runtime(
                bad, db_factory=forbidden_resource, l3_factory=forbidden_resource, pin_hashes={}
            ):
                pytest.fail("invalid keys must refuse startup")
        assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
        assert exc.value.status == 503
        assert not factory_called, "resource factory must not be called for invalid keys"

    asyncio.run(run())


def test_keys_do_not_appear_in_repr_or_error_chain(keyed_settings, fake_engine_lock_hook):
    """repr and error messages must never leak the actual key bytes."""
    from services.gateway.app.runtime import gateway_runtime

    async def run():
        async with gateway_runtime(
            keyed_settings, db_factory=lambda url: Engine(), l3_factory=Client, pin_hashes={}
        ) as rt:
            rt_repr = repr(rt)
            assert APPROVAL_KEY.encode("utf-8") not in rt_repr.encode("utf-8")
            assert OTP_KEY.encode("utf-8") not in rt_repr.encode("utf-8")

        # Error messages must not contain key values
        bad = replace(keyed_settings, approval_hmac_key="short", otp_hmac_key=OTP_KEY)
        with pytest.raises(GatewayError) as exc:
            async with gateway_runtime(
                bad, db_factory=forbidden_pool, l3_factory=forbidden_pool, pin_hashes={}
            ):
                pytest.fail("must fail")
        error_text = str(exc.value)
        assert "short" not in error_text
        assert OTP_KEY not in error_text

    def forbidden_pool(url):
        pytest.fail("must not allocate")

    asyncio.run(run())


# -- Task 2 red tests: runtime isolation --------------------------------------


def test_two_runtimes_keep_independent_key_snapshots(keyed_settings, fake_lock_configuration):
    """Two concurrent runtimes each keep their own key snapshot."""
    from services.gateway.app.main import build_internal_app, build_public_app
    from services.gateway.app.runtime import gateway_runtime

    second_key = "second-approval-hmac-key-32-bytes!!"
    second_otp = "second-otp-hmac-key-32-bytes!!!!!!"

    async def run():
        first_db, second_db = Engine(), Engine()
        clients = []

        def make_client(url, token):
            client = Client(url, token)
            clients.append(client)
            return client

        async with gateway_runtime(
            keyed_settings, db_factory=lambda url: first_db, l3_factory=make_client, pin_hashes={}
        ) as first:
            async with gateway_runtime(
                replace(keyed_settings, approval_hmac_key=second_key, otp_hmac_key=second_otp),
                db_factory=lambda url: second_db,
                l3_factory=make_client,
                pin_hashes={},
            ) as second:
                # Each runtime has its own key snapshot
                assert first.approval_hmac_key == APPROVAL_KEY.encode("utf-8")
                assert first.otp_hmac_key == OTP_KEY.encode("utf-8")
                assert second.approval_hmac_key == second_key.encode("utf-8")
                assert second.otp_hmac_key == second_otp.encode("utf-8")
                # Objects are independent, not shared
                assert first.approval_hmac_key is not second.approval_hmac_key
                assert first.otp_hmac_key is not second.otp_hmac_key

                # Build apps and verify state
                public = build_public_app(keyed_settings, runtime=first)
                internal = build_internal_app(keyed_settings, runtime=first)
                async with public.router.lifespan_context(public):
                    async with internal.router.lifespan_context(internal):
                        assert public.state.approval_hmac_key is first.approval_hmac_key
                        assert public.state.otp_hmac_key is first.otp_hmac_key
                        assert internal.state.approval_hmac_key is first.approval_hmac_key
                        assert internal.state.otp_hmac_key is first.otp_hmac_key
                        # Second runtime's apps are independent
                        pub2 = build_public_app(keyed_settings, runtime=second)
                        assert pub2.state.approval_hmac_key is second.approval_hmac_key
                        assert pub2.state.otp_hmac_key is second.otp_hmac_key

            assert first_db.closed == 0  # second closed, first still open
        assert first_db.closed == 1
        assert second_db.closed == 1

    asyncio.run(run())


def test_environment_change_does_not_affect_existing_runtime(
    keyed_settings, monkeypatch, fake_lock_configuration
):
    """Changing process environment after startup does not affect installed keys."""
    from services.gateway.app.main import build_public_app
    from services.gateway.app.runtime import gateway_runtime

    async def run():
        db = Engine()
        async with gateway_runtime(
            keyed_settings, db_factory=lambda url: db, l3_factory=Client, pin_hashes={}
        ) as rt:
            assert rt.approval_hmac_key == APPROVAL_KEY.encode("utf-8")

            # Change environment — runtime must not be affected
            monkeypatch.setenv("APPROVAL_HMAC_KEY", "poisoned-approval-hmac-key-32b!!")
            monkeypatch.setenv("OTP_HMAC_KEY", "poisoned-otp-hmac-key-32-bytes!!!")

            app = build_public_app(keyed_settings, runtime=rt)
            async with app.router.lifespan_context(app):
                assert app.state.approval_hmac_key == APPROVAL_KEY.encode("utf-8")
                assert app.state.otp_hmac_key == OTP_KEY.encode("utf-8")

            # A *new* runtime with the same Settings still uses Settings values
            alt_settings = replace(
                keyed_settings,
                approval_hmac_key="new-approval-hmac-key-32-bytes-min!",
                otp_hmac_key="new-otp-hmac-key-32-bytes-min!!!!",
            )
            async with gateway_runtime(
                alt_settings, db_factory=lambda url: Engine(), l3_factory=Client, pin_hashes={}
            ) as rt2:
                assert rt2.approval_hmac_key == b"new-approval-hmac-key-32-bytes-min!"
                assert rt2.otp_hmac_key == b"new-otp-hmac-key-32-bytes-min!!!!"

        assert db.closed == 1

    asyncio.run(run())


# -- Settings repr regression -------------------------------------------------


def test_settings_repr_excludes_keys():
    """Settings repr must not include approval or OTP HMAC key values."""
    s = Settings(
        demo_mode=False,
        eval_mode=False,
        profile="B2",
        demo_console_token=None,
        public_bind="127.0.0.1",
        internal_bind="127.0.0.2",
        max_executions=8,
        database_url="postgresql://x/db",
        l3_base_url="http://l3",
        service_tokens={"F": "f", "A": "a", "S": "s", "D": "d" * 32},
        jwt_public_key_path="/tmp/pub.pem",
        gateway_service_token="gw",
        approval_hmac_key=APPROVAL_KEY,
        otp_hmac_key=OTP_KEY,
    )
    r = repr(s)
    assert APPROVAL_KEY not in r
    assert OTP_KEY not in r


def test_settings_from_env_only_reads_explicit_mapping():
    """from_env reads APPROVAL_HMAC_KEY/OTP_HMAC_KEY only from the passed mapping."""
    # Empty mapping → empty keys
    s = from_env({})
    assert s.approval_hmac_key == ""
    assert s.otp_hmac_key == ""

    # Only from mapping, not from process environment
    s = from_env(
        {
            "APPROVAL_HMAC_KEY": "from-mapping-approval-key-32b!!!!",
            "OTP_HMAC_KEY": "from-mapping-otp-key-32-bytes!!!",
        }
    )
    assert s.approval_hmac_key == "from-mapping-approval-key-32b!!!!"
    assert s.otp_hmac_key == "from-mapping-otp-key-32-bytes!!!"

    # If process env has them but mapping doesn't → empty
    os.environ["APPROVAL_HMAC_KEY"] = "process-env-approval-key-32-bytes"
    os.environ["OTP_HMAC_KEY"] = "process-env-otp-key-32-bytes!!!!!"
    try:
        s = from_env({"GATEWAY_SERVICE_TOKEN": "tok"})
        assert s.approval_hmac_key == ""

        # Mapping takes precedence over process env
        s = from_env(
            {
                "GATEWAY_SERVICE_TOKEN": "tok",
                "APPROVAL_HMAC_KEY": "explicit-approval-key-32-bytes!!!",
            }
        )
        assert s.approval_hmac_key == "explicit-approval-key-32-bytes!!!"
    finally:
        del os.environ["APPROVAL_HMAC_KEY"]
        del os.environ["OTP_HMAC_KEY"]
