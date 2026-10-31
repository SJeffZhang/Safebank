import json
import os
import time

import httpx

base = "http://" + os.environ["GATEWAY_PUBLIC_BIND"] + ":8200"
c = httpx.Client(timeout=30)
h = {"X-Service-Token": os.environ["DEMO_CONSOLE_TOKEN"]}
r = c.post(base + "/demo/auth/login", headers=h, json={"secret": os.environ["DEMO_CONSOLE_TOKEN"]})
r.raise_for_status()
h["Authorization"] = "Bearer " + r.json()["token"]
proof = []
for n, target in enumerate(
    ("2026-10-15T09:40:00+08:00", "2026-10-23T09:00:00+08:00", "2026-12-06T09:00:00+08:00")
):
    body = {"request_id": "req_clock_proof_" + str(n), "target_at": target}
    r = c.post(base + "/demo/clock/advance", headers=h, json=body)
    r.raise_for_status()
    job = r.json()["advance_job_id"]
    same = c.post(base + "/demo/clock/advance", headers=h, json=body)
    same.raise_for_status()
    assert same.json()["advance_job_id"] == job
    conflict = c.post(
        base + "/demo/clock/advance",
        headers=h,
        json={**body, "target_at": "2027-01-01T09:00:00+08:00"},
    )
    assert conflict.status_code == 409, conflict.status_code
    for _attempt in range(100):
        r = c.get(base + "/demo/clock/advances/" + job, headers=h)
        r.raise_for_status()
        state = r.json()
        if state["status"] == "DONE":
            break
        if state["status"] in ("FAILED", "RETRYING"):
            raise RuntimeError(str(state))
        time.sleep(0.5)
    assert state["status"] == "DONE" and state["remaining"] == 0, state
    proof.append({"id": job, **state})
past = c.post(
    base + "/demo/clock/advance",
    headers=h,
    json={"request_id": "req_clock_past_probe", "target_at": "2026-10-15T09:00:00+08:00"},
)
assert past.status_code == 422, past.status_code
print(json.dumps(proof))
