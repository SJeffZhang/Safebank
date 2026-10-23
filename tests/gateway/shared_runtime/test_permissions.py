"""Real Linux kernel permission acceptance, opt-in via make test-shared-runtime."""

import os

import pytest


def test_linux_private_secrets_bootstrap():
    if os.getenv("GATEWAY_SECRET_TESTS") != "1":
        pytest.skip("requires isolated Linux permission acceptance container")
    from scripts.gateway_permissions_acceptance import run

    run()


def test_linux_compose_healthcheck_drops_privileges():
    if os.getenv("GATEWAY_SECRET_TESTS") != "1":
        pytest.skip("requires isolated Linux healthcheck acceptance container")
    from scripts.gateway_permissions_acceptance import run_healthcheck

    run_healthcheck()
