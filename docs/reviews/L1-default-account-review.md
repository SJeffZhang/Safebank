# L1 Default Account & REPLAN Reply — Review

## Verdict

REQUEST_CHANGES

The override itself is correct (only touches `from_account_id`, uses a real read id + `read` source with the right JSON pointer, leaves target/amount/tool untouched, degrades safely when no `get_accounts` read exists), and the REPLAN-user reply is now deterministic with no id leakage. But the "named source" keyword detector is a naive substring over **all** utterances, so it false-positives on exactly the cases this rule must not skip ("转到他的活期" names the *target*; a payee name containing 储蓄/活期/支票/定期; negation "不要用活期"; any earlier turn that mentioned one of these words). When it fires, the default-CHECKING correction is silently skipped and the model's pick (e.g. SAVINGS) flows to L2 and is accepted. Fix the detector before merge.

## Findings (P0, P1, P2)

- **P1 — named-source keyword detection has false positives that silently skip the rule.**
  `_user_named_source_account` returns True if any of ("储蓄","活期","支票","定期") is a substring of *any* utterance in the conversation. False positives:
  - `"转到他的活期"` — "活期" names the **target** account type, not a source; the source is still unspecified, but the rule is skipped and the model's pick (SAVINGS in the test fixture) is kept.
  - a payee name containing one of the words (e.g. `"转给储蓄客户王五"`).
  - negation `"不要用活期"` (user excludes an account type — not "named this source").
  - cross-turn: any earlier utterance that mentioned e.g. `"我活期余额多少"` marks every later unspecified-source transfer as "named".
  Consequence: the transfer proceeds from a SAVINGS (or other) source even though the PRD §7.6 rule says unspecified source ⇒ single CHECKING. L2 accepts it (SAVINGS is a legitimate source), so this is not fund-loss but it directly defeats the fix for the "不稳定" source selection the feature exists to remove. (`services/agent/app/llm/plan.py:_user_named_source_account`)

- **P2 — `_checking_accounts` counts occurrences, not distinct ids → spurious clarification.**
  Two `get_accounts` reads (e.g. accumulated across turns, or LLM read + supplement) that both contain the *same* single CHECKING account yield `len(checking) == 2` and raise `ExhaustedClarify`, even though there is exactly one disambiguable account. Should de-duplicate by account id. (`services/agent/app/llm/plan.py:_checking_accounts`)

- **P2 — closed keyword list can false-negative on other explicit source phrasings.**
  An explicitly named source not using the four words (e.g. `"从理财账户转"`, `"从主账户转"`) is not detected as "named", so the override replaces the user's explicit choice with the CHECKING account. (Whether such account types exist in the seed determines real impact; noted for completeness.)

Non-blocking observation: `_replan_user_items` passes L2 `replan_hints` labels through verbatim (`_replan_hint_labels`). These are L2-authored user-facing labels and the previous LLM path received the same data, so this is not new leakage; the finding-code labels are fixed Chinese strings with no ids. Flagged only so L2 keeps hint labels id-free.

## Checks

- [x] git diff origin/main..HEAD — 5 files: `plan.py` (+77), `respond.py` (+59/-), `test_default_account.py` (new), `test_respond.py` (+28/-), progress doc.
- [x] keyword detection of named source (false pos/neg) — **FAIL** (P1 above): substring over all utterances; "转到他的活期", payee-name, negation, cross-turn all false-positive.
- [x] override only changes from_account_id to a read-data id with correct source pointer — **PASS**. `_default_source_account_overrides` rewrites only `step.args.from_account_id` and the `from_account_id` `ArgSource` for `transfer`/`schedule_transfer` steps; target/amount/tool untouched. Id comes from `get_accounts` read `data["accounts"][i]["id"]` (production shape confirmed: `AccountsResult.accounts=tuple[AccountView,...]`, `AccountView.id/type`, `AccountType.CHECKING=="CHECKING"`); pointer `/accounts/{i}/id` is a valid JSON pointer into that dict; `ReadSource(kind="read", ref, pointer)` matches the contract.
- [x] no get_accounts read / zero CHECKING — **PASS**: `checking` empty ⇒ body returned unchanged; downstream reference/required-value validation handles a missing/hallucinated `from_account_id`.
- [x] respond text leaks no ids/tokens — **PASS**. `_REPLAN_USER_CODE_LABELS` values are fixed Chinese sentences; `respond_template("REPLAN_USER")` = "需要您确认以下信息："; new reply = template + "\n" + de-duplicated bullet list. `test_replan_user_lists_findings_without_ids` asserts no `acc_/pye_/pln_/evl_/src_/utt_/crd_/txn_` markers. (Hint-label passthrough noted above.)
- [x] existing tests not loosened — **PASS**. `test_replan_user_calls_llm` was *replaced* (not weakened) by `test_replan_user_deterministic_reply` asserting `len(llm.calls) == 0` for the intended deterministic change, plus a stricter no-id test added. No assertions weakened.
- [x] `.venv/bin/pytest tests/agent -q` — **526 passed, 38 skipped** (5.57s).

## Round 2

- P1 named-source detection (current turn only, source-anchored) — **FIXED**. `_current_turn_text` returns only the last `UtteranceKind.TYPED` utterance (verified `StrEnum`, `TYPED="typed"`); `_NAMED_SOURCE_PATTERNS` require a source anchor (`从<类型>(账户|卡)?` or `用<类型>…转/付/汇`). "转到他的活期" (target), "转给储蓄客户王五" (payee name), "不要用活期" (negation), and earlier-turn "我活期余额多少" no longer count as named — each has a new RED test. Positive case "从储蓄转500给王五" still keeps SAVINGS.
- P2a CHECKING de-dup by id — **FIXED**. `_checking_accounts` now keys by account id (`setdefault`), so the same single CHECKING account across multiple `get_accounts` reads counts once; two distinct CHECKING ids still raise `ExhaustedClarify`. Covered by `test_duplicate_checking_reads_do_not_clarify`.
- Any new problem:
  - P2: `_NAMED_SOURCE_KEYWORDS` is now dead (only its definition references it; the regexes hard-code the words). Harmless, but should be deleted or reused by the patterns.
  - P2 (edge): `_negated_before` only inspects 2 chars before the match, so "不需要用活期转…" is not caught as negated (the "不" is 3 chars back). Common immediate negations (不/别/勿/莫 right before) are covered.
  - Observation (pre-existing, not new): `_user_named_source_account` is one boolean for the whole plan, so naming a source on one step of a multi-step plan also skips defaulting for the other steps.
- Tests: `.venv/bin/pytest tests/agent -q` → **532 passed, 38 skipped** (5.32s).

## Round 2 verdict

APPROVE