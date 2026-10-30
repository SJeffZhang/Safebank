"""Trusted local demo lifecycle. Rotate databases; never delete old demo history.

Only targets the isolated safebank-e2e-1029 project. Database administration and
migration credentials stay in mode-0600 local Compose overlays, never L1/BFF.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PROJECT = "safebank-e2e-1029"
SERVICES = ("frontend", "operator", "agent", "gateway", "bank-backend")
IDENTIFIER = re.compile(r"demo_[0-9]{14}_[a-f0-9]{8}\Z")


def private_write(path: Path, data: str | bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data.encode() if isinstance(data, str) else data)


def read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def database_url(url: str, name: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise ValueError("invalid database URL")
    return urlunsplit((parsed.scheme, parsed.netloc, "/" + name, "", ""))


def generation_names(generation: str) -> dict[str, str]:
    if not IDENTIFIER.fullmatch(generation):
        raise ValueError("invalid generation")
    return {kind: f"{kind}_{generation}" for kind in ("bank", "gateway", "agent")}


class DemoEnvironment:
    def __init__(self, deploy: Path):
        self.deploy = deploy.resolve()
        if not (self.deploy / "e2e.override.yml").is_file():
            raise ValueError("isolated e2e.override.yml required")
        self.state = self.deploy / ".demo-state"
        self.state.mkdir(mode=0o700, exist_ok=True)
        self.base = [str(self.deploy / "docker-compose.yml"), str(self.deploy / "e2e.override.yml")]

    def compose(self, *args: str, overlay: Path | None = None, data: str | None = None) -> str:
        env = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": PROJECT,
            "COMPOSE_FILE": os.pathsep.join(self.base + ([str(overlay)] if overlay else [])),
        }
        # Never echo stderr: Compose/migration failures can contain database URLs.
        result = subprocess.run(
            ["docker", "compose", *args],
            cwd=self.deploy,
            env=env,
            input=data,
            text=True,
            capture_output=True,
            timeout=240,
        )
        if result.returncode:
            raise RuntimeError("demo command failed: " + " ".join(args[:3]))
        return result.stdout

    def retain_image(self, image: str, tag: str) -> None:
        result = subprocess.run(
            ["docker", "image", "tag", image, tag], text=True, capture_output=True, timeout=30
        )
        if result.returncode:
            raise RuntimeError("cannot retain previous runtime image before build")

    def sql(self, service: str, database: str, query: str) -> str:
        return self.compose(
            "exec",
            "-T",
            service,
            "psql",
            "-U",
            "postgres",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-tA",
            data=query,
        ).strip()

    def current(self) -> dict | None:
        path = self.state / "current.json"
        return json.loads(path.read_text()) if path.exists() else None

    def reset(self) -> dict:
        generation = (
            "demo_" + datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(4)
        )
        names = generation_names(generation)
        directory = self.state / generation
        directory.mkdir(mode=0o700)
        prior = self.current()
        prior_overlay = self.state / prior["generation"] / "compose.yml" if prior else None
        # Pin the actual old images before rebuilding mutable local tags. A newer
        # schema/runtime may not be compatible with the previous generation.
        rollback_cfg = (
            yaml.safe_load(prior_overlay.read_text()) if prior_overlay else {"services": {}}
        )
        for service in SERVICES:
            image = self.compose("images", "-q", service, overlay=prior_overlay).strip()
            prior_image = rollback_cfg.get("services", {}).get(service, {}).get("image", "")
            if not image and re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", prior_image):
                image = prior_image
            if not image and prior is not None:
                raise RuntimeError("cannot pin previous runtime image before build")
            if image:
                if not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", image):
                    raise RuntimeError("ambiguous previous image identity")
                # Docker/containerd may discard an untagged replaced image.
                # Retain an explicit unique tag BEFORE overwriting the build tag.
                self.retain_image(image, f"safebank-demo-retained:{generation}-{service}")
                rollback_cfg.setdefault("services", {}).setdefault(service, {})["image"] = image
        rollback_overlay = directory / "rollback.yml"
        private_write(rollback_overlay, yaml.safe_dump(rollback_cfg))
        if prior_overlay is not None:
            # Keep demo-up of a retained generation on its original image IDs.
            # This preserves its runtime/schema compatibility after a failed upgrade.
            pinned = prior_overlay.with_suffix(".pin-next.yml")
            private_write(pinned, yaml.safe_dump(rollback_cfg))
            pinned.replace(prior_overlay)
        # Build using the deployment's exact checkout before interrupting any service.
        self.compose("build", "bank-backend", "frontend")
        self.compose("up", "-d", "--wait", "postgres-bank", "postgres-agent")
        for kind, owner, server in (
            ("bank", "postgres", "postgres-bank"),
            ("gateway", "postgres", "postgres-bank"),
            ("agent", "agent_app", "postgres-agent"),
        ):
            db = names[kind]
            roles = {
                "bank": "bank_app,bank_ro",
                "gateway": "gateway_app,gateway_admin",
                "agent": "agent_app",
            }[kind]
            if kind == "gateway":
                self.sql(
                    server,
                    "postgres",
                    "DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles "
                    "WHERE rolname='gateway_admin') THEN CREATE ROLE gateway_admin; "
                    "END IF; END $$;",
                )
            self.sql(
                server,
                "postgres",
                f'CREATE DATABASE "{db}" OWNER {owner};\n'
                f'REVOKE ALL ON DATABASE "{db}" FROM PUBLIC;\n'
                f'GRANT CONNECT ON DATABASE "{db}" TO {roles};',
            )
        envs = {
            k: read_env(self.deploy / ".secrets" / f"{k}.env") for k in ("bank", "gateway", "agent")
        }
        admin = (self.deploy / ".secrets/postgres-bank-password").read_text().strip()
        urls = {
            "bank": database_url(envs["bank"]["BANK_DATABASE_URL"], names["bank"]),
            "gateway": database_url(envs["gateway"]["GATEWAY_DATABASE_URL"], names["gateway"]),
            "agent": database_url(envs["agent"]["AGENT_DATABASE_URL"], names["agent"]),
        }
        migration = {
            k: f"postgresql://postgres:{quote(admin, safe='')}@postgres-bank/{names[k]}"
            for k in ("bank", "gateway")
        }
        key = Ed25519PrivateKey.generate()
        private_write(
            directory / "jwt-private.pem",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        private_write(
            directory / "jwt-public.pem",
            key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ),
        )
        os.chmod(directory / "jwt-public.pem", 0o644)
        public_mount = f"{directory}/jwt-public.pem:/run/secrets/jwt-public.pem:ro"
        cfg = {
            "services": {
                "bank-backend": {"environment": {"BANK_DATABASE_URL": urls["bank"]}},
                "gateway": {
                    "environment": {"GATEWAY_DATABASE_URL": urls["gateway"]},
                    "volumes": [
                        f"{directory}/jwt-private.pem:/run/bootstrap/jwt-private.pem:ro",
                        public_mount,
                    ],
                },
                "agent": {
                    "environment": {"AGENT_DATABASE_URL": urls["agent"]},
                    "volumes": [public_mount],
                },
                "bank-maintenance": {
                    "environment": {
                        "BANK_DATABASE_URL": urls["bank"],
                        "BANK_MIGRATION_DATABASE_URL": migration["bank"],
                    }
                },
                "gateway-maintenance": {
                    "environment": {
                        "GATEWAY_DATABASE_URL": urls["gateway"],
                        "GATEWAY_MIGRATION_DATABASE_URL": migration["gateway"],
                    }
                },
            }
        }
        overlay = directory / "compose.yml"
        private_write(overlay, yaml.safe_dump(cfg))
        # Prepare new databases while the current demo is still available.
        self.compose("run", "--rm", "bank-maintenance", overlay=overlay)
        self.compose("run", "--rm", "gateway-maintenance", overlay=overlay)
        self.compose(
            "run", "--rm", "bank-maintenance", "python", "-m", "app.bootstrap", overlay=overlay
        )
        self.compose(
            "run", "--rm", "bank-maintenance", "python", "-m", "app.seed_demo", overlay=overlay
        )
        # This is a new, unpublished database: set its dataset label once.
        self.sql(
            "postgres-bank",
            names["bank"],
            "UPDATE clock SET dataset_revision='l1_seed_v1.0' WHERE id=1;",
        )
        digest = self.verify(names)
        manifest = {
            "generation": generation,
            "databases": names,
            "initial": digest,
            "created_at": datetime.now(UTC).isoformat(),
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.deploy, text=True
            ).strip(),
        }
        # Record the prepared generation even if service switching fails.
        private_write(directory / "manifest.json", json.dumps(manifest, indent=2) + "\n")
        try:
            self.compose("stop", *SERVICES, overlay=prior_overlay)
            self.compose(
                "up", "-d", "--no-build", "--force-recreate", "--wait", *SERVICES, overlay=overlay
            )
            if self.verify(names) != digest:
                raise RuntimeError("initial state changed during startup")
            temporary = self.state / "current.next.json"
            if temporary.exists():
                raise RuntimeError("interrupted state publication; inspect current.next.json")
            private_write(temporary, json.dumps(manifest, indent=2) + "\n")
            temporary.replace(self.state / "current.json")
        except BaseException:
            # No old database is modified or deleted. Restore the previous deployment.
            self.compose(
                "up",
                "-d",
                "--no-build",
                "--force-recreate",
                "--wait",
                *SERVICES,
                overlay=rollback_overlay,
            )
            raise
        return manifest

    def verify(self, names: dict[str, str]) -> dict:
        # Fixed SQL only; never user-supplied assertions. Full row digest is stable
        # because seed IDs/timestamps are deterministic and no jobs have run yet.
        query = """SELECT json_build_object(
          'seed', (select dataset_revision from clock where id=1),
          'sim_now', (select sim_now from clock where id=1),
          'bank_revision', (select version_num from alembic_version),
          'customers', (select count(*) from customers
            where id in ('cus_zs','cus_ls','cus_sb','cus_ww')),
          'balanced', (select sum(debits_posted_cents)=sum(credits_posted_cents)
            AND sum(debits_pending_cents)=sum(credits_pending_cents) from accounts),
          'accounts', (select json_agg(json_build_array(id,credits_posted_cents,
            debits_posted_cents,debits_pending_cents,credits_pending_cents)
            order by id) from accounts),
          'instructions', (select json_agg(json_build_array(id,status,next_run_at)
            order by id) from standing_instructions),
          'orders', (select count(*) from fund_orders));"""
        data = json.loads(self.sql("postgres-bank", names["bank"], query))
        seed = json.loads(
            (self.deploy / "services/bank-backend/fixtures/l1_seed_v1.json").read_text()
        )
        if (
            data["customers"] != 4
            or data["balanced"] is not True
            or data["orders"] != 0
            or data["seed"] != seed["dataset_revision"]
            or datetime.fromisoformat(data["sim_now"]) != datetime.fromisoformat(seed["sim_now"])
        ):
            raise RuntimeError("invalid demo seed, clock or ledger")
        actual_accounts = {row[0]: row[1:] for row in data["accounts"]}
        for account in seed["accounts"]:
            if account.get("customer_id") is not None:
                expected = [
                    account[k]
                    for k in (
                        "credits_posted_cents",
                        "debits_posted_cents",
                        "debits_pending_cents",
                        "credits_pending_cents",
                    )
                ]
                if actual_accounts.get(account["id"]) != expected:
                    raise RuntimeError("unexpected initial account balance")
        data["gateway_revision"] = self.sql(
            "postgres-bank", names["gateway"], "SELECT version_num FROM alembic_version;"
        )
        for kind in ("bank", "gateway"):
            config = Config(
                str(
                    self.deploy
                    / f"services/{'bank-backend' if kind == 'bank' else kind}/alembic.ini"
                )
            )
            if data[kind + "_revision"] != ScriptDirectory.from_config(config).get_current_head():
                raise RuntimeError("migration head mismatch")
        data["digest"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return data

    def up(self) -> dict:
        current = self.current()
        if current is None:
            raise RuntimeError("no initialized demo; run make demo-reset first")
        overlay = self.state / current["generation"] / "compose.yml"
        self.compose("up", "-d", "--no-build", "--wait", *SERVICES, overlay=overlay)
        return {"generation": current["generation"], "url": "http://127.0.0.1:3001"}


def default_deploy() -> Path:
    if (Path.cwd() / "e2e.override.yml").is_file():
        return Path.cwd()
    common = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=Path(__file__).parent,
            text=True,
        ).strip()
    )
    return common.parent.parent / "Fintech2026-wt/e2e-deploy"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("reset", "up"))
    parser.add_argument(
        "--deploy",
        type=Path,
        default=default_deploy(),
    )
    args = parser.parse_args()
    demo = DemoEnvironment(args.deploy)
    with (demo.state / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = demo.reset() if args.action == "reset" else demo.up()
        except (RuntimeError, subprocess.SubprocessError) as exc:
            raise SystemExit(
                f"Demo lifecycle failed ({type(exc).__name__}); old databases retained"
            ) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
