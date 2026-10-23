import pytest

from services.gateway.app.config import from_env
from services.gateway.app.errors import GatewayError


def test_demo_and_eval_are_mutually_exclusive():
    with pytest.raises(GatewayError):
        from_env({"DEMO_MODE": "1", "EVAL_MODE": "true"}).validate()


def test_non_b2_profile_requires_eval_mode():
    with pytest.raises(GatewayError):
        from_env({"GATEWAY_PROFILE": "B1", "EVAL_MODE": "false"}).validate()
    assert from_env({"GATEWAY_PROFILE": "B1", "EVAL_MODE": "1"}).profile == "B1"


def test_b2_profile_does_not_require_eval_mode():
    assert from_env({}).profile == "B2"
    from_env({}).validate()


def test_unknown_profile_is_rejected():
    with pytest.raises(GatewayError):
        from_env({"GATEWAY_PROFILE": "B9"}).validate()


def test_short_demo_console_token_is_rejected():
    with pytest.raises(GatewayError):
        from_env({"DEMO_CONSOLE_TOKEN": "too-short"}).validate()


def test_adequate_demo_console_token_accepted():
    token = "x" * 32
    settings = from_env({"DEMO_CONSOLE_TOKEN": token, "EVAL_MODE": "1"})
    assert settings.demo_console_token == token
    settings.validate()


def test_truthy_parsing():
    assert from_env({"DEMO_MODE": "1"}).demo_mode
    assert from_env({"DEMO_MODE": "true"}).demo_mode
    assert not from_env({"DEMO_MODE": "0"}).demo_mode
    assert not from_env({}).demo_mode
