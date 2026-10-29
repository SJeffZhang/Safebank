# L1 Prompt Rules for Subscriptions / Bills — Independent Review

## Verdict

**APPROVE** — no P0/P1. Rules are accurate to the contracts, the local guard is correctly scoped (cannot reject legitimate literal-amount schedules, runs locally in the agent before L2), and prompts never tell the model to bypass or decide authorization. Two minor P2 accuracy nits.

## Findings

### P0

None.

### P1

None.

### P2

1. **Gather prompt conflates `list_subscriptions` with `detect_subscriptions` on `is_idle`.** The added text says both tools yield "带 si_ 开头的 instruction_id 与 is_idle 标记". In the contracts, `list_subscriptions` returns `SubscriptionView` (`views.py:127`), which has **no** `is_idle` field; only `detect_subscriptions` returns `DetectedSubscription` (`views.py:216`) which carries `is_idle` (and its `instruction_id` is `Id | None`, nullable — a detected subscription may not yet have a standing instruction). "找出没用的订阅" specifically needs `detect_subscriptions`; "取消订阅" needs an existing `instruction_id` from `list_subscriptions`. The blanket "or … and is_idle" wording could lead the model to look for `is_idle` in `list_subscriptions` output. Non-blocking: the model reads real tool results and L2 re-validates sources.

2. **Guard error message hardcodes one template while guarding both.** `_derived_schedule_problems` fires on *any* `derived` source for `amount_cents`, which includes both `cancelled_monthly_fees` and `available_less_7d_charges` (`plan.py:41`), but its problem string says "cancelled_monthly_fees 派生金额不能携带…". Semantically correct (an utterance leaf in a derived amount is wrong for either template), just a misleading message if the other template is used. Cosmetic.

## Checks

- **Diff scope** (`origin/main..HEAD`): `docs/progress/L1-prompt-subs-bills.md`, `services/agent/app/llm/gather.py` (+8), `services/agent/app/llm/plan.py` (+47/−2), `tests/agent/test_subs_bills_prompt.py` (+190). Review is read-only; no edits made.

- **Tool names / arg names accurate to contracts** (`packages/contracts/src/safebank_contracts/tools.py`): `cancel_subscription` → `Instruction.instruction_id` ✓; `schedule_transfer` → `Schedule` with `amount_cents`, `first_run_at`, `period`, `from_account_id`/`payee_id`/`to_account_id` ✓; `detect_subscriptions` → `DetectSubscriptions(lookback_days)` ✓; `list_subscriptions` → `StatusQuery` ✓; `get_spending_summary` → `SpendingQuery.month` (`^\d{4}-(0[1-9]|1[0-2])$`) matches "month 为 YYYY-MM" ✓; `get_transactions`/`detect_anomalies` are date-range (`Range` from_at/to_at) ✓; `get_cards` → `CardsQuery` ✓. The `read` source pointer `/items/0/instruction_id` used in tests matches the `items` shape of subscription results.

- **si_ prefix**: `instruction_id` is a generic `Id` in contracts, so the "si_" prefix is a data convention rather than a type-level fact; it is consistent with the L3 seed/data convention (standing instructions are `si_`-prefixed) and with the W2b/gateway derived-amount semantics. Accurate.

- **Derived template name**: `cancelled_monthly_fees` is the correct literal (`plan.py:41` `Literal["cancelled_monthly_fees", "available_less_7d_charges"]`). The plan prompt restricts the X-2 combo amount to this template ✓.

- **SAVINGS target / monthly anchor 1**: the plan prompt says "付款/收款账户仍用 read 来源 … first_run_at 按用户说的日期（如每月1号）". The SAVINGS-target constraint is enforced gateway-side (`services/gateway/app/utterance/parser.py:746-750` requires a single own SAVINGS account for "省下来的钱转到储蓄"), and the monthly anchor is carried via `first_run_at` (anchor day 1 from "每月1号"). The L1 prompt does not contradict these and correctly leaves authorization/derivation validation to L2.

- **Asia/Shanghai date guidance**: gather prompt maps "10月/上个月/9月" to "北京时间（Asia/Shanghai，+08:00）" ✓ (matches the check requirement).

- **No authorization bypass / no authorization decision**: gather prompt only constrains read-tool usage and flags `<untrusted_data>` as non-authoritative; plan prompt mandates read-sourced `instruction_id`, read/step-only inputs for the derived amount, and "账单分析是只读任务，不要生成任何写计划". None of these instruct the model to decide or bypass authorization — they are plan-integrity/source guidance only.

- **Local guard cannot reject legitimate plans**: `_derived_schedule_problems` triggers only when `step.tool == "schedule_transfer"` **and** `source.param == "amount_cents"` **and** `source.source.kind == "derived"`. A normal literal-amount schedule has `amount_cents` sourced as `kind == "utterance"`, so it is skipped. A legitimate X-2 plan with read/step leaves passes (guard only rejects `leaf.kind == "utterance"` inside derived inputs). Contract guarantees `DerivedSource.inputs` is a non-empty tuple of leaves, so `source.source.inputs` is always iterable under the `kind == "derived"` guard.

- **Runs before L2**: the guard is invoked inside `PlanAdapter` in `services/agent/app/llm/plan.py` and raises `ReferenceError` locally before the draft is returned for submission to the gateway (L1 local reference-integrity boundary, same pattern as `_reference_problems`). ✓

- **Exhausted-clarify branch correctly extended**: `der_problems` is now part of `all_problems` and also added to the "only user-missing params → clarification" guard, so a smuggled-literal derived amount no longer collapses into a clarification. Variables are defined in the try block before the raise, matching the existing pattern.

- **Tests**: `.venv/bin/pytest tests/agent -q` → `536 passed, 38 skipped in 6.06s`. The new `tests/agent/test_subs_bills_prompt.py` covers prompt presence, read-sourced `si_` pass, and derived-amount literal-source rejection via `ReferenceError`.
