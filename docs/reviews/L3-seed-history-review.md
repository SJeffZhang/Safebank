# L3 Seed History Review

Scope: `git diff origin/main..HEAD` — 张三 synthetic transaction history in the demo seed (`services/bank-backend/fixtures/l1_seed_v1.json`) + generator script (`scripts/generate_seed_history.py`).
Independent read-only review. No code edited, no commit, no push. Static review (no Docker).

## Verdict

**REQUEST_CHANGES** — the seed data itself is internally consistent and its analytics detectability is correct, but (1) the branch is behind `origin/main` and merging it as-is would delete two already-merged features (P1), and (2) the daily card spends are dated June, not the specified 2026-07-17..10-14 (P1).

## Findings (P0, P1, P2)

### P0
None.

### P1

**P1-1 — Branch is behind `origin/main`; merging would regress main.**
`git diff --stat origin/main..HEAD` shows the branch's own seed-history files *plus* the reverse of two features that are already on `origin/main`:

- **L1 default-account** (`fix/l1-default-account`): `services/agent/app/llm/plan.py` (-119), `services/agent/app/llm/respond.py` (-59), `tests/agent/test_default_account.py` (-198), `tests/agent/test_respond.py` (-28), `docs/progress/L1-default-account.md` (-118), `docs/reviews/L1-default-account-review.md` (-49).
- **L2 parser-coverage-c** (`feat/l2-parser-coverage-c`): `services/gateway/app/utterance/parser.py` (-19), `tests/gateway/w1/test_parser_coverage_c.py` (-167), `tests/gateway/w1/test_parser.py` (-4), `tests/gateway/w2/test_derived_cancelled.py` (-7), `docs/progress/L2-parser-coverage-c.md` (-91), `docs/reviews/L2-parser-coverage-c-review.md` (-35).

These are "behind main" artifacts: HEAD does not contain those features, so a straight merge would delete/rewind them on `main`. The branch must rebase onto (or merge in) current `origin/main` before any PR.

**P1-2 — Daily card spends are in June, not the specified 2026-07-17..10-14.**
The task spec says "~130-150 daily card spends 2026-07-17..10-14". The 140 generated rows (`txn_zs_100..239`) all fall in **2026-06-01..06-28** (`_daily_txns` docstring: "spread across June (before the anomaly window)"). Consequence: 张三's checking account has *zero* everyday card spending in the most recent ~3 months (Jul–Oct); a "last 3 months" bill/usage view shows only mandates + anomalies. The count (140) and small-amount/daytime design are correct, but the date range contradicts the spec and weakens the 账单分析 / recent-activity demo narrative. (Note: placing them in July per spec would *not* break the "exactly 3 anomalies" test — everyday categories 餐饮/出行/零售/日用 are disjoint from the anomaly categories, and amounts are all < 100000, so they never trip `NEW_MERCHANT_LARGE`/`CATEGORY_OUTLIER`.)

### P2

**P2-1 — Progress doc category labels don't match the generator.**
`docs/progress/L3-seed-history.md` describes the daily-spend categories as 「餐饮/交通/购物/娱乐」, but the generator uses 「餐饮/出行/零售/日用」 (`scripts/generate_seed_history.py` merchants list). Cosmetic, but the progress doc is the audit trail for this seed and should list the actual categories.

## Checks

### Check 1 — Generator deterministic; JSON matches it — PASS
- The generator uses no randomness or wall-clock: `(i % 4)`, `day = 1 + i//5`, `slot = i % 5`, `minute = (i*7) % 60`, `amount = base + (i*13) % spread`. Deterministic.
- jq verification of the emitted JSON matches the generator's asserted shape exactly:
  - 175 transactions total (generator prints "wrote 175 transactions").
  - Kind histogram: 153 `CARD_SPEND` (140 daily + 4 anomaly + 6 gym + 3 enriched original), 12 `MANDATE`, 6 `P2P_EXTERNAL`, 3 `P2P_INTERNAL`, 1 `REFUND_CREDIT`. Direction: 1 IN / 174 OUT.
  - `acc_zs_chk` credits_posted 2,097,874 / debits_posted 1,497,874 / ledger 600,000 — equals the generator's `credits = 1_207_950 + new_out_total`, `debits = 607_950 + new_out_total`.
- (Python re-execution of the generator was blocked by the sandbox; determinism asserted statically and cross-checked via jq against the committed JSON.)

