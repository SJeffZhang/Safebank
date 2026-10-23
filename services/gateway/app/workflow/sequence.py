"""Order-independent dangerous-sequence policy, PRD §7.8."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from safebank_contracts.state import Operation

POLICY_DIR = Path(__file__).resolve().parents[2] / "policies"
THRESHOLDS = yaml.safe_load((POLICY_DIR / "thresholds.yaml").read_text())
WORKFLOW = yaml.safe_load((POLICY_DIR / "workflow.yaml").read_text())


def risky_sequence(events: Iterable[Operation], target: str, now: datetime) -> bool:
    """Caller supplies only this customer's committed/prior planned operations."""
    events = tuple(
        e
        for e in events
        if now - timedelta(hours=THRESHOLDS["rolling_hours"]) <= e.occurred_at <= now
    )
    rule = WORKFLOW["risky_sequence"]
    return any(
        e.action_kind == rule["prerequisite"] and e.target_fingerprint == target for e in events
    ) and any(e.action_kind in rule["loosen_actions"] for e in events)
