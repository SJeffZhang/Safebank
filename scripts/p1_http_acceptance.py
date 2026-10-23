"""Local operator launches trusted network acceptance, then restarts only L3."""

import subprocess


def main() -> None:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "gateway",
        "python",
        "-m",
        "scripts.p1_http_driver",
    ]
    subprocess.run(command, check=True)
    subprocess.run(["docker", "compose", "restart", "--no-deps", "bank-backend"], check=True)
    subprocess.run(
        ["docker", "compose", "up", "-d", "--no-build", "--wait", "bank-backend"], check=True
    )
    subprocess.run([*command, "--recover"], check=True)


if __name__ == "__main__":
    main()
