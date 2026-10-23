"""Trusted local admin bootstrap for isolated P1 tests and migration environment."""

import json
import os
import subprocess
import sys
from pathlib import Path


def provision(*, fresh_test: bool = False) -> None:
    sql = """SELECT 'CREATE DATABASE bank_p1_test OWNER postgres' WHERE NOT EXISTS
        (SELECT FROM pg_database WHERE datname='bank_p1_test')\n\\gexec
        REVOKE ALL ON DATABASE bank_p1_test FROM PUBLIC;
        GRANT CONNECT ON DATABASE bank_p1_test TO bank_app,bank_ro;
        ALTER DATABASE bank OWNER TO postgres;
        GRANT CONNECT ON DATABASE bank TO bank_app,bank_ro;
    """
    if fresh_test:
        sql = "DROP DATABASE IF EXISTS bank_p1_test WITH (FORCE);\n" + sql
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
    passwords = json.loads(Path(".secrets/db-passwords.json").read_text())
    admin = Path(".secrets/postgres-bank-password").read_text().strip()

    def write(name, values):
        file = Path(".secrets") / name
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in values.items()))

    write(
        "p1-test.env",
        {
            "BANK_MIGRATION_DATABASE_URL": f"postgresql://postgres:{admin}@postgres-bank/bank_p1_test",
            "P1_TEST_DATABASE_URL": f"postgresql://bank_app:{passwords['bank_app']}@postgres-bank/bank_p1_test",
        },
    )
    write(
        "bank-migration.env",
        {
            "BANK_MIGRATION_DATABASE_URL": f"postgresql://postgres:{admin}@postgres-bank/bank",
        },
    )


if __name__ == "__main__":
    provision(fresh_test="--fresh-test" in sys.argv)
