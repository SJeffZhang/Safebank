# Review: L1 trailing memo treated as data (commit c998f86)

Reviewer: independent read-only reviewer (Claude subagent). Date 2026-11-09.
Scope: `services/agent/app/llm/plan.py` (+69), `tests/agent/test_memo_as_data.py` (+155).

## 1. `_memo_region` vs gateway `_MEMO`

- Agent regex (plan.py:603) is the gateway regex (parser.py:838) with the NFKC-folded
  full-width forms added explicitly: `，` `；` `：`. Python `\s` already matches U+3000,
  so full-width space is covered. `.` without DOTALL and non-multiline `$` behave the
  same on both sides (a memo followed by a newline-separated clause matches on neither).
- Trailing trim set `" \t。.!！~"` matches the gateway's `strip(" 。.!！~")`; leading
  whitespace is consumed by the regex's greedy `\s*`, matching gateway `.strip()`.
- Index arithmetic: `found.span("memo")` is on the original string, end is trimmed only
  backwards, result is `[start, end)`. Verified by the test indices (15..25 for the D4
  text) and by hand.
- Residual differences (all low impact):
  - Compatibility forms that NFKC folds but the agent does not list: `﹐` (U+FE50),
    `﹔`, `︰`, `～` (U+FF5E). Probe: `给王五转100元﹐备注房租` → agent None, gateway
    memo `房租`; `…备注房租～` → agent region includes `～`, gateway strips it. Only effect:
    no memo hint/check for that utterance (miss) or a one-character wider region.
  - Gateway rejects the whole utterance when the memo contains `备注|附言|[，,；;。\n\r]`,
    or when quotes change it (parser.py:909-915). The agent still marks such a region
    (probe: `…备注：房租，再给赵六转50元` → region covers the second clause). Because the
    gateway returns INTENT_UNCLEAR for these, L1 rejecting args sourced there cannot
    reject a plan the gateway would accept. Harmless.
  - Gateway applies `_MEMO` after `_command_text` (correction-prefix removal) and quote
    removal; correction prefixes are only at the start, so a trailing memo is unaffected.
- No case found where L1 marks text as memo that the gateway would scope as a command.

## 2. `_memo_instruction_problems` false rejections / bypass

- Skips `param == "memo"` and non-utterance sources; only TYPED utterances get regions
  (hint/action/context excluded). Per-utterance-id regions, so multi-utterance states and
  G21 replies are handled independently. Amount/payee spans before the memo do not
  overlap. Legitimate D4-style plans pass (test `test_memo_value_from_memo_text_is_accepted`).
- Possible false rejection: an overly wide utterance span (e.g. a whole-utterance span for
  amount) overlapping the memo. This only triggers one retry with clear feedback; the
  gateway's minimal-span rules would reject such spans anyway. Acceptable.
- Bypass: a memo-derived step whose args use only read/none sources, or no utterance
  source, is not caught here. L2 still rejects it as INTENT_SCOPE_EXCEEDED (gateway scopes
  exactly one transfer), so this is a quality gap, not an authorization gap.

## 3. Retry loop and ExhaustedClarify guard

- `memo_problems` is added to `all_problems` (plan.py:1237) and to the ReferenceError-path
  ExhaustedClarify guard (plan.py:1257) — a memo-instruction failure there cannot become a
  clarification. Correct.
- The ValidationError path (plan.py:1262-1271) is not guarded: on the final attempt
  `_exhausted_clarify_fields_from_ve` can still raise ExhaustedClarify for a memo-derived
  step whose only VE errors are null user-only params (e.g. a memo-derived `add_payee`
  with name sourced from the memo and `account_no="__NULL__"`). The user would be asked
  for an account number for an operation that came from memo text. L2 would still refuse
  the extra operation, and this shape is unlikely (the observed D4 failure is a
  targetless entity error, which is not user-only), so P2.
- `_memo_note` is appended to every shape-failure note when any memo exists; harmless and
  gives the targeted hint for the D4 path.

## 4. Tests fail on pre-fix code

Copied commit tree to `/private/tmp/claude-501/memo-red`, restored `plan.py` from
`c998f86^`, ran the new tests (with `_memo_region` import stubbed): **5 failed / 2 passed**.
The two passes are the negative controls (`no_memo_line_without_memo`,
`memo_value_from_memo_text_is_accepted`). `retried_with_feedback`, `never_returned` and
`targetless…_names_the_memo` fail on behaviour (old code returns the 2-step plan / old
retry note lacks the memo text), not only on the import.

## 5. Other

- No change to L2, authorization, or plan rewriting; the check only triggers a retry or a
  ReferenceError. Prompt rule and per-utterance memo line are consistent.
- Runs: `tests/agent/test_memo_as_data.py` 7 passed; full `tests/agent` 1159 passed,
  41 skipped.

## Findings

- P2 services/agent/app/llm/plan.py:1262-1268 — ValidationError-path ExhaustedClarify is not
  gated on memo-sourced args; a memo-derived step with only null user-only params could turn
  into a clarification. L2 still refuses it.
- P2 services/agent/app/llm/plan.py:603-604 — NFKC compatibility forms (`﹐` `﹔` `︰` `～`)
  are not folded; region missed or one char wider than the gateway's. Consider matching on
  NFKC per char or adding these characters.
- P2 services/agent/app/llm/plan.py:628-648 — memo-derived steps with no utterance source in
  the memo region are not caught locally (L2 catches them as scope exceeded).

Verdict: APPROVE
