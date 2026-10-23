"""Isolated Linux permissions acceptance; synthetic secrets, tmpfs, no data volumes.

Builds only an independently tagged image from the existing local runtime image;
never replaces safebank-p0:local or changes the running compose stack.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSPECT = r"""
import json, os, stat
from pathlib import Path
assert os.getuid() == os.getgid() == 10001
assert os.getgroups() == []
base = Path('/run/gateway-private')
assert stat.S_IMODE(base.stat().st_mode) == 0o700 and base.stat().st_uid == 10001
for filename, value in [('jwt-private.pem', b'synthetic-private-material'),
                        ('demo_pins', b'{"cus_zs":"123456"}')]:
    original = Path('/run/bootstrap') / filename
    assert stat.S_IMODE(original.stat().st_mode) == 0o600
    assert original.stat().st_uid == 20002
    try:
        original.read_bytes()
    except PermissionError:
        pass
    else:
        raise AssertionError('runtime user can still read foreign source')
    target = base / filename
    assert stat.S_IMODE(target.stat().st_mode) == 0o600 and target.stat().st_uid == 10001
    assert target.read_bytes() == value
assert os.environ['JWT_PRIVATE_KEY_PATH'] == str(base / 'jwt-private.pem')
assert os.environ['DEMO_PINS_FILE'] == str(base / 'demo_pins')
status = dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines()
              if ':' in line)
assert int(status['CapEff'].strip(),16) == int(status['CapPrm'].strip(),16) == 0
assert status['NoNewPrivs'].strip() == '1'
print(json.dumps({'uid':os.getuid(), 'gid':os.getgid(), 'directory_mode':'0700',
                  'file_mode':'0600', 'effective_caps':0, 'no_new_privs':True}))
"""

SETUP = r"""
import os, subprocess, sys
from pathlib import Path
scenario = sys.argv[1]
base = Path('/run/bootstrap')
for filename, value in [('jwt-private.pem', b'synthetic-private-material'),
                        ('demo_pins', b'{"cus_zs":"123456"}')]:
    path = base / filename
    path.write_bytes(value)
    path.chmod(0o600)
    os.chown(path,20002,20002)
# Kernel reproduction of old behavior: uid10001 cannot read a foreign-owned 0600 key.
probe = subprocess.run([sys.executable,'-c',
    "from pathlib import Path; Path('/run/bootstrap/jwt-private.pem').read_bytes()"],
    user=10001, group=10001, extra_groups=[], capture_output=True)
assert probe.returncode != 0 and b'PermissionError' in probe.stderr
print('original_nonroot_read: denied',flush=True)
if scenario == 'missing_key':
    (base/'jwt-private.pem').unlink()
elif scenario == 'missing_pins':
    (base/'demo_pins').unlink()
elif scenario == 'unsafe_mode':
    os.chown(base/'jwt-private.pem',0,0)
    (base/'jwt-private.pem').chmod(0o644)
    os.chown(base/'jwt-private.pem',20002,20002)
elif scenario == 'symlink':
    (base/'jwt-private.pem').rename(base/'target')
    (base/'jwt-private.pem').symlink_to(base/'target')
elif scenario == 'nonroot_bootstrap':
    os.setgroups([]); os.setgid(10001); os.setuid(10001)
os.execv(sys.executable,[sys.executable,'/opt/gateway_entrypoint.py',sys.executable,'-c',sys.argv[2]])
"""


def run() -> None:
    tag = f"safebank-s1-permissions:{os.getpid()}-{secrets.token_hex(4)}"
    image_built = False
    with tempfile.TemporaryDirectory(prefix="sbg-s1-permissions-") as directory:
        context = Path(directory)
        (context / "gateway_entrypoint.py").write_bytes(
            (ROOT / "scripts/gateway_entrypoint.py").read_bytes()
        )
        (context / "Dockerfile").write_text(
            "FROM safebank-p0:local\nCOPY gateway_entrypoint.py /opt/gateway_entrypoint.py\n"
        )
        try:
            built = subprocess.run(
                ["docker", "build", "--pull=false", "--network=none", "-t", tag, str(context)],
                capture_output=True,
                text=True,
            )
            if built.returncode:
                raise RuntimeError(f"isolated permissions image build failed: {built.stderr}")
            image_built = True
            for scenario in (
                "ok",
                "missing_key",
                "missing_pins",
                "unsafe_mode",
                "symlink",
                "nonroot_bootstrap",
                "missing_tmpfs",
            ):
                args = [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--user",
                    "0:0",
                    "--cap-drop",
                    "ALL",
                ]
                for cap in ("CHOWN", "DAC_OVERRIDE", "SETUID", "SETGID"):
                    args += ["--cap-add", cap]
                args += [
                    "--security-opt",
                    "no-new-privileges:true",
                    "--tmpfs",
                    "/run/bootstrap:rw,noexec,nosuid,nodev,mode=0755,size=1m",
                ]
                if scenario != "missing_tmpfs":
                    args += [
                        "--tmpfs",
                        "/run/gateway-private:rw,noexec,nosuid,nodev,mode=0700,size=1m",
                    ]
                args += ["--entrypoint", "python", tag, "-c", SETUP, scenario, INSPECT]
                result = subprocess.run(args, capture_output=True, text=True)
                combined = result.stdout + result.stderr
                if "synthetic-private-material" in combined or '"123456"' in combined:
                    raise AssertionError("secret appeared in container output")
                if "original_nonroot_read: denied" not in result.stdout:
                    raise AssertionError(f"old permission failure was not reproduced: {scenario}")
                if scenario == "ok":
                    if result.returncode:
                        raise AssertionError(f"bootstrap failed: {result.stderr}")
                    evidence = json.loads(result.stdout.splitlines()[-1])
                    assert evidence["uid"] == 10001 and evidence["effective_caps"] == 0
                elif result.returncode == 0 or "gateway secret bootstrap refused" not in combined:
                    raise AssertionError(f"unsafe bootstrap did not refuse: {scenario}")
                print(f"Linux secret acceptance {scenario}: passed")
        finally:
            if image_built:
                subprocess.run(["docker", "image", "rm", tag], capture_output=True, check=True)


if __name__ == "__main__":
    run()
