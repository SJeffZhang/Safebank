"""E0a 金额一致性：原话里的金额表达式必须与 params.amount_cents 一致（元 × 100）。"""

from tests.evaluation.corpus import spec


def test_amount_cents_matches_utterance():
    """逐条：params.amount_cents // 100 必须能被确定性金额解析从原话中抽到。

    防止「裸数字=元」约定被破坏（评审 P1-1）：a2/a3 曾把「转50000」写成 50_000 分，
    实为 5,000,000 分。金额解析见 spec.parse_amounts_yuan（7 位以上连续数字视为账号）。
    """
    problems: list[str] = []
    for case in spec.load_cases():
        params = case.expected.params
        if "amount_cents" not in params:
            continue
        amount_cents = params["amount_cents"]
        if amount_cents % 100 != 0:
            problems.append(f"{case.case_id}: amount_cents={amount_cents} 不是整元")
            continue
        yuan = amount_cents // 100
        parsed = spec.parse_amounts_yuan("\n".join(case.turns))
        if yuan not in parsed:
            problems.append(f"{case.case_id}: 原话解析金额 {sorted(parsed)} 不含期望 {yuan} 元")
    assert not problems, f"金额与原话不一致：{problems}"
