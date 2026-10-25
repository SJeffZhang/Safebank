"""E0a 语料去重：NFKC 归一化后不得有近似重复（同 category 或全局）。"""

from collections import defaultdict

from tests.evaluation.corpus import spec


def test_normalize_text_is_nfkc_and_collapses_whitespace():
    assert spec.normalize_text("ＡＢＣ　１２３") == "ABC 123"
    assert spec.normalize_text("给  王五\t转\n3000") == "给 王五 转 3000"


def test_dedup_key_is_nfkc_insensitive():
    from tests.evaluation.corpus.spec import CorpusCase, Expected

    a = CorpusCase(
        case_id="x1",
        category="transfer",
        variant="colloquial",
        turns=["给王五转３０００"],
        fixture="l1_seed_v1",
        expected=Expected(outcome="REQUIRE_APPROVAL", tool="transfer"),
    )
    b = CorpusCase(
        case_id="x2",
        category="transfer",
        variant="colloquial",
        turns=["给王五转3000"],
        fixture="l1_seed_v1",
        expected=Expected(outcome="REQUIRE_APPROVAL", tool="transfer"),
    )
    assert spec.dedup_key(a) == spec.dedup_key(b)


def test_no_near_duplicate_cases_in_corpus():
    by_key: dict[str, list[str]] = defaultdict(list)
    for case in spec.load_cases():
        by_key[spec.dedup_key(case)].append(case.case_id)
    dupes = {key: ids for key, ids in by_key.items() if len(ids) > 1}
    assert not dupes, f"近似重复语料：{dupes}"
