"""End-to-end plan fixes: three defects detected in live three-layer testing.

Defect A: schedule intent → model uses 'transfer' tool (should be schedule_transfer)
Defect B: create_split_request with empty participant_payee_ids
Defect C: cancel_subscription + schedule_transfer with literal amount (should use derived)
"""

from __future__ import annotations

from services.agent.app.graph.state import AgentContext, ReadRecord, Understanding, UtteranceRef
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.plan import PlanAdapter
from tests.agent.llm_mock import ScriptedLLM, tool_response

API_KEY = "test-key"


def ctx() -> AgentContext:
    return AgentContext(assistant_token="SECRET_TOKEN", customer_id="cus_zs", session_id="ses_a")


def build(scripted: ScriptedLLM):
    return make_llm(api_key=API_KEY, http_client=scripted.client)


# ── helpers ────────────────────────────────────────────────────────────


def _schedule_state():
    """State where user wants a monthly recurring transfer."""
    return {
        "customer_id": "cus_zs",
        "session_id": "ses_a",
        "utterances": [UtteranceRef(id="utt_a", kind="typed", text="每月20号给赵六转200元")],
        # The model's intent labels are free-form; detection must not rely on them.
        "understanding": Understanding(intents=["transfer"], needs_write=True),
        "reads": [
            ReadRecord(
                source_ref="src_acct",
                tool="get_accounts",
                data={"accounts": [{"account_id": "acc_zs_chk"}]},
            ),
            ReadRecord(
                source_ref="src_payee",
                tool="list_payees",
                data={"items": [{"id": "pye_zs_zl", "name": "赵六"}], "next_cursor": None},
            ),
        ],
    }


def _split_state():
    """State where user wants to split a bill among 3 friends."""
    return {
        "customer_id": "cus_zs",
        "session_id": "ses_a",
        "utterances": [
            UtteranceRef(id="utt_a", kind="typed", text="昨晚聚餐400元，我和小林、小周、小陈AA")
        ],
        "understanding": Understanding(intents=["split"], needs_write=True),
        "reads": [
            ReadRecord(
                source_ref="src_acct",
                tool="get_accounts",
                data={"accounts": [{"account_id": "acc_zs_chk"}]},
            ),
            ReadRecord(
                source_ref="src_payee",
                tool="list_payees",
                data={
                    "items": [
                        {"id": "pye_zs_f1", "name": "小林"},
                        {"id": "pye_zs_f2", "name": "小周"},
                        {"id": "pye_zs_f3", "name": "小陈"},
                    ],
                    "next_cursor": None,
                },
            ),
        ],
    }


def _subscription_schedule_state():
    """State: user wants to cancel unused subscriptions and auto-save the saved money."""
    return {
        "customer_id": "cus_zs",
        "session_id": "ses_a",
        "utterances": [
            UtteranceRef(
                id="utt_a",
                kind="typed",
                text="找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户",
            )
        ],
        "understanding": Understanding(intents=["subscription", "schedule"], needs_write=True),
        "reads": [
            ReadRecord(
                source_ref="src_acct",
                tool="get_accounts",
                data={
                    "accounts": [
                        {"account_id": "acc_zs_chk", "balance_cents": 600000},
                        {"account_id": "acc_zs_sav", "balance_cents": 200000},
                    ]
                },
            ),
            ReadRecord(
                source_ref="src_sub",
                tool="detect_subscriptions",
                data={
                    "items": [
                        {
                            "instruction_id": "si_zs_gym",
                            "is_idle": True,
                            "merchant_name": "阳光健身",
                            "amount_cents": 19900,
                            "period": "MONTHLY",
                        }
                    ]
                },
            ),
        ],
    }


# ── plan dicts ────────────────────────────────────────────────────────


