"""Deployment wiring for the L3 scheduler → gateway S1 callback."""

from pathlib import Path

import yaml
from app import main

ROOT = Path(__file__).resolve().parents[2]


def test_default_s1_url_targets_gateway_internal_listener():
    # S1 exists only on the gateway internal app (bank-net :8201); :8200 never
    # registers /internal/* and would 404 every scheduled callback.
    assert main.DEFAULT_S1_URL == "http://gateway:8201"


def test_compose_enables_scheduler_and_points_at_s1():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    env = compose["services"]["bank-backend"]["environment"]
    assert env["SCHEDULER_ENABLED"] == "true"
    assert env["S1_URL"] == "http://gateway:8201"
