"""Exercise Linux file ownership with disposable Docker volumes, never live bank data."""

import json
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import yaml

from scripts.keys import generate

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    prefix = "safebank-bootstrap-" + uuid4().hex[:10]
    volumes: list[str] = []
    containers: list[str] = []
    private_values: list[str] = []

    def run(args: list[str], *, stdin: str | None = None, check: bool = True) -> str:
        result = subprocess.run(
            ["docker", *args], input=stdin, capture_output=True, text=True, timeout=60
        )
        if check and result.returncode:
            output = result.stderr[-2000:]
            for value in private_values:
                output = output.replace(value, "[REDACTED]")
            raise RuntimeError(f"Docker acceptance command failed: {output}")
        return (result.stdout + (result.stderr if args[0] == "logs" else "")).strip()

    def volume(suffix: str) -> str:
        name = prefix + "-" + suffix
        run(["volume", "create", "--label", "safebank.acceptance=bootstrap", name])
        volumes.append(name)
        return name

    try:
        with tempfile.TemporaryDirectory(prefix=prefix + "-") as temporary:
            secrets = Path(temporary) / "secrets"
            generate(secrets)
            private_values.extend(json.loads((secrets / "db-passwords.json").read_text()).values())
            for server in ("bank", "agent"):
                service = config["postgres-" + server]
                image = service["image"]
                password = secrets / f"postgres-{server}-password"
                private_values.append(password.read_text().strip())
                source = volume(server + "-source")
                # Native Linux volume: foreign ownership is enforced even on macOS hosts.
                run(
                    [
                        "run",
                        "--rm",
                        "-i",
                        "--network",
                        "none",
                        "-v",
                        f"{source}:/bootstrap",
                        "--entrypoint",
                        "/bin/sh",
                        image,
                        "-ec",
                        "umask 077; cat > /bootstrap/roles.sql; "
                        "chown 12345:12345 /bootstrap/roles.sql; chmod 0600 /bootstrap/roles.sql",
                    ],
                    stdin=(secrets / f"init-{server}.sql").read_text(),
                )

                def common(name: str, data: str, password: Path = password) -> list[str]:
                    containers.append(name)
                    return [
                        "run",
                        "-d",
                        "--name",
                        name,
                        "--network",
                        "none",
                        "--label",
                        "safebank.acceptance=bootstrap",
                        "-v",
                        f"{data}:/var/lib/postgresql/data",
                        "--mount",
                        f"type=bind,src={password},dst=/run/secrets/password,readonly",
                        "-e",
                        "POSTGRES_PASSWORD_FILE=/run/secrets/password",
                    ]

                # Reproduce the original failure using a separate, disposable database.
                broken = prefix + "-" + server + "-before"
                run(
                    common(broken, volume(server + "-before"))
                    + [
                        "-v",
                        f"{source}:/docker-entrypoint-initdb.d:ro",
                        image,
                    ]
                )
                assert run(["wait", broken]) == "1"
                assert "roles.sql: Permission denied" in run(["logs", broken])
                print(f"PASS: {server} reproduces original 0600 foreign-owner read failure")

                name = prefix + "-" + server + "-after"
                args = common(name, volume(server + "-after"))
                for mount in service["tmpfs"]:
                    args.extend(["--tmpfs", mount])
                args.extend(
                    [
                        "-v",
                        f"{source}:/run/bootstrap:ro",
                        "--mount",
                        f"type=bind,src={ROOT / 'scripts/postgres-entrypoint.sh'},"
                        "dst=/usr/local/bin/safebank-postgres-entrypoint.sh,readonly",
                        "--health-cmd",
                        "pg_isready -U postgres",
                        "--health-interval",
                        "1s",
                        "--health-retries",
                        "20",
                        "--entrypoint",
                        service["entrypoint"][0],
                        image,
                        *service["entrypoint"][1:],
                        *service["command"],
                    ]
                )
                run(args)
                healthy = [
                    "exec",
                    name,
                    "/bin/sh",
                    "-ec",
                    # The temporary init server also accepts connections; wait for final PID 1.
                    'i=0; until [ "$(head -1 /proc/1/comm)" = postgres ]; do '
                    "i=$((i+1)); [ $i -lt 40 ]; sleep 0.25; done; "
                    "i=0; until pg_isready -U postgres >/dev/null 2>&1; do "
                    "i=$((i+1)); [ $i -lt 40 ]; sleep 0.25; done",
                ]
                run(healthy)
                sql = (
                    "SELECT has_database_privilege('bank_app','bank','CONNECT') "
                    "AND NOT has_database_privilege('bank_app','gateway','CONNECT') "
                    "AND has_database_privilege('gateway_app','gateway','CONNECT') "
                    "AND has_database_privilege('bank_ro','bank','CONNECT');"
                    if server == "bank"
                    else "SELECT has_database_privilege('agent_app','agent','CONNECT') "
                    "AND NOT has_database_privilege('agent_app','postgres','CONNECT');"
                )
                assert run(["exec", name, "psql", "-U", "postgres", "-tAc", sql]) == "t"
                mode = run(
                    [
                        "exec",
                        name,
                        "/bin/sh",
                        "-ec",
                        "[ $(stat -c %u /docker-entrypoint-initdb.d/01-roles.sql) "
                        "= $(id -u postgres) ]; "
                        "[ $(stat -c %a /docker-entrypoint-initdb.d) = 700 ]; "
                        "[ $(stat -c %a /run/bootstrap/roles.sql) = 600 ]; "
                        "stat -c %a /docker-entrypoint-initdb.d/01-roles.sql",
                    ]
                )
                assert mode == "600"
                run(
                    [
                        "exec",
                        name,
                        "psql",
                        "-U",
                        "postgres",
                        "-c",
                        "CREATE TABLE bootstrap_preserved(id integer PRIMARY KEY); "
                        "INSERT INTO bootstrap_preserved VALUES(1)",
                    ]
                )
                run(["restart", name])
                run(healthy)
                assert (
                    run(
                        [
                            "exec",
                            name,
                            "psql",
                            "-U",
                            "postgres",
                            "-tAc",
                            "SELECT count(*) FROM bootstrap_preserved",
                        ]
                    )
                    == "1"
                )
                assert run(["exec", name, "psql", "-U", "postgres", "-tAc", sql]) == "t"
                assert password.stat().st_mode & 0o777 == 0o600
                assert (secrets / f"init-{server}.sql").stat().st_mode & 0o777 == 0o600
                print(
                    f"PASS: {server} fresh init, role isolation, "
                    "private permissions and restart persistence"
                )
    finally:
        for name in reversed(containers):
            run(["rm", "-f", name], check=False)
        for name in reversed(volumes):
            run(["volume", "rm", name], check=False)
    print("PASS: disposable Linux bootstrap acceptance; existing application volumes untouched")


if __name__ == "__main__":
    main()
