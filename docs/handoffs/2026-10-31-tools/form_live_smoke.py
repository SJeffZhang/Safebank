import json
import os
import sys
import uuid

import httpx

pin = sys.stdin.read().strip()
c = httpx.Client(base_url="http://gateway:8200", timeout=30)
h = {"X-Service-Token": os.environ["FRONTEND_SERVICE_TOKEN"]}
r = c.post("/auth/login", headers=h, json={"customer_id": "cus_zs", "pin": pin})
r.raise_for_status()
h["Authorization"] = "Bearer " + r.json()["app_token"]
del pin
prefix = "req_form_live_" + uuid.uuid4().hex[:12]


def post(path, body):
    r = c.post(path, headers=h, json=body)
    r.raise_for_status()
    return r.json()


sid = post("/sessions", {"request_id": prefix + "_session"})["session_id"]
proof = []
for n, (kind, args, risk) in enumerate(
    (
        (
            "TRANSFER_EXTERNAL",
            {
                "from_account_id": "acc_zs_chk",
                "payee_id": "pye_zs_zl",
                "to_account_id": None,
                "amount_cents": 20000,
                "memo": None,
            },
            "YELLOW",
        ),
        ("CARD_FREEZE", {"card_id": "crd_zs_1"}, "GREEN"),
    )
):
    action = post(
        "/sessions/" + sid + "/actions",
        {
            "request_id": prefix + "_action" + str(n),
            "kind": "FORM",
            "action_kind": kind,
            "fields": args,
        },
    )["action_id"]
    body = {
        "request_id": prefix + "_evaluate" + str(n),
        "session_id": sid,
        "form_action_ids": [action],
    }
    result = post("/plans/evaluate", body)
    assert result["risk"] == risk, result
    assert post("/plans/evaluate", body) == result
    if risk == "YELLOW":
        assert result["decision"] == "REQUIRE_APPROVAL", result
        aid = result["approval_id"]
        r = c.get("/approvals/" + aid, headers=h)
        r.raise_for_status()
        panel = r.json()
        assert not panel["otp_required"], panel
        post(
            "/approvals/" + aid + "/confirm",
            {
                "request_id": prefix + "_confirm" + str(n),
                "view_hash": panel["view_hash"],
                "risk_ack": panel["risk_ack_required"],
            },
        )
    else:
        assert result["decision"] == "ALLOW", result
    pid = result["plan_id"]
    body = {"request_id": prefix + "_execute" + str(n)}
    executed = post("/plans/" + pid + "/execute", body)
    assert executed["status"] == "EXECUTED", executed
    replay = post("/plans/" + pid + "/execute", body)
    assert replay["status"] == "EXECUTED"
    assert {k: v for k, v in executed.items() if k != "replayed"} == {
        k: v for k, v in replay.items() if k != "replayed"
    }
    proof.append(
        {
            "kind": kind,
            "action_id": action,
            "plan_id": pid,
            "risk": risk,
            "status": executed["status"],
        }
    )
print(json.dumps(proof))