### Check 2 — Data matches spec; no other row flagged by anomaly rules — PARTIAL
- **Exactly 3 anomalies**, verified in JSON: `txn_zs_014` 夜色酒吧 2026-09-07T02:13 128,000 (NIGHT_LARGE + NEW_MERCHANT_LARGE); `txn_zs_015`/`016` 美味外卖 2026-09-15T12:00/12:04 26,800 each (4 min apart → DUPLICATE); `txn_zs_017` 星耀电子 2026-09-22T15:30 349,900 (NEW_MERCHANT_LARGE). ✓
- **4 monthly subscriptions**, each 3 charges at 06:00: 爱看视频 d18, 乐听音乐 d20, 云盘 d1, 力健健身 d25. ✓
- **Gym idle**: 6 CARD_SPEND visits at mer_gym, 2026-07-19..08-15, none after 08-16. ✓ (`usage_expected=true` on mer_gym only.)
- **Refund**: `txn_zs_013` 2026-10-14T10:20, IN, 120,000, counterparty 退款中心, verbatim memo, `balance_after_cents=600000`. Kind is `REFUND_CREDIT` (not `CARD_SPEND`), so it cannot be falsely flagged as a new-merchant card spend. ✓
- **Final balance**: acc_zs_chk ledger = 600,000. ✓
- **No other row flagged**: everyday merchants (餐饮/出行/零售/日用) have categories disjoint from the anomaly merchants (娱乐/外卖/数码) and small amounts; the 3 enriched October original rows (010/011/012) are 餐饮/出行/零售 with small amounts; anomaly categories have no ≥10 prior same-category history within 90d, so no `CATEGORY_OUTLIER` misfires. ✓
- **Deviation**: daily spends are dated June (see P1-2), not the spec's 07-17..10-14.

### Check 3 — balance_after chain consistent; final 600,000; funding conservation — PASS
- Chain: opening credits = `ORIGINAL_OPENING_CREDIT + new_out_total` = 1,977,874; iterate chronologically (OUT subtracts, IN adds); refund IN (120,000) is last chronologically and closes at 600,000. Internally consistent; jq confirms `txn_zs_013.balance_after_cents == 600000` and account ledger 600,000.
- Global conservation: `seed_demo.install_demo` funds each new account via `funding = credits_posted_cents - in_total_by_account` debited against `acc_seed_equity`, and folds IN/OUT deltas into the internal clearing account (`debits += in_delta`, `credits += out_delta`). The added spending is therefore auto-absorbed by an equal increase in opening funding; `sum(debits_posted) == sum(credits_posted)` holds. The test asserts exactly this. ✓
- Observation (not a defect): to preserve final = 600,000 while adding ~889,924 cents of new OUT, the opening checking balance becomes ~1,977,874 cents (~¥19,779), a large synthetic June balance draining to ¥6,000 by October.

### Check 4 — Injection memo is only data — PASS
- `txn_zs_013.memo` holds the verbatim injection text; it is stored as an untrusted text column on a `REFUND_CREDIT` row (no `card_id`/`merchant_id`).
- Gateway marks `memo` untrusted: `policies/trust.yaml` lists `memo` under `get_transactions`/`detect_anomalies`; `pipeline/provenance.py` treats `memo` as a tainted source (`MEMO_SENSITIVE_CONTENT`), and `intent.py`/`planview.py` only echo it as display/params.
- L1 is instructed not to fabricate memo (`plan.py`: nullable params including `memo` default to `__NULL__` unless the user stated them; "must not copy payee names/account numbers into memo"). No code path parses a transaction memo as an instruction.

### Check 5 — Tests assert real values; E0 changes justified — PASS
- `tests/obligations_data/test_seed_history.py` asserts real values, not just shape: exactly 3 anomalies with sorted amounts `[26800, 128000, 349900]` and codes ⊇ `{NIGHT_LARGE, DUPLICATE, NEW_MERCHANT_LARGE}`; exactly 4 subscriptions with `mer_gym` idle (`previous_usage_count==6`, `recent==0`); `acc_zs_chk` credits−debits == 600000; `txn_zs_013.memo` equals the verbatim `REFUND_MEMO`; global `sum(debits)==sum(credits)` and pending == 0.
- E0 changes: none. `git diff --stat origin/main..HEAD` contains no `evaluation/` files; the progress doc confirms the E0 runner uses `mini_seed.json`, not `l1_seed_v1.json`, so no old transaction-count assertion needed updating.
