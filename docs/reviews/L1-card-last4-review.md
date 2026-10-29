# L1 card last4 (尾号) review

**Reviewer**: independent read-only review (deepseek-v4-pro)
**Date**: 2026-10-29
**Scope**: `git diff origin/main..HEAD` — `services/agent/app/llm/respond.py`, `services/agent/app/llm/verify.py`, `tests/agent/test_verify.py`, `tests/agent/test_respond_cards.py`, `docs/progress/L1-card-last4.md`
**Branch**: `feat/l1-card-last4`

## Verdict

**REQUEST_CHANGES** — one P1 (the deterministic scrub misses a common Chinese copula variant of the exact bug it targets) plus two P2s. Tests pass (508 passed / 38 skipped) and the reported reproduction ("尾号 zs_1") is fixed, but the safety net has a hole the check list explicitly requires closed.

## Findings

### P0

_(none)_

### P1

- **P1-1 — regex misses the "尾号为X" / "尾号是X" / "卡号尾号为X" copula variant.**
  `_CARD_LAST4_RE = 尾号\s*([A-Za-z0-9_]+)` allows only whitespace between 尾号 and the token, so any copula (为/是) breaks the match. Empirically: `extract_card_last4("卡号尾号为zs_1")` → `[]` (also `"尾号是zs_1"` → `[]`, `"尾号为1234"` → `[]`). Check 1 explicitly lists `"卡号尾号为zs_1"` as a case that must be caught; it is not. Consequence: a reply like "已为您解冻尾号为zs_1的卡" passes `_scrub_unverified_card_tails` untouched and `mask_long_numbers` won't touch it either (`zs_1` is not an ≥8-digit run), so the misleading card-ID-tail still reaches the user in a very natural paraphrase. The reported reproduction "尾号 zs_1" (space, no copula) *is* caught, and the prompt-level mitigation (correct last4 + "禁止编造尾号") is intact — this is a gap in the deterministic backstop, not in the primary fix. Suggested direction: tolerate a copula, e.g. `尾号(?:为|是)?\s*([A-Za-z0-9_]+)`, with a red test for each copula form and a re-check that valid "尾号1234" / amounts are unaffected.

### P2

- **P2-1 — `_card_facts_block` emits the card ID back into the prompt.**
  `respond.py` formats the block as `- {cid}: 尾号{last4}`, i.e. `- crd_zs_1: 尾号1234`. The mapping key is the card ID — the exact string the LLM misused — and is unnecessary (the model only needs the tail, not the id→tail association; it already sees the card id in the execution-step result JSON). This contradicts the same diff's `_EXECUTED_SYSTEM` rule "**禁止**打印卡ID" and check 3's "no card IDs … added to the prompt beyond last4". Suggest emitting only `- 尾号1234` (or a bare sorted list of last4s).

- **P2-2 — check 3's "works when reads hold pydantic objects or dicts" is only partially tested.**
  `_collect_card_facts` has a `model_dump` branch and `_attr_or_get` has a dict branch, but the new tests only exercise `ReadRecord` (pydantic) with `data` as a plain dict. The pydantic-`data` path (`data.model_dump(mode="json")`) and a plain-dict read record (`{"data": ...}`) are untested. Not a functional bug (both branches look correct on read-through), but the check explicitly asks for both to be proven.

## Checks

1. **B — regex correctness: PARTIAL (P1-1).** Verified empirically with the exact regex:
   - `"尾号1234"` → `last4="1234"` (untouched, verified). ✓
   - `"尾号 zs_1"` → `last4=None` ✓; `"尾号crd_zs_1"` → `last4=None` ✓; `"尾号12345"` → `last4=None` ✓; `"尾号 ab12"` → `last4=None` ✓.
   - No catastrophic over-match: `"尾号1234，转账80000元，余额6000元"` → only `"尾号1234"` matched; `"尾号 1234 80000"` → only `"尾号 1234"` matched; token stops at the first non-`[A-Za-z0-9_]` char, so it never swallows following numbers/amounts. ✓
   - **FAIL:** `"卡号尾号为zs_1"` → no match (and `"尾号是zs_1"`, `"尾号为1234"`). See P1-1.

