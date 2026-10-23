"""Healthcheck processes do not inherit PID1's setuid: drop their privileges too."""

from __future__ import annotations

import os
import urllib.request


def main() -> None:
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    if os.getuid() != 10001 or os.getgid() != 10001 or os.getgroups():
        raise SystemExit("gateway healthcheck requires unprivileged runtime user")
    host = os.environ.get("GATEWAY_PUBLIC_BIND", "127.0.0.1")
    if ":" in host:
        host = f"[{host}]"
    with urllib.request.urlopen(f"http://{host}:8200/health", timeout=3) as response:
        if response.status != 200:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
