"""Execute real assertions inside running Compose services, without printing env."""

import subprocess


def inside(service: str, code: str) -> None:
    subprocess.run(["docker", "compose", "exec", "-T", service, "python", "-c", code], check=True)


def main() -> None:
    inside("agent", "import socket; s=socket.create_connection(('gateway',8200),3); s.close()")
    for host, port in (
        ("bank-backend", 8300),
        ("postgres-bank", 5432),
        ("172.29.20.12", 8201),
        ("172.29.10.12", 8201),
    ):
        inside(
            "agent",
            f"""
import socket
try:
    socket.create_connection(({host!r},{port}),2)
except OSError:
    pass
else:
    raise AssertionError('agent crossed bank boundary')
""",
        )
    inside(
        "gateway", "import socket; s=socket.create_connection(('bank-backend',8300),3); s.close()"
    )
    inside(
        "agent",
        "import os; assert not any(k in os.environ for k in ['GATEWAY_SERVICE_TOKEN','FRONTEND_SERVICE_TOKEN','SCHEDULER_SERVICE_TOKEN','DEMO_CONSOLE_TOKEN','BANK_DATABASE_URL','JWT_PRIVATE_KEY'])",  # noqa: E501
    )
    inside(
        "gateway",
        "import urllib.request,os; r=urllib.request.Request('http://bank-backend:8300/health',headers={'X-Service-Token':os.environ['GATEWAY_SERVICE_TOKEN']}); assert urllib.request.urlopen(r).status==200",  # noqa: E501
    )
    # Each application DB user can reach only its own database.
    for server, owner, db, forbidden in (
        ("bank", "bank_app", "bank", "gateway"),
        ("bank", "gateway_app", "gateway", "bank"),
        ("agent", "agent_app", "agent", "postgres"),
    ):
        command = [
            "docker",
            "compose",
            "exec",
            "-T",
            f"postgres-{server}",
            "psql",
            "-U",
            "postgres",
            "-tAc",
            f"SELECT has_database_privilege('{owner}','{db}','CONNECT') AND NOT has_database_privilege('{owner}','{forbidden}','CONNECT')",  # noqa: E501
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        assert result.stdout.strip() == "t", (owner, "database isolation failed")
    print("PASS: real network, credential and database role boundaries")


if __name__ == "__main__":
    main()
