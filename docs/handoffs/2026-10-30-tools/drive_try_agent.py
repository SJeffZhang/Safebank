"""Drive `try-agent --confirm` non-interactively for a demo run.

Answers the PIN / confirm / risk-ack / OTP prompts. The PIN is read from the
demo_pins secret file; the OTP is fetched through D0+D5 inside the gateway
container. Neither value is printed. Usage:
    python drive_try_agent.py <deploy_dir> <pins_file> <customer_id> <text>
"""

from __future__ import annotations

import codecs
import json
import os
import re
import select
import subprocess
import sys
import time

OTP_FETCH = r"""
import json, os, sys, httpx
tok = os.environ["DEMO_CONSOLE_TOKEN"]
base = "http://" + os.environ.get("GW_PUBLIC", "172.29.10.12") + ":8200"
h = {"X-Service-Token": tok}
r = httpx.post(base + "/demo/auth/login", json={"secret": tok}, headers=h, timeout=10)
r.raise_for_status()
h["Authorization"] = "Bearer " + r.json()["token"]
r = httpx.get(base + "/demo/sms", params={"approval_id": sys.argv[1]}, headers=h, timeout=10)
r.raise_for_status()
sys.stdout.write(r.json()["code"])
"""


def fetch_otp(deploy: str, approval_id: str) -> str:
    out = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            "GW_PUBLIC=" + os.environ.get("GW_PUBLIC", "172.29.10.12"),
            "gateway",
            "python",
            "-c",
            OTP_FETCH,
            approval_id,
        ],
        cwd=deploy,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    code = out.stdout.strip()
    if not re.fullmatch(r"\d{6}", code):
        raise RuntimeError("D5 did not return a 6-digit code")
    return code


_QUOTA = re.compile(r"(?:HTTP\s+|Error code:\s*|status(?:_code)?[\"'\s:=]+)(402|403|429)\b", re.I)


def quota_status(text: str) -> int | None:
    match = _QUOTA.search(text)
    return int(match[1]) if match else None


def stop_child(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def main() -> int:
    deploy, pins_file, customer, text = sys.argv[1:5]
    # Negative-case mode: ok | reject | wrong_otp | expired_otp | restart_agent
    mode = sys.argv[5] if len(sys.argv) > 5 else "ok"
    with open(pins_file) as f:
        pin = json.load(f)[customer]
    timeout = float(os.environ.get("TRY_AGENT_TIMEOUT_SECONDS", "300"))
    if timeout <= 0:
        raise ValueError("TRY_AGENT_TIMEOUT_SECONDS must be positive")
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            "PYTHONUNBUFFERED=1",
            "operator",
            "python",
            "-m",
            "scripts.try_agent",
            "--customer-id",
            customer,
            "--text",
            text,
            "--confirm",
        ]
        + (["--out", os.environ["TRY_AGENT_OUT"]] if os.environ.get("TRY_AGENT_OUT") else []),
        cwd=deploy,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    buf = ""
    approval_id = None
    answered = set()

    def send(line: str) -> None:
        proc.stdin.write((line + "\n").encode())
        proc.stdin.flush()

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([proc.stdout], [], [], max(0, remaining))[0]:
            stop_child(proc)
            print("\n[driver] TIMEOUT; stop batch, inspect in-flight plan before retry")
            return 124
        ch = proc.stdout.read(1)
        if not ch:
            break
        c = decoder.decode(ch)
        if not c:
            continue
        sys.stdout.write(c)
        sys.stdout.flush()
        buf += c
        if status := quota_status(buf[-2000:]):
            stop_child(proc)
            print(f"\n[driver] STOP_UPSTREAM_{status}; no retry")
            return 75
        m = re.findall(r"apv_[A-Za-z0-9]+", buf)
        if m:
            approval_id = m[-1]
        if buf.endswith(f"PIN for {customer}: ") and "pin" not in answered:
            answered.add("pin")
            send(pin)
        elif buf.endswith("Type 'yes' to confirm: ") and "confirm" not in answered:
            answered.add("confirm")
            if mode == "restart_agent":
                print("\n[driver] restarting agent container before confirm", flush=True)
                subprocess.run(
                    ["docker", "compose", "restart", "agent"],
                    cwd=deploy,
                    check=True,
                    capture_output=True,
                    timeout=min(30, max(0.1, deadline - time.monotonic())),
                )
                for _ in range(60):
                    h = subprocess.run(
                        ["docker", "compose", "ps", "agent", "--format", "{{.Health}}"],
                        cwd=deploy,
                        capture_output=True,
                        text=True,
                        timeout=min(5, max(0.1, deadline - time.monotonic())),
                    ).stdout.strip()
                    if h == "healthy":
                        break
                    time.sleep(2)
                print(f"\n[driver] agent health after restart: {h}", flush=True)
            send("no" if mode == "reject" else "yes")
        elif buf.endswith("Risk acknowledged? Type 'yes': ") and "risk" not in answered:
            answered.add("risk")
            send("yes")
        elif buf.endswith("OTP: ") and "otp" not in answered:
            answered.add("otp")
            if not approval_id:
                print("\n[driver] no approval_id seen before OTP prompt", file=sys.stderr)
                send("")
            else:
                code = fetch_otp(deploy, approval_id)
                if mode == "wrong_otp":
                    code = f"{(int(code) + 1) % 1000000:06d}"
                    print("\n[driver] submitting a deliberately wrong OTP", flush=True)
                elif mode == "expired_otp":
                    print("\n[driver] waiting 130s so the OTP expires", flush=True)
                    time.sleep(130)
                send(code)
                print("\n[driver] OTP submitted (not shown)", flush=True)
    proc.wait()
    print(f"\n[driver] try-agent exit={proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
