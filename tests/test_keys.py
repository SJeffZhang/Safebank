import stat

import pytest
from cryptography.hazmat.primitives import serialization

from scripts.keys import generate


def test_generation_is_private_isolated_and_idempotent(tmp_path):
    root = tmp_path / "secrets"
    assert generate(root)
    private = serialization.load_pem_private_key(
        (root / "jwt-private.pem").read_bytes(), password=None
    )
    public = serialization.load_pem_public_key((root / "jwt-public.pem").read_bytes())
    public.verify(private.sign(b"acceptance"), b"acceptance")
    for server in ("bank", "agent"):
        assert (
            "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC"
            in (root / f"init-{server}.sql").read_text()
        )
    before = (root / "gateway.env").read_bytes()
    assert not generate(root)
    assert (root / "gateway.env").read_bytes() == before
    for item in root.iterdir():
        assert stat.S_IMODE(item.stat().st_mode) == 0o600
    for name in ("agent.env", "frontend.env"):
        assert "GATEWAY_SERVICE_TOKEN" not in (root / name).read_text()
        assert "DEMO_CONSOLE_TOKEN" not in (root / name).read_text()


def test_partial_generation_cannot_silently_rotate(tmp_path):
    root = tmp_path / "secrets"
    root.mkdir()
    with pytest.raises(RuntimeError):
        generate(root)
