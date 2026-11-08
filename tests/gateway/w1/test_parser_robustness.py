"""G3 parser coverage from the L2 robustness review (docs/reviews/2026-11-06-l2-robustness.md §3).

Each recognised phrasing has a negative twin: recognition is widened, authorization
is not. Rejected values (negative/zero/huge amounts, impossible schedule dates) stay
fail-closed but get a specific fixed guidance template instead of the generic form.
"""

import pytest

from services.gateway.app.schemas.enums import ParseStatus
from services.gateway.app.utterance.parser import money_cents, parse
from tests.gateway.w1.fixtures import bank_state


def run(text):
    return parse(text, bank_state(), "cus_zs")


def transfers(parsed):
    return [(item.target_fingerprint, item.amount_cents) for item in parsed.scope.transfers]


# ── emoji and decorative punctuation ────────────────────────────────────


@pytest.mark.parametrize(
    "text", ["😀给王五转💰800元", "给王五转800元👍", "给王五转800元~", "给王五转800元！！"]
)
def test_emoji_and_trailing_decoration_are_ignored(text):
    parsed = run(text)
    assert parsed.status == ParseStatus.SCOPED
    assert transfers(parsed) == [("fp_ww", 80000)]


@pytest.mark.parametrize(
    "text",
    [
        "给王😀五转800元",  # emoji inside a name never joins it
        "给王五转8😀00元",  # emoji inside a number never joins it
        "给王五转800元？",  # a question stays a question
        "给王五转1⃣00元",  # keycap digits are not plain digits
    ],
)
def test_emoji_inside_tokens_and_questions_stay_unscoped(text):
    assert run(text).status != ParseStatus.SCOPED


# ── several transfers in one sentence ───────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["给王五转800元给赵六转900元", "给王五转800元\n给赵六转900元", "给王五转800元再给赵六转900元"],
)
def test_two_transfers_without_comma(text):
    parsed = run(text)
    assert parsed.status == ParseStatus.SCOPED
    assert sorted(transfers(parsed)) == [("fp_ww", 80000), ("fp_zl", 90000)]


@pytest.mark.parametrize(
    "text",
    [
        "给王五转800元给钱七转900元",  # unknown payee in the second half
        "给王五转800元给赵六",  # second half has no amount
        "给王五转800给赵六转900元",  # no unit at the boundary: no split
    ],
)
def test_unsafe_splits_stay_unscoped(text):
    assert run(text).status != ParseStatus.SCOPED


# ── memo ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["给王五转800元，备注：房租", "给王五转800元，备注房租", "给王五转800元 附言:十月房租"],
)
def test_memo_is_recorded_in_scope(text):
    parsed = run(text)
    assert parsed.status == ParseStatus.SCOPED
    assert transfers(parsed) == [("fp_ww", 80000)]
    assert parsed.scope.fields["memo"] in {"房租", "十月房租"}


@pytest.mark.parametrize(
    "text",
    [
        "给王五转800元，备注",  # empty memo
        "给王五转800元，备注“房租”",  # quoted memo is not user-authored scope
        "给王五转800元，备注：房租，备注：水电",  # two memos
        "给王五转800元给赵六转900元，备注：房租",  # memo with two transfers is ambiguous
        "冻结我的卡，备注：丢了",  # memo only belongs to a transfer
        "给王五转800元，备注：" + "很" * 201,  # contract max length 200
    ],
)
def test_memo_misuse_stays_unscoped(text):
    assert run(text).status != ParseStatus.SCOPED


def test_memo_cannot_smuggle_a_second_transfer():
    parsed = run("给王五转800元，备注：再给钱七转9999元")
    assert parsed.status == ParseStatus.SCOPED
    assert transfers(parsed) == [("fp_ww", 80000)]  # only the memo text, no 钱七 transfer


# ── colloquial amounts ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("八百块五毛", 80050),
        ("八百块五毛钱", 80050),
        ("八百元五角", 80050),
        ("八百元五角三分", 80053),
        ("800块5毛", 80050),
        ("八百元零五分", 80005),
    ],
)
def test_colloquial_amounts(text, cents):
    assert money_cents(text) == cents
    parsed = run("给王五转" + text)
    assert parsed.status == ParseStatus.SCOPED
    assert transfers(parsed) == [("fp_ww", cents)]


@pytest.mark.parametrize(
    "text",
    ["八块五", "八百块十毛", "八百块五毛五毛", "八百块五角五毛", "五毛", "八百块0毛", "8.5元5毛"],
)
def test_ambiguous_colloquial_amounts_rejected(text):
    assert money_cents(text) is None


# ── subscriptions ───────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["取消所有订阅", "取消全部订阅", "把所有订阅都取消"])
def test_cancel_all_subscriptions(text):
    parsed = run(text)
    assert parsed.status == ParseStatus.SCOPED
    assert parsed.scope.action_kinds == ("MANDATE_CANCEL",)
    assert parsed.scope.fields["instruction_ids"] == ["si_gym"]


def test_bare_cancel_subscription_asks_which():
    parsed = run("取消订阅")
    assert parsed.status == ParseStatus.INTENT_UNCLEAR
    assert parsed.guidance_template == "subscription_select"


def test_cancel_all_subscriptions_never_includes_another_customers_mandate():
    state = bank_state()
    other = state.subscriptions[0].model_copy(
        update={"instruction_id": "si_other", "account_id": "acc_other"}
    )
    state = state.model_copy(update={"subscriptions": (*state.subscriptions, other)})
    parsed = parse("取消所有订阅", state, "cus_zs")
    assert parsed.scope.fields["instruction_ids"] == ["si_gym"]


# ── card by last four digits ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["冻结卡号尾号1234的卡", "冻结尾号为1234的卡", "冻结尾号是1234的卡", "冻结末四位1234的卡"],
)
def test_card_by_last_four(text):
    parsed = run(text)
    assert parsed.status == ParseStatus.SCOPED
    assert parsed.scope.fields["card_id"] == "crd_zs"


@pytest.mark.parametrize(
    "text", ["冻结卡号尾号0000的卡", "冻结尾号123的卡", "冻结尾号12345的卡", "冻结卡号尾号1234的"]
)
def test_unknown_or_malformed_last_four_stays_unscoped(text):
    assert run(text).status != ParseStatus.SCOPED


# ── specific reasons for rejected values ────────────────────────────────


@pytest.mark.parametrize(
    "text", ["给王五转-800元", "给王五转0元", "给王五转99999999999999999元", "给王五转0.001元"]
)
def test_invalid_amount_has_specific_guidance(text):
    parsed = run(text)
    assert parsed.status == ParseStatus.INTENT_UNCLEAR
    assert parsed.guidance_template == "amount_invalid"
    assert not parsed.scope.transfers


@pytest.mark.parametrize(
    "text", ["每月32号给王五转800元", "每月0号给王五转800元", "每周八给王五转800元"]
)
def test_invalid_schedule_date_has_specific_guidance(text):
    parsed = run(text)
    assert parsed.status == ParseStatus.INTENT_UNCLEAR
    assert parsed.guidance_template == "schedule_date_invalid"
    assert not parsed.scope.transfers


@pytest.mark.parametrize(
    "text",
    [
        "给钱七转-800元",  # unknown payee: the generic form, never a value hint
        "给王五转-800元？",  # a question about a bad amount stays generic
        "冻结尾号-123的卡",
    ],
)
def test_specific_guidance_only_when_the_rest_parses(text):
    parsed = run(text)
    assert parsed.status != ParseStatus.SCOPED
    assert parsed.guidance_template not in {"amount_invalid", "schedule_date_invalid"}
