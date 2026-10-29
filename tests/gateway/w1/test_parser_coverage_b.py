"""Parser coverage (task L2-parser-coverage-b): two more live write requests
that were marked INTENT_UNCLEAR and never reached the agent.

1. Recurring transfers with a concrete anchor must now produce a SCOPED
   ``schedule_transfer`` scope whose ``first_run_at`` is computed
   deterministically from the anchor (monthly day-of-month, or weekly
   weekday).  A schedule with *no* anchor (明天/后天/定时) stays FORM_REQUIRED.

2. A shared-expense request ("昨晚聚餐400元，我和小林、小周、小陈AA") must
   produce a SCOPED ``create_split_request`` scope: the total comes only from
   the amount clause, the participants only from the named payees, and no
   money moves.

Safety rules kept: unknown / ambiguous participants -> FORM_REQUIRED (no
guessing); negation, quotes and questions still produce no scope; amounts are
captured from the text only, never invented.
"""

import pytest
from safebank_contracts.state import Payee

from services.gateway.app.utterance.parser import parse
from tests.gateway.w1.fixtures import NOW, bank_state


def state():
    """bank_state() + the payees these utterances name (妈妈, 小林, 小周, 小陈)."""
    s = bank_state()
    payees = dict(s.payees)
    for pid, name, fp, acct in [
        ("pye_mom", "妈妈", "fp_mom", "6222000000000111"),
        ("pye_xl", "小林", "fp_xl", "6222000000000112"),
        ("pye_xz", "小周", "fp_xz", "6222000000000113"),
        ("pye_xc", "小陈", "fp_xc", "6222000000000114"),
    ]:
        payees[pid] = Payee(
            id=pid,
            customer_id="cus_zs",
            name=name,
            alias=None,
            phone=None,
            bank_name="银行",
            account_no=acct,
            target_account_id=None,
            target_fingerprint=fp,
            created_at=NOW,
        )
    return s.model_copy(update={"payees": payees})


_STATE = state()
_CUS = "cus_zs"


# ── 1. 定时转账：每月 / 每周 ────────────────────────────────────────────────


def test_monthly_schedule_gei_zhao_liu():
    """每月20号给赵六转200元 → SCOPED schedule_transfer, next 20th as first_run_at."""
    result = parse("每月20号给赵六转200元", _STATE, _CUS)
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("SCHEDULE_CREATE",)
    assert [(t.target_fingerprint, t.amount_cents) for t in result.scope.transfers] == [
        ("fp_zl", 20000)
    ]
    assert result.scope.period == "MONTHLY"
    assert result.scope.fields["first_run_at"] == "2026-10-20T08:00:00+08:00"


def test_weekly_schedule_gei_mama():
    """每周一给妈妈转100元 → SCOPED schedule_transfer, next Monday as first_run_at."""
    result = parse("每周一给妈妈转100元", _STATE, _CUS)
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("SCHEDULE_CREATE",)
    assert [(t.target_fingerprint, t.amount_cents) for t in result.scope.transfers] == [
        ("fp_mom", 10000)
    ]
    assert result.scope.period == "WEEKLY"
    assert result.scope.fields["first_run_at"] == "2026-10-19T08:00:00+08:00"


def test_bare_future_schedule_still_requires_form():
    """明天给赵六转200元 (no anchor) → INTENT_UNCLEAR, missing first_run_at."""
    result = parse("明天给赵六转200元", _STATE, _CUS)
    assert result.status == "INTENT_UNCLEAR"
    assert result.scope.fields["missing_fields"] == ["first_run_at"]


# ── 2. AA 分账 ─────────────────────────────────────────────────────────────


def test_split_request_aa():
    """昨晚聚餐400元，我和小林、小周、小陈AA → SCOPED create_split_request."""
    result = parse("昨晚聚餐400元，我和小林、小周、小陈AA", _STATE, _CUS)
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("SPLIT_CREATE",)
    assert result.scope.transfers == ()
    assert result.scope.fields["total_cents"] == 40000
    assert result.scope.fields["account_id"] == "acc_zs"
    assert result.scope.fields["participant_payee_ids"] == ["pye_xl", "pye_xz", "pye_xc"]
    assert result.scope.fields["include_self"] is True


# ── 攻击回归：否定 / 疑问 / 未知参与人不得授权 ───────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # 未知参与人
        "昨晚聚餐400元，我和小王AA",
        "昨晚聚餐400元，我和王AA",
        # 总金额句提到收款人（不是聚餐描述）
        "王五400元，我和小林、小周AA",
        # 否定 / 疑问 / 引号
        "不要AA",
        "昨晚聚餐400元吗，我和小林AA",
        "他说“我和小林、小周、小陈AA”",
    ],
)
def test_split_attack_no_scope(text):
    result = parse(text, _STATE, _CUS)
    assert result.status != "SCOPED"
