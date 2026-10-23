"""Gateway database bootstrap (mirrors ``scripts/p1_database.py``).

Separates the gateway database owner (postgres) from the runtime role
(``gateway_app``), creates the reset-only ``gateway_admin`` role, and writes
``.secrets/gateway-migration.env`` for the maintenance migration step. This
must run before ``migrate-gateway``.
"""

import subprocess
from pathlib import Path


def provision() -> None:
    sql = """ALTER DATABASE gateway OWNER TO postgres;
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='gateway_admin') THEN
            CREATE ROLE gateway_admin;
          END IF;
        END $$;
        REVOKE ALL ON DATABASE gateway FROM PUBLIC;
        GRANT CONNECT ON DATABASE gateway TO gateway_app, gateway_admin;
    """
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres-bank",
            "psql",
            "-U",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        text=True,
        check=True,
    )
    admin = Path(".secrets/postgres-bank-password").read_text().strip()
    Path(".secrets/gateway-migration.env").write_text(
        f"GATEWAY_MIGRATION_DATABASE_URL=postgresql://postgres:{admin}@postgres-bank/gateway\n"
    )


if __name__ == "__main__":
    provision()
