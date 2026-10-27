# L1 Channel Sources Review

**Branch**: `feat/l1-channel-sources`
**Reviewer**: DeepSeek V4 Pro (independent, read-only)
**Date**: 2026-10-27
**Scope**: `git diff origin/main -- services/agent/app/llm/plan.py tests/agent/test_llm_adapters.py`

---

## Conclusion

**REQUEST_CHANGES** — 1 P1 (verb-table inconsistency with L2 `matches()`), 2 P2 (non-blocking).

---

## P0: None

---

## P1-1: `"开通"` and `"关掉"` in L1 direction sets but missing from L2 `text.py` `matches()` boolean sets

**Files**: `services/agent/app/llm/plan.py:134-135` (L1) vs `services/gateway/app/taint/text.py:34-39` (L2)

L1's direction-aware verb partition (`plan.py:134-135`):

```
_CHANNEL_TRUE_VERBS  = {"开启", "打开", "开通"}
_CHANNEL_FALSE_VERBS = {"关闭", "关掉"}
```

L2's provenance `matches()` boolean gate (`text.py:35-38`):

```
True  -> raw in {"true", "是", "开启", "打开", "补卡", "包括自己"}
False -> raw in {"false", "否", "关闭", "不补卡", "不包括自己"}
```

Diff:

| Direction | In L1 but NOT in L2 | In L2 but NOT in L1 |
|-----------|---------------------|----------------------|
| True      | `"开通"`           | `"true"`, `"是"`, `"补卡"`, `"包括自己"` |
| False     | `"关掉"`           | `"false"`, `"否"`, `"不补卡"`, `"不包括自己"` |

**Failure scenario**: User says `"把尾号1234的卡开通境外支付"`, LLM produces `overseas=true` with span exactly `[9,11)` = `"开通"`. L1 accepts it (verb in `_CHANNEL_TRUE_VERBS`). At L2, `provenance.py:137` calls `matches(True, "开通", "overseas", state, "cus_a")`. In `text.py:35`, `normalize("开通")="开通"`, and `"开通" not in {"true", "是", "开启", "打开", "补卡", "包括自己"}` -> `matches()` returns `False` -> trust stays `"unknown"` -> `UNKNOWN_SENSITIVE_ARG` + `REPLAN`.

**Is this branch's fault?** No — L1 faithfully mirrors `intent_lexicon.yaml` (line 9), which lists both `"开通"` and `"关掉"` as `update_card_controls` action verbs. The gap is pre-existing in `text.py`, but was latent because L1 didn't enforce exact-verb spans before. Now that L1 rejects spans that don't match a verb, LLMs will learn to produce exact verb spans — and if they choose `"开通"` or `"关掉"`, L1 accepts but L2 rejects.

**Recommended fix**: Add `"开通"` to `text.py:35` True set and `"关掉"` to `text.py:37` False set. This brings `matches()` in line with `intent_lexicon.yaml`. Alternatively, remove the two verbs from `intent_lexicon.yaml` and `_CHANNEL_VERBS` — but that shrinks usable vocabulary for no reason.

**Why live 10/10 passes**: Coordinator reports say all 10 live samples reached REQUIRE_APPROVAL. Those samples likely use `"关闭"` or `"开启"` or `"打开"` as span text (all in both L1 and L2). `"开通"` and `"关掉"` simply haven't been exercised yet.

---

## P2-1: `_channel_source_problems` normalizes, pre-existing `_reference_problems` does not

`_channel_source_problems` (`plan.py:250-253`) applies NFKC + strip whitespace+Cf normalization before comparing against verb sets — matching L2 `text.py:11-14 normalize()`. The pre-existing `_reference_problems` (`plan.py` ~line 290, unchanged) checks `span[0] < span[1] <= len(text)` but does NOT normalize when comparing span validity. A zero-width joiner (`‍`) in a transfer amount span would pass `_reference_problems` silently but cause mismatches at L2. Not a regression (unchanged code) and L2's `normalize()` catches it at the gate, so correctness is preserved end-to-end. Potential future hardening.

---

## P2-2: Verb comparison in `test_channel_verbs_match_intent_lexicon_yaml` uses ordered tuple equality

`_CHANNEL_VERBS` is `tuple`, `yaml_verbs` is the YAML-parsed tuple. The test (`test_llm_adapters.py` last ~15 lines of diff) uses `==` for comparison — tuples compare ordered. If someone reorders the YAML array entries (e.g. `[关掉, 关闭, 开通, 打开, 开启]` for readability), the test fails even though the *set* of verbs is identical. Direction sets use `frozenset` which is order-agnostic — consistent approach would use `set` for the full verb list too. Not a correctness bug — the test catches it immediately; maintenance hazard only.

---

## Check 1: Verb table & direction consistency

- **YAML lexicon** (`intent_lexicon.yaml:9`): `[关闭, 开启, 关掉, 打开, 开通]` — same five verbs, same order as `_CHANNEL_VERBS`. ok
- **L2 `text.py` direction**: P1-1 documents the gap (pre-existing, now exposed). ok with caveat
- **Consistency test**: `test_channel_verbs_match_intent_lexicon_yaml` reads YAML at runtime, compares verb tuple, verifies direction partition is exact (union = all, intersection = empty). ok
- **Normalization**: L1 `_channel_source_problems` uses identical NFKC + strip whitespace+Cf to L2's `normalize()`. ok

## Check 2: Only catches real errors, no false positives

- **KEEP values**: `value is not True and value is not False -> continue` (line 240). `None` (not changed) skipped. `test_channel_keep_no_source_ok` verifies. ok
- **Non-utterance sources**: `source.source.kind != "utterance" -> continue` (line 235). Read/derived/hint sources for boolean params are not affected. ok
- **Empty span**: Guarded by `if not (0 <= span[0] < span[1] <= len(text)): continue` (line 244), then caught by `_reference_problems`. `test_empty_span_triggers_retry` verifies end-to-end. ok
- **Exact-verb span**: `test_channel_span_exact_verb_passes_first_time` passes on attempt 1. ok
- **Direction mismatch**: `test_channel_span_wrong_direction_triggers_retry` catches `overseas=false` with span `"开通"` (true-class verb), feedback includes `"方向"`. ok
- **Overseas/true direction**: `test_channel_span_overseas_with_verb_passes` passes first time. ok

## Check 3: No L2 authorization duplication, no budget change, no silent mutation

- `_channel_source_problems` only returns problem strings. No values modified. ok
- Same `ReferenceError` -> retry path as existing `_reference_problems`. Retry budget unchanged (inherits `PlanAdapter` limit). ok
- No new `matches()` call, no L2 logic replicated — only verb-set membership check. ok

## Check 4: Retry exhaustion behavior unchanged

- `_channel_source_problems` problems merged into `all_problems` (line 371). Same `ReferenceError` -> `except ReferenceError` -> feedback -> LLM retry loop. Exhaustion falls through to `all_problems` template path (pre-existing). ok

## Check 5: No modification to existing test assertions

- Diff only adds new tests (lines 1493+). No `-` lines remove or modify any existing assertion. ok
- `make test` 2965 passed / agent 482 passed per coordinator. ok

---

## Summary

| Grade | Count | Detail |
|-------|-------|--------|
| P0    | 0     | — |
| P1    | 1     | `"开通"`/`"关掉"` in L1+YAML but not in L2 `text.py` `matches()` boolean sets |
| P2    | 2     | Normalization gap in `_reference_problems` (pre-existing); ordered tuple comparison fragility |