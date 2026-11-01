# Nov 1 canonical seed assertions review

**APPROVE** — exact target `7f3dd79c85148d70a7c48e922efda5b2202c8626` vs `2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f`. Archive: `/private/tmp/sbg-nov1-repairs/review-canonical-7f3dd79`. No DB/PG/API/provider/model/Docker/browser or worktree/data writes. Root's decision to preserve canonical semantics/data is honored.

## Integrity and scope

Only the seed-history test, generator comments/docstrings and acceptance note change. Frozen fixture bytes equal the exact base; SHA256 `edbd72f12e179510529af3bab824351289fb1b7a358655614a18ab1193191d45`. Generator AST, after removing docstrings, is identical. Its writer/main was never invoked. No production category mapping, analytics threshold, seed/merchant/transaction/ledger data or existing database is changed. Integrity record: `/private/tmp/sbg-nov1-repairs/review-canonical-integrity.json`. All local logs/probes below use that same private directory.

## Mathematical checks

The default MCC0000 uses the existing canonical category map: 零食/茶饮/外卖 → 餐饮, 生鲜/零售 → 购物. An independent Decimal calculation over the frozen prepared rows confirms strictly prior 90-day CARD_SPEND/POSTED history:

| Item | Prior amount groups (cents) | n | Mean | Rounded mean + 3 population σ |
|---|---|---:|---:|---:|
| 015, amount26800 | 25×800 +24×1800 | 49 | 63200/49 =1289.795918… | 2789 |
| 016, amount26800 | the preceding49 +26800 | 50 | 1800 | 12617 |
| 012, amount12800 | 35×3200 | 35 | 3200 | 3200 |

All amounts exceed their thresholds. Removing every transaction later than015 leaves its CATEGORY_OUTLIER signal unchanged, so future duplicate016 is not a baseline input. The original deliberately embedded014/016/017 remain:014 has NIGHT_LARGE plus NEW_MERCHANT_LARGE;016 retains DUPLICATE (previous015, four minutes) and gains CATEGORY_OUTLIER;017 retains NEW_MERCHANT_LARGE.015 and012 each have CATEGORY_OUTLIER. Thus September's exact chronological IDs are014/015/016/017; through the seed clock they are014/015/016/017/012.

The changed test asserts those exact lists and exact per-item codes, amounts, account `acc_zs_chk`, card `crd_zs_1`, all five evidence fields (including missing-value None), monthly totals August133190 / September644800 / October55050 cents, and checking balance600000. Independent direct consumption sums confirm all three month totals. These are stronger semantic checks, not relaxed counts or hidden signals. The other seed history/memo/ledger/subscription assertions are untouched.

## Independent execution evidence

- Original actual assertion body over pure prepared seed state on the exact old base: **1 failed** (0.35s), `review-canonical-old-red.log`; its global three-item expectation is false.
- Corrected actual assertion body, nine corruption controls, four prior-history/canonical controls and the existing analytics groups: **92 passed** (1.06s), `review-canonical-pure.log`.
- Additional independent amount-group/Decimal-threshold and month-sum calculations: **6 passed** (0.21s), `review-canonical-independent-math.log`; source `test_review_canonical_independent_math.py`.
- Four real seed-history PG tests: **4 collected** (0.26s); not executed; no skipped/pure result is labeled PG acceptance.

No open finding. PRD/AGENTS synchronization, actual test-obligations PG, independent final-head integration and full CI remain root-owned gates. The old43 report observations remain untouched.
