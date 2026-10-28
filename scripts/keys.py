"""Create isolated local credentials once; never print secret values."""

import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _fix_public_key_permissions(root: Path) -> None:
    # The JWT public key is not secret and is mounted read-only into the
    # gateway and agent containers; on Linux the non-root container user cannot
    # read a 0600 host file, so it must be 0644 (the private key stays 0600).
    public = root / "jwt-public.pem"
    if public.exists():
        os.chmod(public, 0o644)


def generate(root: Path) -> bool:
    if root.exists():
        # Never rotate half the credentials underneath running services.
        if not (root / "complete").exists():
            raise RuntimeError("Incomplete secrets directory; inspect before retrying")
        _fix_public_key_permissions(root)
        return False
    root.mkdir(mode=0o700)

    def write(name: str, value: str, mode: int = 0o600) -> None:
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w") as stream:
            stream.write(value)

    def env(name: str, values: dict[str, str]) -> None:
        write(name, "".join(f"{key}={value}\n" for key, value in values.items()))

    tokens = {
        name: secrets.token_urlsafe(32)
        for name in (
            "GATEWAY_SERVICE_TOKEN",
            "FRONTEND_SERVICE_TOKEN",
            "AGENT_SERVICE_TOKEN",
            "SCHEDULER_SERVICE_TOKEN",
            "DEMO_CONSOLE_TOKEN",
            "APPROVAL_HMAC_KEY",
            "OTP_HMAC_KEY",
            "FINGERPRINT_KEY",
        )
    }
    passwords = {
        name: secrets.token_hex(24) for name in ("bank_app", "gateway_app", "bank_ro", "agent_app")
    }
    write("db-passwords.json", json.dumps(passwords))
    for server, roles in (
        ("bank", ("bank_app", "gateway_app", "bank_ro")),
        ("agent", ("agent_app",)),
    ):
        sql = "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;\n" + "".join(
            f"CREATE ROLE {role} LOGIN PASSWORD '{passwords[role]}';\n" for role in roles
        )
        for db, owner in (
            (("bank", "bank_app"), ("gateway", "gateway_app"))
            if server == "bank"
            else (("agent", "agent_app"),)
        ):
            sql += (
                f"CREATE DATABASE {db} OWNER {owner};\nREVOKE ALL ON DATABASE {db} FROM PUBLIC;\n"
            )
        if server == "bank":
            sql += "GRANT CONNECT ON DATABASE bank TO bank_ro;\n"
        write(f"init-{server}.sql", sql)
    for server in ("bank", "agent"):
        write(f"postgres-{server}-password", secrets.token_hex(24))
    pins = {
        customer: str(secrets.randbelow(900000) + 100000)
        for customer in ("cus_zs", "cus_ls", "cus_sb", "cus_ww")
    }
    write("demo_pins", json.dumps(pins))
    env(
        "gateway.env",
        {
            **tokens,
            "GATEWAY_DATABASE_URL": f"postgresql://gateway_app:{passwords['gateway_app']}@postgres-bank/gateway",
        },
    )
    env(
        "bank.env",
        {
            key: tokens[key]
            for key in ("GATEWAY_SERVICE_TOKEN", "SCHEDULER_SERVICE_TOKEN", "FINGERPRINT_KEY")
        }
        | {
            "BANK_DATABASE_URL": f"postgresql://bank_app:{passwords['bank_app']}@postgres-bank/bank"
        },
    )
    env(
        "agent.env",
        {
            "AGENT_SERVICE_TOKEN": tokens["AGENT_SERVICE_TOKEN"],
            "AGENT_DATABASE_URL": f"postgresql://agent_app:{passwords['agent_app']}@postgres-agent/agent",
            "GATEWAY_URL": "http://gateway:8200",
        },
    )
    env("frontend.env", {"FRONTEND_SERVICE_TOKEN": tokens["FRONTEND_SERVICE_TOKEN"]})
    key = Ed25519PrivateKey.generate()
    write(
        "jwt-private.pem",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    write(
        "jwt-public.pem",
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        mode=0o644,
    )
    write("complete", "v1\n")
    return True


if __name__ == "__main__":
    print("Secrets created" if generate(Path(".secrets")) else "Existing secrets preserved")
