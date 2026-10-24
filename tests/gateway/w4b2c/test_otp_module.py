"""W4b2c Task 1: pure-function tests for approval/otp.py.

Red tests written FIRST — expected to fail until otp.py is implemented.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

WALL = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
MASTER_KEY = b"w4b2c-otp-master-key-at-least-32-bytes!!"


# ── generate_otp_code ────────────────────────────────────────────────────────


def test_generate_otp_code_is_6_digit_string():
    from services.gateway.app.approval.otp import generate_otp_code

    code = generate_otp_code()
    assert isinstance(code, str), f"expected str, got {type(code)}"
    assert len(code) == 6, f"expected 6 chars, got {len(code)}"
    assert code.isdigit(), f"expected all digits, got {code!r}"


def test_generate_otp_code_zero_padded():
    """Codes must be zero-padded to 6 digits (secrets.randbelow returns 0–999999)."""
    from services.gateway.app.approval.otp import generate_otp_code

    # Statistical test: 1000 calls, at least one < 100000 (would be 5-digit without padding)
    codes = [generate_otp_code() for _ in range(1000)]
    has_small = any(int(c) < 100000 for c in codes)
    assert has_small, "expected at least one code < 100000, zero-padding may be missing"
    # All codes must be exactly 6 characters.
    assert all(len(c) == 6 for c in codes)


def test_generate_otp_code_uniformish():
    """Rough uniformity: 1000 codes, each digit appears roughly equally."""
    from collections import Counter

    from services.gateway.app.approval.otp import generate_otp_code

    codes = [generate_otp_code() for _ in range(1000)]
    all_digits = "".join(codes)
    counts = Counter(all_digits)
    # Each digit (0–9) should appear at least 400 times out of 6000 digits.
    for d in "0123456789":
        assert counts[d] > 400, f"digit {d} appeared {counts[d]} times, expected >400"


# ── derive_otp_keys ──────────────────────────────────────────────────────────


def test_derive_otp_keys_outputs_differ():
    """Code digest key and AEAD key must be different (domain separation)."""
    from services.gateway.app.approval.otp import derive_otp_keys

    cdk, aead = derive_otp_keys(MASTER_KEY)
    assert cdk != aead, "code_digest_key must not equal aead_key"
    assert len(cdk) == 32
    assert len(aead) == 32


def test_derive_otp_keys_deterministic():
    """Same input always produces the same output."""
    from services.gateway.app.approval.otp import derive_otp_keys

    k1a, k1b = derive_otp_keys(MASTER_KEY)
    k2a, k2b = derive_otp_keys(MASTER_KEY)
    assert k1a == k2a
    assert k1b == k2b


def test_derive_otp_keys_different_master_yields_different_keys():
    """Different master keys produce totally different derived keys."""
    from services.gateway.app.approval.otp import derive_otp_keys

    a1, a2 = derive_otp_keys(b"aaaa-key-at-least-32-bytes-long!!")
    b1, b2 = derive_otp_keys(b"bbbb-key-at-least-32-bytes-long!!")
    assert a1 != b1
    assert a2 != b2


# ── compute_code_digest ──────────────────────────────────────────────────────


def test_compute_code_digest_deterministic():
    from services.gateway.app.approval.otp import compute_code_digest

    key = b"digest-key-32-bytes-long-at-least!!"
    d1 = compute_code_digest("123456", "apv_1", key)
    d2 = compute_code_digest("123456", "apv_1", key)
    assert d1 == d2


def test_compute_code_digest_different_code_yields_different_digest():
    from services.gateway.app.approval.otp import compute_code_digest

    key = b"digest-key-32-bytes-long-at-least!!"
    d1 = compute_code_digest("123456", "apv_1", key)
    d2 = compute_code_digest("654321", "apv_1", key)
    assert d1 != d2


def test_compute_code_digest_different_approval_yields_different_digest():
    from services.gateway.app.approval.otp import compute_code_digest

    key = b"digest-key-32-bytes-long-at-least!!"
    d1 = compute_code_digest("123456", "apv_1", key)
    d2 = compute_code_digest("123456", "apv_2", key)
    assert d1 != d2


def test_compute_code_digest_different_key_yields_different_digest():
    from services.gateway.app.approval.otp import compute_code_digest

    d1 = compute_code_digest("123456", "apv_1", b"key1-key-32-bytes-long-at-least!!")
    d2 = compute_code_digest("123456", "apv_1", b"key2-key-32-bytes-long-at-least!!")
    assert d1 != d2


# ── encrypt / decrypt round-trip ─────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    from services.gateway.app.approval.otp import decrypt_otp_code, encrypt_otp_code

    key = bytes.fromhex("aa" * 32)
    code = "789012"
    ciphertext = encrypt_otp_code(
        code, approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    assert isinstance(ciphertext, str)
    plaintext = decrypt_otp_code(
        ciphertext, approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    assert plaintext == code


def test_decrypt_wrong_key_returns_none():
    from services.gateway.app.approval.otp import decrypt_otp_code, encrypt_otp_code

    key_a = bytes.fromhex("aa" * 32)
    key_b = bytes.fromhex("bb" * 32)
    ciphertext = encrypt_otp_code(
        "111111", approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key_a
    )
    result = decrypt_otp_code(
        ciphertext, approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key_b
    )
    assert result is None, "decrypt with wrong key must return None"


def test_decrypt_wrong_approval_id_returns_none():
    from services.gateway.app.approval.otp import decrypt_otp_code, encrypt_otp_code

    key = bytes.fromhex("aa" * 32)
    ciphertext = encrypt_otp_code(
        "222222", approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    result = decrypt_otp_code(
        ciphertext, approval_id="apv_2", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    assert result is None, "AAD mismatch (wrong approval_id) must fail"


def test_decrypt_wrong_plan_id_returns_none():
    from services.gateway.app.approval.otp import decrypt_otp_code, encrypt_otp_code

    key = bytes.fromhex("aa" * 32)
    ciphertext = encrypt_otp_code(
        "333333", approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    result = decrypt_otp_code(
        ciphertext, approval_id="apv_1", plan_id="pln_2", view_hash="v" * 64, wall_now=WALL, key=key
    )
    assert result is None, "AAD mismatch (wrong plan_id) must fail"


def test_decrypt_wrong_view_hash_returns_none():
    from services.gateway.app.approval.otp import decrypt_otp_code, encrypt_otp_code

    key = bytes.fromhex("aa" * 32)
    ciphertext = encrypt_otp_code(
        "444444", approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    result = decrypt_otp_code(
        ciphertext, approval_id="apv_1", plan_id="pln_1", view_hash="w" * 64, wall_now=WALL, key=key
    )
    assert result is None, "AAD mismatch (wrong view_hash) must fail"


def test_decrypt_wrong_wall_now_returns_none():
    from services.gateway.app.approval.otp import decrypt_otp_code, encrypt_otp_code

    key = bytes.fromhex("aa" * 32)
    ciphertext = encrypt_otp_code(
        "555555", approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    later = WALL + timedelta(seconds=1)
    result = decrypt_otp_code(
        ciphertext, approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=later, key=key
    )
    assert result is None, "AAD mismatch (wrong wall_now) must fail"


def test_decrypt_bitflipped_ciphertext_returns_none():
    """A single bit flip in the ciphertext must cause AEAD tag validation failure."""
    from services.gateway.app.approval.otp import decrypt_otp_code, encrypt_otp_code

    key = bytes.fromhex("aa" * 32)
    ciphertext = encrypt_otp_code(
        "666666", approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    raw = bytes.fromhex(ciphertext)
    # Flip the last byte.
    flipped = bytes(raw[:-1]) + bytes([raw[-1] ^ 0x01])
    result = decrypt_otp_code(
        flipped.hex(), approval_id="apv_1", plan_id="pln_1", view_hash="v" * 64, wall_now=WALL, key=key
    )
    assert result is None, "bit-flipped ciphertext must fail AEAD tag check"


# ── verify_otp ───────────────────────────────────────────────────────────────


def test_verify_otp_correct_code_returns_true():
    from services.gateway.app.approval.otp import compute_code_digest, verify_otp

    key = b"verify-key-32-bytes-long-at-least!!"
    code = "123456"
    digest = compute_code_digest(code, "apv_1", key)
    challenge = {"code_digest": digest}
    assert verify_otp(challenge, code, "apv_1", key) is True


def test_verify_otp_wrong_code_returns_false():
    from services.gateway.app.approval.otp import compute_code_digest, verify_otp

    key = b"verify-key-32-bytes-long-at-least!!"
    digest = compute_code_digest("123456", "apv_1", key)
    challenge = {"code_digest": digest}
    assert verify_otp(challenge, "654321", "apv_1", key) is False


def test_verify_otp_wrong_approval_id_returns_false():
    from services.gateway.app.approval.otp import compute_code_digest, verify_otp

    key = b"verify-key-32-bytes-long-at-least!!"
    digest = compute_code_digest("123456", "apv_1", key)
    challenge = {"code_digest": digest}
    assert verify_otp(challenge, "123456", "apv_2", key) is False


def test_verify_otp_wrong_key_returns_false():
    from services.gateway.app.approval.otp import compute_code_digest, verify_otp

    key_a = b"key-a-32-bytes-long-at-least-bytes!!"
    digest = compute_code_digest("123456", "apv_1", key_a)
    challenge = {"code_digest": digest}
    assert verify_otp(challenge, "123456", "apv_1", b"key-b-32-bytes-long-at-least-bytes!!") is False


def test_verify_otp_same_length_wrong_code_no_early_exit():
    """Constant-time: same-length wrong code must follow the same code path
    (at minimum, the function must not raise or return early differently)."""
    from services.gateway.app.approval.otp import compute_code_digest, verify_otp

    key = b"verify-key-32-bytes-long-at-least!!"
    digest = compute_code_digest("123456", "apv_1", key)
    challenge = {"code_digest": digest}

    # Both calls must return bool without exception.
    assert verify_otp(challenge, "123456", "apv_1", key) is True
    assert verify_otp(challenge, "999999", "apv_1", key) is False
    # Different-length code should also not crash (unlikely in practice
    # because both are generated 6-digit codes, but robustness matters).
    assert verify_otp(challenge, "12345", "apv_1", key) is False


# ── otp_retry_after ──────────────────────────────────────────────────────────


def test_otp_retry_after_can_send():
    from services.gateway.app.approval.otp import otp_retry_after

    # Never sent before.
    result = otp_retry_after(None, 0, WALL)
    assert result is None, "should be able to send when never sent"


def test_otp_retry_after_interval_not_elapsed():
    from services.gateway.app.approval.otp import otp_retry_after

    last = WALL - timedelta(seconds=10)  # 10s ago, need 30s
    result = otp_retry_after(last, 1, WALL)
    assert result is not None
    assert result > 0


def test_otp_retry_after_interval_elapsed():
    from services.gateway.app.approval.otp import otp_retry_after

    last = WALL - timedelta(seconds=40)  # 40s ago
    result = otp_retry_after(last, 1, WALL)
    assert result is None, "should be able to send after 30s interval"


def test_otp_retry_after_sent_count_exhausted():
    from services.gateway.app.approval.otp import otp_retry_after

    with pytest.raises(ValueError, match="exhausted"):
        otp_retry_after(WALL, 3, WALL)


def test_otp_retry_after_sent_count_exhausted_even_with_interval_elapsed():
    from services.gateway.app.approval.otp import otp_retry_after

    last = WALL - timedelta(hours=1)  # well past interval
    with pytest.raises(ValueError, match="exhausted"):
        otp_retry_after(last, 3, WALL)


# ── otp_attempts_exceeded ────────────────────────────────────────────────────


def test_otp_attempts_exceeded_false_at_4():
    from services.gateway.app.approval.otp import otp_attempts_exceeded

    assert otp_attempts_exceeded(4) is False


def test_otp_attempts_exceeded_true_at_5():
    from services.gateway.app.approval.otp import otp_attempts_exceeded

    assert otp_attempts_exceeded(5) is True


def test_otp_attempts_exceeded_true_above_5():
    from services.gateway.app.approval.otp import otp_attempts_exceeded

    assert otp_attempts_exceeded(6) is True