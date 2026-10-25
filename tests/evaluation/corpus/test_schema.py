"""E0a 语料 schema 校验：字段枚举、金额整数分、reason code 是真实 Code、case_id 唯一。"""

from safebank_contracts.codes import Code

from tests.evaluation.corpus import spec

VALID_CODES = {member.value for member in Code}


def test_corpus_is_not_empty():
    assert spec.load_cases(), "语料为空：先运行 generate.py 生成 JSONL"


def test_case_ids_are_unique():
    seen: set[str] = set()
    dupes: set[str] = set()
    for case in spec.load_cases():
        if case.case_id in seen:
            dupes.add(case.case_id)
        seen.add(case.case_id)
    assert not dupes, f"重复 case_id：{sorted(dupes)}"


def test_reason_codes_are_real_gateway_codes():
    unknown: set[str] = set()
    for case in spec.load_cases():
        for code in case.expected.reason_codes_any:
            if code not in VALID_CODES:
                unknown.add(f"{case.case_id}:{code}")
    assert not unknown, f"非真实 L2 finding code：{sorted(unknown)}"


def test_params_amounts_are_positive_int_cents():
    problems: list[str] = []
    for case in spec.load_cases():
        problems.extend(spec.param_type_problems(case))
    assert not problems, problems


def test_params_keys_are_valid_tool_args():
    """params 键必须是该工具的合法参数（评审 P1-2：split_count 等非工具参数移出 params）。"""
    problems: list[str] = []
    for case in spec.load_cases():
        tool = case.expected.tool
        if tool is None:
            if case.expected.params:
                problems.append(f"{case.case_id}: 无 tool 却带 params")
            continue
        allowed = spec.TOOL_PARAMS.get(tool)
        if allowed is None:
            problems.append(f"{case.case_id}: tool={tool} 不在 TOOL_PARAMS 白名单")
            continue
        extra = set(case.expected.params) - allowed
        if extra:
            problems.append(f"{case.case_id}: 非工具参数 {sorted(extra)}")
    assert not problems, problems


def test_executable_cases_have_risk_and_others_none():
    """可执行样本（ALLOW/REQUIRE_APPROVAL）必须有 risk（GREEN/YELLOW/RED），其余为 None。

    评审 P1-3：红级需 OTP 与黄级坍缩为 REQUIRE_APPROVAL，无法验证 L2 是否给了 RED。
    """
    problems: list[str] = []
    for case in spec.load_cases():
        outcome = case.expected.outcome
        risk = case.expected.risk
        if outcome in ("ALLOW", "REQUIRE_APPROVAL"):
            if risk not in spec.RISK_LEVELS:
                problems.append(f"{case.case_id}: 可执行样本缺 risk（got {risk!r}）")
        elif risk is not None:
            problems.append(f"{case.case_id}: 非可执行样本 {outcome} 不应带 risk={risk!r}")
    assert not problems, problems


def test_every_case_parses_into_corpus_case():
    # iter_cases() 内部已做 Pydantic 解析与枚举校验；这里确保逐条可读且数量一致。
    files = [path for path in spec.CORPUS_DIR.glob("*.jsonl")] if spec.CORPUS_DIR.exists() else []
    assert files, "evaluation/corpus/l1_stability 下没有任何 JSONL"
    n = 0
    for _, case in spec.iter_cases():
        assert case.turns
        assert case.case_id
        n += 1
    assert n == len(spec.load_cases())
