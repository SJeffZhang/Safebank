"""Bare-date first_run_at is filled in with the gateway's 08:00 +08:00 anchor."""

from services.agent.app.llm.plan import _normalize_schedule_dates


def test_bare_date_first_run_gets_anchor_time():
    args = {"steps": [{"schedule_transfer": {"args": {"first_run_at": "2026-10-20"}}}]}
    out = _normalize_schedule_dates(args)
    assert out["steps"][0]["schedule_transfer"]["args"]["first_run_at"] == (
        "2026-10-20T08:00:00+08:00"
    )


def test_full_timestamp_and_other_fields_untouched():
    args = {
        "first_run_at": "2026-10-20T09:30:00+08:00",
        "end_at": "2026-12-31",
        "memo": "2026-10-20",
        "nested": ["__NULL__", {"first_run_at": "__NULL__"}],
    }
    assert _normalize_schedule_dates(args) == args
