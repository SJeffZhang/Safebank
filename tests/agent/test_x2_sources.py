"""X-2: derived inputs reference cancel steps and amount fields; midnight anchors at 08:00."""

from services.agent.app.llm.plan import _fix_cancelled_fee_inputs, _normalize_schedule_dates


def _plan(inputs, depends_on=("cancel_cloud",)):
    return {
        "steps": [
            {"step_id": "cancel_cloud", "tool": "cancel_subscription", "args": {}},
            {"step_id": "cancel_gym", "tool": "cancel_subscription", "args": {}},
            {
                "step_id": "save",
                "tool": "schedule_transfer",
                "args": {"amount_cents": 22000},
                "depends_on": list(depends_on),
                "arg_sources": [
                    {
                        "param": "amount_cents",
                        "source": {
                            "kind": "derived",
                            "template": "cancelled_monthly_fees",
                            "inputs": inputs,
                        },
                    }
                ],
            },
        ]
    }


def _read(pointer):
    return {"kind": "read", "ref": "src_sub", "pointer": pointer}


def test_whole_item_reads_point_at_amount_and_cancel_steps_are_added():
    plan = _fix_cancelled_fee_inputs(_plan([_read("/items/0"), _read("/items/1")]))
    save = plan["steps"][2]
    inputs = save["arg_sources"][0]["source"]["inputs"]
    assert inputs == [
        {"kind": "step", "ref": "cancel_cloud"},
        {"kind": "step", "ref": "cancel_gym"},
        _read("/items/0/amount_cents"),
        _read("/items/1/amount_cents"),
    ]
    assert save["depends_on"] == ["cancel_cloud", "cancel_gym"]


def test_correct_inputs_are_kept_without_duplicates():
    good = [
        {"kind": "step", "ref": "cancel_cloud"},
        {"kind": "step", "ref": "cancel_gym"},
        _read("/items/0/amount_cents"),
    ]
    plan = _fix_cancelled_fee_inputs(_plan(list(good)))
    assert plan["steps"][2]["arg_sources"][0]["source"]["inputs"] == good


def test_other_pointers_and_templates_are_left_alone():
    plan = _plan([_read("/items/0/merchant_name")])
    out = _fix_cancelled_fee_inputs(plan)
    assert _read("/items/0/merchant_name") in out["steps"][2]["arg_sources"][0]["source"]["inputs"]
    other = _plan([_read("/items/0")])
    other["steps"][2]["arg_sources"][0]["source"]["template"] = "available_less_7d_charges"
    out = _fix_cancelled_fee_inputs(other)
    assert out["steps"][2]["arg_sources"][0]["source"]["inputs"] == [_read("/items/0")]


def test_midnight_beijing_first_run_moves_to_anchor():
    assert _normalize_schedule_dates({"first_run_at": "2026-11-01T00:00:00+08:00"}) == {
        "first_run_at": "2026-11-01T08:00:00+08:00"
    }
    assert _normalize_schedule_dates({"first_run_at": "2026-11-01T00:00:00"}) == {
        "first_run_at": "2026-11-01T08:00:00+08:00"
    }


def test_other_times_are_unchanged():
    for value in ("2026-11-01T09:30:00+08:00", "2026-11-01T00:00:00Z"):
        assert _normalize_schedule_dates({"first_run_at": value}) == {"first_run_at": value}
