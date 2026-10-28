"""Read foreign-owned 0600 gateway secrets once, then permanently drop root.

Only the short bootstrap runs as root. The application inherits no effective
capabilities, no supplementary groups, and no-new-privileges. Source mounts stay
read-only and 0600; only the gateway user owns the private tmpfs copies.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

SOURCE = Path("/run/bootstrap")
PRIVATE = Path("/run/gateway-private")
UID = GID = 10001


def _tmpfs_required() -> None:
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, separator, right = line.partition(" - ")
        if separator and left.split()[4] == str(PRIVATE) and right.split()[0] == "tmpfs":
            return
    raise ValueError("private secret directory requires tmpfs")


def _copy_secret(filename: str) -> None:
    fd = os.open(SOURCE / filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("unsafe secret source")
        data = source.read(65537)
        if not data or len(data) > 65536:
            raise ValueError("empty or oversized secret")
    fd = os.open(PRIVATE / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(data)
        os.fchown(target.fileno(), UID, GID)


def main() -> None:
    try:
        if os.geteuid() != 0:
            raise ValueError("bootstrap requires root")
        _tmpfs_required()
        info = PRIVATE.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("unsafe private directory")
        os.umask(0o077)
        for filename in ("jwt-private.pem", "demo_pins"):
            _copy_secret(filename)
        os.chown(PRIVATE, UID, GID)
        os.environ["JWT_PRIVATE_KEY_PATH"] = str(PRIVATE / "jwt-private.pem")
        os.environ["DEMO_PINS_FILE"] = str(PRIVATE / "demo_pins")
        # HOME stays /root (0700) after the privilege drop; asyncpg probes
        # ~/.postgresql/postgresql.key on connect and fails with PermissionError.
        os.environ["HOME"] = str(PRIVATE)
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)
        command = sys.argv[1:] or [sys.executable, "-m", "services.gateway.app.main"]
        os.execvp(command[0], command)
    except (OSError, ValueError):
        # Do not print exception reprs: secret paths/configuration can appear in
        # low-level exceptions; contents and PIN values must never reach logs.
        print("gateway secret bootstrap refused", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
