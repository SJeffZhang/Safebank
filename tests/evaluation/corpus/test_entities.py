"""E0a 种子结构校验 + 语料中引用的实体都存在于种子。"""

from tests.evaluation.corpus import spec


def test_seed_passes_structural_validation():
    seed = spec.load_seed()
    problems = spec.validate_seed(seed)
    assert not problems, problems


def test_seed_fixture_id_matches_corpus_reference():
    seed = spec.load_seed()
    assert seed["fixture_id"] == spec.SEED_FIXTURE_ID


def test_all_corpus_entities_exist_in_seed():
    seed = spec.load_seed()
    registry = spec.entity_registry(seed)
    problems: list[str] = []
    for case in spec.load_cases():
        problems.extend(spec.entity_problems(case, registry))
    assert not problems, problems


def test_attack_targets_are_not_seed_payees():
    seed = spec.load_seed()
    registry = spec.entity_registry(seed)
    assert not (registry["attack_account_nos"] & registry["payee_account_nos"])
    assert not (registry["attack_names"] & registry["payee_names"])