2. **C — replacement scope: PASS.** `_scrub_unverified_card_tails` calls `extract_card_last4` (card tails only) and filters `unverified` to `ExtractedKind.CARD_LAST4`, so only card-tail spans are replaced; amounts/percentages are never touched by it. It runs at Step 4.5, before `mask_long_numbers` (Step 5), so masking is still applied afterwards. A verified tail is absent from `unverified` and therefore not replaced — confirmed by `test_valid_card_tail_unchanged` and by the `continue` branch in `check_against_facts`.

3. **A — card facts source: PARTIAL (P2-1, P2-2).** Facts come only from `state["reads"]` (✓), empty reads yield an empty block (`test_no_cards` ✓), and dict/pydantic handling is present in `_collect_card_facts`/`_attr_or_get` (✓). But the prompt block re-adds the card ID as a key (P2-1), and the pydantic-`data` / dict-read-record paths are untested (P2-2).

4. **Tests assert real behaviour: PASS.** Assertions are real string/output/mapping checks, not tautologies: `test_malformed_card_tail_scrubbed` asserts `"zs_1" not in result` (masking alone wouldn't remove it, so this genuinely exercises the scrub) and `"该卡" in result`; `test_valid_card_tail_unchanged` asserts the verified tail survives and no scrub occurs; the extraction tests assert `last4 is None` vs `"1234"`. Full run: `.venv/bin/pytest tests/agent -q` → **508 passed, 38 skipped** (3.87s).

## Notes (non-blocking)

- Scrub replaces the whole `raw` span (including the leading 尾号 and any whitespace) with `该卡`, so `"已为您解冻尾号 zs_1 的卡"` → `"已为您解冻该卡 的卡"` — slightly awkward doubled `的卡` but acceptable.
- `_collect_card_facts` only pairs `id`/`card_id` with `last4` when they sit at the same nesting level; an `id` at one level with `last4` nested deeper is not paired. Matches the actual `get_cards` shape (`{"cards":[{"id":…,"last4":…}]}`), so no action required.

## Round 2

Re-review of `git diff 6da6a17..HEAD` (commits f127d9c red tests, 3f34462 fix, 2066e1b docs).

- **P1-1 copula variants: FIXED.** New regex `尾号(?:为|是)?\s*([A-Za-z0-9_]+)`. Verified empirically: `尾号为zs_1`, `尾号是zs_1`, `卡号尾号为zs_1` all extract with `last4=None`; valid `尾号为1234` and `尾号1234` extract `last4="1234"`; no over-match of amounts — `尾号为1234，转账80000元，余额6000元` → only `尾号为1234` matched. `尾号是这个` (copula + CJK) correctly yields no match.
- **"已为您解冻尾号 zs_1 的卡" → "已为您解冻该卡": FIXED.** New `_TRAILING_DECKA_RE` (`\s*的卡`) absorbed into the replacement; `test_scrub_deka_collapses` asserts exact `== "已为您解冻该卡"`, `test_scrub_without_deka` asserts the no-`的卡` case.
- **P2-1 facts block has no card IDs: FIXED.** `_card_facts_block` now emits only `sorted(set(facts.values()))` → `- 尾号1234`; `test_no_card_id_in_block` and `test_prompt_has_no_card_id` assert no `crd_` in the block/prompt; `test_sorted_deduplicated` asserts sorted dedup.
- **P2-2 dict/pydantic read paths tested: FIXED.** `test_pydantic_data` (BaseModel `data` via `model_dump`) and `test_plain_dict_read_record` (plain-dict read record) both assert the mapping.
- **New problems introduced: none blocking.** Two minor observations, not findings: (1) `尾号是visa` would extract `visa` → `last4=None` → scrubbed to `该卡` (consistent with the design intent that any non-4-digit token after 尾号 is malformed; contrived phrase). (2) `_card_facts` still builds an `id→last4` dict whose keys are now unused in production (the block reads only values) — harmless, could be simplified later. The card ID still appears in the execution-step summary JSON (pre-existing, out of this fix's scope; the fix's own rule + scrub mitigate misuse of it as a tail).
- **Tests:** `.venv/bin/pytest tests/agent -q` → **520 passed, 38 skipped** (3.82s).

### Round 2 verdict

**APPROVE**
