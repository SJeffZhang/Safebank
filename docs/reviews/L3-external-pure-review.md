# L3-external-pure Review

Scope: `git diff origin/main..HEAD` (worktree R-L3-external-pure, commits 120d658, 9c5ea3a, 37629d5, 058420d).
Changed files: `packages/bankcore/src/safebank_bankcore/external.py` (+302), `tests/bankcore/test_external.py` (+390), `docs/progress/L3-external-pure.md` (+57). No changes under `packages/contracts/`, `banking.py`, `ledger.py`, `split.py`, or any dispatcher/wiring.

## Verdict

**APPROVE** — no P0/P1. Two P2 notes (non-blocking, one self-documented).

## Findings

### P0

None.

### P1

None.

### P2

1. **Card-state rule diverges from `banking.apply_transfer` on a dangling `current_card_id`.** `apply_external_debit` (external.py:214-220) does `card = state.cards.get(account.current_card_id) if account.current_card_id else None`, then `if card: … else: single, daily = account.single_limit_cents, account.daily_limit_cents`. `banking.apply_transfer` (banking.py:78-82) instead treats "`current_card_id` set but card missing from `state.cards`" as `CARD_STATE_CONFLICT` (`if not card or card.status != "ACTIVE"`). So external debit is fail-open relative to banking when the account references a missing card, silently degrading to account-level limits. Reachable states (ACTIVE/FROZEN/LOST) match correctly, and no current code path produces a dangling reference (cards are never deleted; `report_card_lost` swaps `current_card_id`), so this is theoretical today. Worth aligning when the module is wired into L3.

2. **`ExternalCredit.kind` / `ExternalDebit.kind` are free strings** with no enum whitelist; the value flows into `LedgerTransfer.code` / `Transaction.kind`, and the transaction category is a naive two-way split (`"消费"` iff `kind == "CARD_SPEND"`, else `"其他"`). This is self-documented in `docs/progress/L3-external-pure.md` ("未覆盖/后续") and acceptable for whitelisted synthetic/demo events, but should be tightened (whitelist + category mapping) before any caller can pass arbitrary `kind`.

## Checks

1. **Limits & card-state rules match banking.py — PASS with one divergence (see P2-1).**
   - Single limit: `amount > single → CARD_SINGLE_LIMIT` (external.py:221-222) matches banking.py:85-86.
   - Daily limit: `used + amount > daily → CARD_DAILY_LIMIT` (external.py:223-225) matches banking.py:87-88.
   - Card state: non-ACTIVE (FROZEN/LOST) → `CARD_STATE_CONFLICT` (external.py:216-217) matches banking.py:80-81. Account-level fallback when no card matches banking.py:84.
   - Divergence only in the dangling-`current_card_id` case (P2-1). All reachable states match; tests `test_rejected_over_single_limit`, `test_rejected_over_daily_limit`, `test_rejected_on_frozen_card`, `test_rejected_on_lost_card`, `test_rejected_over_balance` confirm.

2. **Daily usage accounting — PASS.** `apply_external_debit` reads `state.counters.daily_used_cents.get(account.id, 0)`, enforces the limit, and after a successful post increments `daily_used_cents[account.id]` by the amount (external.py:223-225, 241-249), keyed by account id exactly as banking.py:87,131. `daily_outflow_cents` / `rolling_outflow_cents` are copied through unchanged — intentional: card spend is not an external transfer (progress doc & PRD §7.7 "仍检查余额与适用卡限制"). Test `test_within_limits_posts_and_counts_daily` asserts the increment and that a second spend trips the daily limit.

3. **Split allocation consistent with split.py — PASS.** `apply_split_payment` requires exact item amount (`amount != item.amount_cents → SPLIT_INVALID`, so overpay and mismatch both rejected; external.py:269-270), rejects already-PAID items (external.py:267-268), credits the requester's own account (`split.account_id` must be `ctx.customer_id`'s, else NOT_FOUND; external.py:271-273), and updates `received_cents += amount`, `outstanding_cents -= amount` (external.py:293-299). This preserves `received + outstanding + self_share == total`, matching `split.build_split_view` (`outstanding = total - self_share - received`, split.py:231). Tests `test_credits_requester_and_marks_item_paid`, `test_second_payment_rejected`, `test_amount_mismatch_rejected` cover it. `counterparty = item.payee_name` is the friend who paid — matches test expectation and `SplitItem.payee_name` semantics.

4. **Ledger conservation — PASS.** `_post_simple` (external.py:84-177) reimplements `ledger.post_transfer`'s POSTED path: identical idempotency (`cmd.id in transfers` → no-op on equality, else `IDEMPOTENCY_CONFLICT`), `CONTRACT_INVALID` (self-transfer / `occurred_at != now`), `NOT_FOUND` (missing account), and `INSUFFICIENT_FUNDS` (customer debit only: `debits_posted + debits_pending > credits_posted`, external.py:131-135 vs ledger.py:80-84). It reuses shared `account_deltas(cmd, None)` and `replace_state`. Internal clearing account (`customer_id is None`) is exempt from the non-negative check and acts as source/sink, so total balance across all accounts is conserved — asserted by `test_credit_conservation` and `test_debit_conservation`.

5. **Int cents — PASS.** `_valid_amount_cents` rejects `bool`, non-`int`, and `<= 0` (external.py:80-81); all three entry points validate before posting. `test_float_amount_rejected`, `test_zero_and_negative_amount_rejected`, and `test_all_posted_amounts_are_int_cents` confirm no float/`bool` amounts reach posted state.

6. **No IO — PASS.** `external.py` imports only `dataclasses`, `datetime`, `pydantic`, `safebank_contracts.*`, and local pure modules (`ids.derive`, `ledger.account_deltas`/`replace_state`). No DB, HTTP, filesystem, or randomness.

7. **Additive-only contract changes (ADR-011) — PASS.** `git diff --stat origin/main..HEAD -- packages/contracts/` is empty: no contract, enum, or frozen-snapshot changes. `external.py` adds no new `BankState` fields (it reads/writes existing `accounts`, `transfers`, `transactions`, `counters`, `splits`), consistent with ADR-011's "add new optional fields with empty default; never rename/remove/retype". `apply_external_credit`/`_debit`/`_split_payment` are not wired into any dispatcher (`apply_plan`, `step_result`, L3 persist) — grep confirms references only in `external.py`, tests, PRD, and docs.

8. **Tests — PASS.** `.venv/bin/pytest tests/bankcore -q` → **399 passed in 0.46s** (includes the 18 new `test_external.py` tests; no skipped/errors in the tail).