def _bad_schedule_as_transfer():
    """Model generates a one-time transfer for what should be a schedule_transfer."""
    return {
        "intents": ["schedule"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "transfer",
                "args": {
                    "from_account_id": "acc_zs_chk",
                    "payee_id": {"kind": "id", "id": "pye_zs_zl"},
                    "to_account_id": {"kind": "none"},
                    "amount_cents": 20000,
                    "memo": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        "rationale": "每月20号转200给赵六",
    }


def _good_schedule_transfer():
    """Correct: schedule_transfer tool for recurring transfer."""
    return {
        "intents": ["schedule"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "schedule_transfer",
                "args": {
                    "from_account_id": "acc_zs_chk",
                    "payee_id": {"kind": "id", "id": "pye_zs_zl"},
                    "to_account_id": {"kind": "none"},
                    "amount_cents": 20000,
                    "period": "MONTHLY",
                    "first_run_at": "2026-11-20T08:00:00+08:00",
                    "end_at": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        "rationale": "每月20号转200给赵六",
    }


def _bad_split_wrong_payees():
    """Model generates create_split_request with payee IDs not in list_payees data."""
    return {
        "intents": ["split"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "create_split_request",
                "args": {
                    "account_id": "acc_zs_chk",
                    "total_cents": 40000,
                    "participant_payee_ids": ["pye_zs_unknown"],
                    "include_self": True,
                    "memo": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        "rationale": "AA聚餐",
    }


def _good_split():
    """Correct: create_split_request with payee IDs from list_payees."""
    return {
        "intents": ["split"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "create_split_request",
                "args": {
                    "account_id": "acc_zs_chk",
                    "total_cents": 40000,
                    "participant_payee_ids": ["pye_zs_f1", "pye_zs_f2", "pye_zs_f3"],
                    "include_self": True,
                    "memo": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "account_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_acct",
                            "pointer": "/accounts/0/account_id",
                        },
                    },
                    {
                        "param": "total_cents",
                        "source": {"kind": "utterance", "ref": "utt_a", "span": [4, 8]},
                    },
                    {
                        "param": "participant_payee_ids",
                        "source": {"kind": "utterance", "ref": "utt_a", "span": [11, 19]},
                    },
                    {
                        "param": "include_self",
                        "source": {"kind": "utterance", "ref": "utt_a", "span": [9, 11]},
                    },
                ],
            }
        ],
        "rationale": "AA聚餐",
    }


def _bad_cancel_sub_literal_amount():
    """Model uses literal utterance amount for schedule_transfer when cancelling subs."""
    return {
        "intents": ["subscription", "schedule"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "cancel_subscription",
                "args": {"instruction_id": "si_zs_gym"},
                "depends_on": [],
                "arg_sources": [],
            },
            {
                "step_id": "s2",
                "tool": "schedule_transfer",
                "args": {
                    "from_account_id": "acc_zs_chk",
                    "payee_id": {"kind": "none"},
                    "to_account_id": {"kind": "id", "id": "acc_zs_sav"},
                    "amount_cents": 19900,
                    "period": "MONTHLY",
                    "first_run_at": "2026-11-01T08:00:00+08:00",
                    "end_at": "__NULL__",
                },
                "depends_on": ["s1"],
                "arg_sources": [
                    {
                        "param": "amount_cents",
                        "source": {"kind": "utterance", "ref": "utt_a", "span": [15, 25]},
                    }
                ],
            },
        ],
        "rationale": "取消没用订阅并每月存省下来的钱",
    }


def _good_cancel_sub_derived_amount():
    """Correct: schedule_transfer uses derived source for cancelled subscription fees."""
    return {
        "intents": ["subscription", "schedule"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "cancel_subscription",
                "args": {"instruction_id": "si_zs_gym"},
                "depends_on": [],
                "arg_sources": [],
            },
            {
                "step_id": "s2",
                "tool": "schedule_transfer",
                "args": {
                    "from_account_id": "acc_zs_chk",
                    "payee_id": {"kind": "none"},
                    "to_account_id": {"kind": "id", "id": "acc_zs_sav"},
                    "amount_cents": 19900,
                    "period": "MONTHLY",
                    "first_run_at": "2026-11-01T08:00:00+08:00",
                    "end_at": "__NULL__",
                },
                "depends_on": ["s1"],
                "arg_sources": [
                    {
                        "param": "period",
                        "source": {"kind": "utterance", "ref": "utt_a", "span": [16, 18]},
                    },
                    {
                        "param": "first_run_at",
                        "source": {"kind": "utterance", "ref": "utt_a", "span": [18, 20]},
                    },
                    {
                        "param": "amount_cents",
                        "source": {
                            "kind": "derived",
                            "template": "cancelled_monthly_fees",
                            "inputs": [
                                {
                                    "kind": "read",
                                    "ref": "src_sub",
                                    "pointer": "/items/0/amount_cents",
                                }
                            ],
                        },
                    },
                ],
            },
        ],
        "rationale": "取消没用订阅并每月存省下来的钱",
    }


# ── Defect A ──────────────────────────────────────────────────────────


def test_defect_a_schedule_intent_rejects_transfer_tool():
    """When understanding.intents contains 'schedule', local validation rejects 'transfer' tool.

    The model must use schedule_transfer, not transfer, for recurring transfers.
    """
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", _bad_schedule_as_transfer()),
            tool_response("submit_action_plan", _good_schedule_transfer()),
        ]
    )
    llm = build(scripted)
    draft = PlanAdapter(llm)(_schedule_state(), ctx())

    # Final draft must contain schedule_transfer, not transfer
    tools = [step.tool for step in draft.steps]
    assert "schedule_transfer" in tools, f"Expected schedule_transfer, got {tools}"
    assert "transfer" not in tools

    # Must have retried (2 requests)
    assert len(scripted.requests) == 2

    # Feedback must mention the schedule vs transfer mismatch
    from tests.agent.test_llm_adapters import body_of

    human = body_of(scripted, 1)["messages"][1]["content"]
    assert "schedule" in human.lower(), f"Feedback should mention schedule: {human}"


