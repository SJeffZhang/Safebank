"""E0a 语料分布统计：总量、攻击量、覆盖矩阵、占位场景下限。"""

from tests.evaluation.corpus import spec


def test_total_at_least_300():
    cases = spec.load_cases()
    assert len(cases) >= spec.MIN_TOTAL, f"总量 {len(cases)} < {spec.MIN_TOTAL}"


def test_attack_corpus_at_least_60():
    attack = [c for c in spec.load_cases() if c.category in spec.ATTACK_CATEGORIES]
    assert len(attack) >= spec.MIN_ATTACK, f"攻击语料 {len(attack)} < {spec.MIN_ATTACK}"


def test_every_planned_variant_has_at_least_three_phrasings():
    counts = spec.distribution(spec.load_cases())
    missing: list[str] = []
    for category, variants in spec.CATEGORY_VARIANTS.items():
        for variant in variants:
            if counts.get((category, variant), 0) < spec.MIN_PER_VARIANT:
                missing.append(f"{category}/{variant}={counts.get((category, variant), 0)}")
    assert not missing, f"低于 {spec.MIN_PER_VARIANT} 条的变体：{missing}"


def test_each_out_of_scope_scenario_has_at_least_five_placeholders():
    cases = spec.load_cases()
    for scenario in spec.OUT_OF_SCOPE_SCENARIOS:
        n = sum(1 for c in cases if c.category == "out_of_scope" and c.variant == scenario)
        assert n >= spec.MIN_PER_SCENARIO, f"out_of_scope/{scenario} 只有 {n} 条"


def test_out_of_scope_placeholders_are_no_plan_and_pending_tools():
    for case in spec.load_cases():
        if case.category != "out_of_scope":
            continue
        assert case.expected.outcome == "NO_PLAN", case.case_id
        assert case.expected.tool is None, case.case_id
        assert case.model_extra and case.model_extra.get("pending_tools") is True, case.case_id


def test_no_category_is_empty():
    counts: dict[str, int] = {}
    for case in spec.load_cases():
        counts[case.category] = counts.get(case.category, 0) + 1
    missing = [cat for cat in spec.CATEGORY_FILES if counts.get(cat, 0) == 0]
    assert not missing, f"空 category：{missing}"
