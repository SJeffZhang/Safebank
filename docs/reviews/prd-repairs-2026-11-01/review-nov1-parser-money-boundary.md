# Nov 1 parser sensitive-amount boundary review

**APPROVE** — final target `e428e15bfd90db9bc70d8af8bb7c102107a2c1c8`, base `2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f`. Production parser is identical to the preceding `e85a6765f7c384517d90665ed2316cd30f6d340d`; the follow-up fixes one new PG query column reference. Exact archives are `/private/tmp/sbg-nov1-repairs/review-parser-e85a676` and `review-parser-e428e15`. No product/worktree/author-probe edits or DB/API/model/provider/Docker/browser execution by this reviewer.

## Safety findings closed

The original target/money boundary is reproduced: copying the final 135 author tests into the exact old base gives **63 failed / 72 passed** (0.43s). The old `赵` + 90600-cent split was dangerous even when left in a CLARIFYING amount slot, since later selecting the target could promote it. The fix checks complete saved current-customer identities against the complete clause, considers all legal splits, and continues only for one distinct split. It does not choose by label length or entity order.

A unique saved 赵六 with 九百 yields exactly 90000 cents. Multiple exact-name/alias splits fail closed. Unresolved adjacent Chinese numerals cannot supply a guessed amount to near/prefix candidates; explicit separators retain candidate-only behavior. Transfer-local malformed leading digit rejection also prevents a short saved name/account from absorbing an unknown or foreign longer name's numeral suffix. This does not change global `money_cents`, card/fund parsing, amount limits, source credentials or approval rules.

The existing R7 genuinely-unclear fixture is now the unknown 钱七 with explicit 900 amount; its original 30 healthy/outage assertions remain strict and unchanged. This preserves the test's original purpose while a separate 135-case boundary suite and explicit-selection tests pin the actual production defect. Existing quote/question/conditional/full-clause, bare multi-transfer, self-account source and foreign-customer boundaries remain covered.

## Independent evidence

- Exact e85 complete W1 pure suite: **1870 passed / 164 skipped** (3.32s), `review-parser-e85-w1.log`.
- Final e428 author boundary/R7 files plus both private probe files: **188 passed / 44 skipped** (0.85s), `review-parser-e428-final.log`. The 44 PG skips are not acceptance.
- Private 16 controls inspect both actual transfers and incomplete amount slots, explicit grammar positive cases and malformed Chinese amounts. Seven additional controls cover duplicate phones, entity-order-independent multiple splits, short owned account names, exact owned account/source identity, mixed unresolved clauses, and real SQL column references.
- Old-base 63/72 red evidence: `review-parser-old-red.log`. Private files: `test_review_parser_money_boundary.py` and `test_review_parser_boundary_extra.py`; all paths above are under `/private/tmp/sbg-nov1-repairs/`.

## Historical test-only P2 and closure

On e85, new PG test `test_r7_prefix_correction_cannot_load_until_explicit_g19_g3_selection` referenced nonexistent `execution_jobs.sid` (line 267). Independent static column probe gave **1 failed / 6 passed** (0.34s), `review-parser-e85-extra.log`; this would fail only when the skipped PG body ran. A was notified. e428 changes it to execution_jobs.plan_id IN the current sid's plan-ID subquery, preserving both zero-plan/job assertions. The same original static probe passes on the exact follow-up. No production authorization change was used to close this finding.

No open finding within this unit. Root must run the original R7 PG cases plus new G3/G19/G3 adoption/loader case and final exact-head CI; this pure/static approval does not assert financial or real-PG success.