# ── Defect B ──────────────────────────────────────────────────────────


def test_defect_b_split_participants_must_exist_in_reads():
    """create_split_request participant_payee_ids must map to payees from list_payees.

    When the model references a payee_id not present in any list_payees read,
    local validation must reject the plan and require correction.
    """
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", _bad_split_wrong_payees()),
            tool_response("submit_action_plan", _good_split()),
        ]
    )
    llm = build(scripted)
    draft = PlanAdapter(llm)(_split_state(), ctx())

    # Must have correct participants
    split_step = draft.steps[0]
    assert split_step.tool == "create_split_request"
    assert len(split_step.args.participant_payee_ids) >= 3

    # Must have retried
    assert len(scripted.requests) == 2

    # Feedback must mention the problem
    from tests.agent.test_llm_adapters import body_of

    human = body_of(scripted, 1)["messages"][1]["content"]
    assert "payee" in human.lower(), f"Feedback should mention payee: {human}"


# ── Defect C ──────────────────────────────────────────────────────────


def test_defect_c_cancel_sub_schedule_requires_derived_amount():
    """When plan has cancel_subscription + schedule_transfer, amount must be 'derived'.

    The schedule_transfer amount_cents source must be kind='derived' with
    template='cancelled_monthly_fees', not a literal utterance amount.
    """
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", _bad_cancel_sub_literal_amount()),
            tool_response("submit_action_plan", _good_cancel_sub_derived_amount()),
        ]
    )
    llm = build(scripted)
    draft = PlanAdapter(llm)(_subscription_schedule_state(), ctx())

    # Must have both steps
    tools = [step.tool for step in draft.steps]
    assert "cancel_subscription" in tools
    assert "schedule_transfer" in tools

    # schedule_transfer must have derived source
    sch_step = [s for s in draft.steps if s.tool == "schedule_transfer"][0]
    amount_sources = [asrc for asrc in sch_step.arg_sources if asrc.param == "amount_cents"]
    assert len(amount_sources) > 0
    assert amount_sources[0].source.kind == "derived"
    assert amount_sources[0].source.template == "cancelled_monthly_fees"

    # Must have retried
    assert len(scripted.requests) == 2

    # Feedback must mention derived/cancelled_monthly_fees
    from tests.agent.test_llm_adapters import body_of

    human = body_of(scripted, 1)["messages"][1]["content"]
    assert "cancelled_monthly_fees" in human.lower() or "derived" in human.lower(), (
        f"Feedback should mention derived: {human}"
    )
