# Review: L1 card choice labels and card write sources (247382c)

Reviewer: independent read-only reviewer (Claude subagent). Scope: `git show 247382c` on
`fix/l1-card-choices`, base `origin/main` 4fa52ac. Only this report was written.

## Verdict

**APPROVE.** No P0 or P1. Authorization is not widened anywhere; the D2 check is at least as
strict as before for `report_card_lost` and newly stricter (matching L2 semantics) for the
other four card writes. Three P2 items below; none blocks merge.

## Findings

### D1 trust trace (no finding)

- `state["clarification"]` is set only in `ingest` (`services/agent/app/graph/graph.py:382`)
  from `GatewayClient.get_clarification` (G21, `services/agent/app/gateway/client.py:83-97`),
  validated as `GatewayClarification` (`wire.py:96`). The only other writer
  (`graph.py:721`, `source: "l1_exhausted"`) has no `candidates`, so it contributes nothing.
  Model output and read data cannot reach this field.
- The only `card_id` candidate producer in L2 is `parser.py:1159`:
  `_candidate("card_id", card.id, "尾号" + card.last4)` over `_card_candidates(...)`, i.e. the
  customer's own L3 cards. `last4` is L3 data, not user or merchant text, so a candidate label
  is not attacker-controlled. The new verifier accepts only `field == "card_id"` and a
  fullmatch of `尾号\d{4}` (`verify.py:451-458`), so payee/product labels or decorated labels
  add nothing (covered by `test_only_card_field_candidates_with_exact_label_count`).

### D2 vs L2 semantics (no finding)

- L2 card tool policy marks `card_id` sensitive for all five tools
  (`services/gateway/policies/tools.yaml:46-85`). L2 `matches()` (`taint/text.py:61`) only
  resolves strings by exact value or by account/payee/product names; cards are not in the
  candidate groups, so a typed tail span (`1234`) can never make `card_id` user-trusted at L2.
  The new L1 rule (read from a current-turn `get_cards` read, or a verbatim typed card id, or
  hint/action/step left to L2) therefore only pre-rejects plans L2 would REPLAN anyway.
- `report_card_lost`: before, required exactly one `read` source in `card_reads`; now
  identical (read must be in `card_reads`, every non-read kind returns False at
  `plan.py:913`). The `replace` check is unchanged. Nothing loosened.
- Full `tests/agent` (incl. X-4 `test_nov1_x4_plan_retry.py`, `test_cross_scene_x4_gather.py`)
  passes: 1152 passed, 41 skipped. X-4 gather already supplies `get_cards` (`gather.py:214`)
  and hint/action sources pass through.

### P2-1 Clarification-answer turn does not trigger the supplemental card read

`gather.py:410-422` keys the supplement on the latest TYPED utterance of the current round
only. In the natural continuation of D1, L2 offers `尾号1234/尾号5678`, the user replies
`尾号5678`; that text does not match `_CARD_WRITE_TEXT`, so if the model again fails or skips
its own `get_cards`, no owned read exists and `_card_source_problems` rejects both attempts,
ending in a plan error rather than a gateway-checked plan. This is not a regression (L2 would
REPLAN a tail-sourced plan before too), but D2 is only half-closed for this path. Suggest also
supplementing when `state["clarification"]` has a `card_id` candidate or its
`partial_scope.action_kinds` is a card action.

### P2-2 One test is not a red test; one assertion is vacuous

`tests/agent/test_card_write_sources.py:75` (`test_tail_span_as_card_id_source_is_retried_with_feedback`)
passes on 4fa52ac too (some other existing check already triggers a retry there), and its
`"get_cards" in str(scripted.requests[1])` is satisfied by the system prompt text alone. The
parametrized `test_every_card_write_needs_a_card_read_source` does discriminate (fails on old
code for all four tools), so coverage of the change exists; tighten this test to assert the
new problem string (e.g. "原话里的尾号只用于") in the feedback message.

Untested branches: `_typed_card_id` positive path (verbatim `crd_...` typed, accepted for
non-lost tools), hint/action pass-through, and utterance-kind rejection for
`report_card_lost` under the new function name (the latter is covered indirectly by
`test_lost_card_sources.py`).

### P2-3 Regex over-trigger and verifier binding (informational)

- `_CARD_WRITE_TEXT` (`gather.py:32`) matches read-only questions like `我的卡限额是多少`,
  `转账限额`, or wealth text containing `冻结`. Effect is one extra owned, null-filter
  `get_cards` read through the user's gateway executor, once per gather (not looped; the
  `_supplement_reads` call is single, after the model loop). Harmless; query paths that return
  early (`card_capacity`, X-1, X-4, monthly report) are unaffected because they return before
  `_supplement_reads`. Verbs such as `停用`/`锁卡`/`禁用` are not covered (falls back to the
  model's own read).
- Provider errors: model `invoke` errors (402/403/429) propagate from the loop before the
  supplement runs; the supplement only calls the gateway read executor and is not retried.
  A gateway failure on the supplemental read raises as before. No retry/loop risk.
- Verifier: candidate tails are added to the global `last4s` set, so while a card
  clarification is open, any reply may mention any owned candidate tail. Like the existing
  reads-based path, the verifier checks tail existence, not which card an action applied to.
  The tails are true owned-card facts, so no false data is introduced.

### Modified existing test

`test_gather_no_supplement_for_non_transfer` (`tests/agent/test_llm_adapters.py:1428`) now
expects exactly one `get_cards(account_id=None)` read for `冻结卡`. The test's purpose (no
account/payee supplement for a non-transfer intent) is preserved and the new assertion is an
exact list, so it would still catch an extra account/payee read. Justified; it does not hide a
regression.

## Tests run

- Commit tree (247382c):
  `pytest -q tests/agent/test_card_choice_labels.py tests/agent/test_card_write_sources.py tests/agent/test_lost_card_sources.py tests/agent/test_llm_adapters.py tests/agent/test_llm_live.py`
  → 98 passed.
- Full `pytest -q tests/agent` → 1152 passed, 41 skipped.
- Old code: temporary detached worktree at 4fa52ac (scratch dir), the two new test files
  copied in, run with the branch venv → 11 failed, 6 passed. Failing: both D1 label tests,
  all four `test_every_card_write_needs_a_card_read_source`, `test_card_id_from_another_read_tool_is_rejected`,
  all four `test_failed_model_card_read_is_backed_by_owned_card_read`. Passing on old code:
  `test_tail_span_as_card_id_source_is_retried_with_feedback` (see P2-2), the two negative
  controls for D1, `test_card_read_source_is_accepted`, `test_no_card_read_for_unrelated_text`,
  `test_successful_model_card_read_is_not_repeated` (expected controls). The temporary
  worktree was removed afterwards.
- Not run: real PG, gateway, or live model.
