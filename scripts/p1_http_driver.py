"""Trusted gateway-side P1 acceptance client; never bound as an L1 endpoint."""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from safebank_bankcore.ids import digest
from safebank_contracts.state import BankState

BASE = "http://bank-backend:8300"
SAVED = Path("/tmp/p1-http-command.json")


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=10, trust_env=False) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/health", headers={"X-Service-Token": "invalid"}).status_code == 401
        client.headers["X-Service-Token"] = os.environ["GATEWAY_SERVICE_TOKEN"]
        assert client.get("/readyz").json() == {"ready": True}
        if "--recover" in sys.argv:
            execute = json.loads(SAVED.read_text())
            query = client.get(
                f"/internal/executions/{execute['plan_id']}", params={"customer_id": "cus_a"}
            )
            assert query.json()["status"] == "COMMITTED", query.text
            replay = client.post("/internal/plans/execute", json=execute)
            assert replay.json()["replayed"] is True, replay.text
            print("PASS: actual L3 process restart recovers committed result and idempotent replay")
            return
        steps = [
            dict(
                step_id="s1",
                tool="transfer",
                depends_on=[],
                arg_sources=[],
                args=dict(
                    from_account_id="acc_a",
                    to_account_id="acc_s",
                    payee_id=None,
                    amount_cents=1,
                    memo="P1 HTTP acceptance",
                ),
            )
        ]
        snapshot = client.post(
            "/internal/snapshot", json=dict(customer_id="cus_a", steps=steps, event_id=None)
        )
        assert snapshot.status_code == 200, snapshot.text
        state = BankState.model_validate(snapshot.json())
        plan_id = "pln_http_" + uuid4().hex
        plan_hash = digest(steps)
        now = datetime.now(UTC)
        evidence = dict(
            type="INTERACTIVE",
            origin="agent",
            scope_ids=["utt_p1_fixture"],
            utterance_ids=["utt_p1_fixture"],
            evaluation_id="evl_p1_fixture",
            approved_evaluation_id="evl_p1_fixture",
            plan_hash=plan_hash,
            approval_id="apv_p1_fixture",
            confirmed_view_hash="p1_fixture",
            final_view_hash="p1_fixture",
            risk="YELLOW",
            confirmed_at_wall=now.isoformat(),
            authorization_epoch=0,
        )
        prepare = dict(
            customer_id="cus_a",
            plan_id=plan_id,
            attempt=1,
            plan_hash=plan_hash,
            steps=steps,
            deadline_wall=(now + timedelta(minutes=5)).isoformat(),
            deadline_cap_wall=(now + timedelta(minutes=10)).isoformat(),
            authorization_evidence=evidence,
        )
        response = client.post("/internal/plans/prepare", json=prepare)
        assert response.status_code == 200, response.text
        execute = {
            k: v for k, v in prepare.items() if k not in ("deadline_wall", "deadline_cap_wall")
        }
        execute.update(expected_versions=dict(state.versions), clock_version=state.clock_version)
        result = client.post("/internal/plans/execute", json=execute)
        assert result.status_code == 200 and result.json()["status"] == "EXECUTED", result.text
        SAVED.write_text(json.dumps(execute))
        accounts = client.post(
            "/internal/read/get_accounts", json=dict(customer_id="cus_a", args=dict(history_days=0))
        )
        actual = {a["id"]: a for a in accounts.json()["accounts"]}
        assert actual["acc_a"]["available_cents"] == state.accounts["acc_a"].available_cents - 1
        assert actual["acc_s"]["available_cents"] == state.accounts["acc_s"].available_cents + 1
        # The outbox pages unacknowledged events oldest first; on a long-lived
        # database this run's event may sit beyond the first page.
        found, after = False, 0
        while not found:
            page = client.get("/internal/outbox", params={"after": after, "limit": 100}).json()
            found = any(e["payload"]["plan_id"] == plan_id for e in page["items"])
            if page["next_cursor"] is None:
                break
            after = int(page["next_cursor"])
        assert found
        print(
            "PASS: actual HTTP -> PostgreSQL, service auth, snapshot, prepare, transfer and outbox"
        )


if __name__ == "__main__":
    main()
