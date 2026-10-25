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
