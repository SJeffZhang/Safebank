"""Explicit startup PIN files cannot inherit ambient or previous-startup secrets."""

import json

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.auth import pin
from services.gateway.app.errors import GatewayError


@pytest.fixture(autouse=True)
def clear_legacy_pin_cache():
    pin.configured_hashes.cache_clear()
    yield
    pin.configured_hashes.cache_clear()


@pytest.mark.parametrize("ambient_hashes", ["valid", "invalid"])
def test_explicit_pin_file_wins_over_both_environment_sources(
    tmp_path, monkeypatch, ambient_hashes
):
    selected, other = tmp_path / "selected.json", tmp_path / "other.json"
    selected.write_text(json.dumps({"cus_zs": "123456"}))
    other.write_text(json.dumps({"cus_zs": "654321"}))
    monkeypatch.setenv("DEMO_PINS_FILE", str(other))
    monkeypatch.setenv(
        "DEMO_PIN_HASHES",
        json.dumps({"cus_zs": pin.HASHER.hash("654321")})
        if ambient_hashes == "valid"
        else "invalid",
    )
    hashes = pin.configured_hashes_from_file(str(selected))
    assert set(hashes) == {"cus_zs"}
    assert pin.verify_pin("123456", hashes["cus_zs"])
    assert not pin.verify_pin("654321", hashes["cus_zs"])


def test_explicit_paths_and_startups_do_not_share_legacy_or_file_cache(tmp_path, monkeypatch):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(json.dumps({"cus_zs": "123456"}))
    second.write_text(json.dumps({"cus_zs": "654321"}))
    monkeypatch.setenv("DEMO_PINS_FILE", str(first))
    monkeypatch.delenv("DEMO_PIN_HASHES", raising=False)
    legacy = pin.configured_hashes()
    other_startup = pin.configured_hashes_from_file(str(second))
    assert pin.verify_pin("654321", other_startup["cus_zs"])
    assert not pin.verify_pin("123456", other_startup["cus_zs"])
    assert pin.verify_pin("123456", legacy["cus_zs"])
    # A new startup at the same path must see replacement contents; the first
    # startup keeps its in-memory snapshot after its file disappears.
    first.write_text(json.dumps({"cus_zs": "777777"}))
    next_startup = pin.configured_hashes_from_file(str(first))
    first.unlink()
    second.unlink()
    assert pin.verify_pin("777777", next_startup["cus_zs"])
    assert not pin.verify_pin("123456", next_startup["cus_zs"])
    assert pin.verify_pin("123456", legacy["cus_zs"])
    assert pin.verify_pin("654321", other_startup["cus_zs"])


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not-json",
        "[]",
        "null",
        '{"cus_unknown":"123456"}',
        '{"cus_zs":null}',
        '{"cus_zs":true}',
        '{"cus_zs":123456}',
        '{"cus_zs":""}',
        json.dumps({"cus_zs": "x" * 129}),
    ],
)
def test_invalid_explicit_pin_file_fails_closed_even_with_valid_environment(
    tmp_path, monkeypatch, payload
):
    selected = tmp_path / "invalid.json"
    selected.write_text(payload)
    monkeypatch.setenv("DEMO_PIN_HASHES", json.dumps({"cus_zs": pin.HASHER.hash("654321")}))
    with pytest.raises(GatewayError) as exc:
        pin.configured_hashes_from_file(str(selected))
    assert exc.value.status == 503 and exc.value.code is Code.DEPENDENCY_UNAVAILABLE
    assert "654321" not in str(exc.value) and str(selected) not in str(exc.value)


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid_utf8"])
def test_unreadable_explicit_pin_file_does_not_fall_back(tmp_path, monkeypatch, kind):
    selected = tmp_path / "unreadable"
    if kind == "directory":
        selected.mkdir()
    elif kind == "invalid_utf8":
        selected.write_bytes(b"\xff")
    monkeypatch.setenv("DEMO_PIN_HASHES", json.dumps({"cus_zs": pin.HASHER.hash("654321")}))
    with pytest.raises(GatewayError) as exc:
        pin.configured_hashes_from_file(str(selected))
    assert exc.value.status == 503 and exc.value.code is Code.DEPENDENCY_UNAVAILABLE


def test_legacy_environment_hashes_keep_zero_argument_entrypoint(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_PINS_FILE", str(tmp_path / "absent"))
    monkeypatch.setenv("DEMO_PIN_HASHES", json.dumps({"cus_zs": pin.HASHER.hash("123456")}))
    assert pin.verify_pin("123456", pin.configured_hashes()["cus_zs"])


def test_legacy_hashes_use_the_same_validation_and_reject_bad_encoding(monkeypatch):
    monkeypatch.setenv("DEMO_PIN_HASHES", json.dumps({"cus_zs": "$argon2-invalid"}))
    with pytest.raises(GatewayError) as exc:
        pin.configured_hashes()
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE
