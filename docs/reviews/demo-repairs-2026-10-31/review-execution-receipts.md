# Execution receipts independent review

Target: `72d0a05625e1f20147b7a1728daacd1c7d9524cf`; base: `22a87799c2b422a6c2186490a5972e11bc05dce4`.

Scope: `execution_receipts.py`, respond/verify integration and the related pure tests. This reviewer did not implement the target change. Reviewed the exact commit diff and `docs/acceptance/execution-receipt-facts.md`, then ran only offline pure checks. No source edit, DB/API/LLM/Docker/browser/deployment/push/merge.

## Verdict

**APPROVE** on follow-up exact head `eb398e4fbc980be66e1a860539f255bd7e1aa547`. The initial `72d0a05` verdict was REQUEST_CHANGES for the P2 below; the follow-up closes it with fixed state templates. The original finding and initial tests are preserved for audit.

Initial finding: one P2 issue in the explicitly requested FAILED/UNKNOWN repair-validation boundary. The original successful-transfer amount error is fixed by this change; the finding below is not a claim of incorrect bank execution or an observed production transfer.

## Findings

### [P2] FAILED/UNKNOWN repair can still turn a rejected draft into a false success assertion

Affected changed acceptance branch: `services/agent/app/llm/verify.py:583` and `:594`; its semantic checker at `:481–507` only recognizes a small exact phrase list. Because a repair is admitted whenever `check_semantic_consistency` returns empty, common equivalent wording passes both the repair gate and the final gate:

| Execution status | Original draft | Fake repair | Actual final output |
|---|---|---|---|
| FAILED | `转账成功。` | `已转账。` | `已转账。` |
| UNKNOWN | `已到账。` | `已向王五转账。` | `已向王五转账。` |

Minimal input is `{'reply_draft': original, 'execution': {'status': status}}`, with `OutputVerifier(WrongLLM(repaired))(state, None)`. In both cases the initial text is correctly recognized as invalid; a single in-process FakeLLM call returns the repair; that new success assertion is incorrectly accepted. No numbers are necessary, so the numeric fact checker cannot reject it.

Evidence: `/private/tmp/sbg-demo-repairs-20261031/test_receipt_review_negatives.py` contains the two independent regressions. Running them against the target yielded **2 failed** (each `assert output != repaired`).

Requested fix scope: the receipt/verify unit only; leave the author's notification work unchanged. Prefer a deterministic state-based FAILED/UNKNOWN verifier result (the existing fixed templates) so free-text repairs cannot assert any completed action. If retaining free-text repair, cover intervening recipient/account phrases and synonymous completion assertions; merely adding these two exact strings will not close the boundary. Preserve 402/403/429 immediate-stop behavior on provider paths that are still intentionally used, and retain one-attempt negative controls. Add red/green tests for the two inputs above plus a safe failure/unknown message and the existing provider-stop tests.

Reachability boundary: the normal gateway graph currently prepopulates fixed templates for FAILED/UNKNOWN (`graph.py:513–548`), so this review does not claim the two bad replies were emitted in the real production graph. However the changed OutputVerifier API and its new repair guarantee still accept false assertions, contrary to the task's explicit FAILED/UNKNOWN and repair-validation requirement.

## Verified behavior and assumptions

- The original 100 yuan transfer / 5900 yuan available balance / 5000 yuan card-limit confusion is closed for an EXECUTED result: respond and verify use one deterministic receipt helper; the successful path makes no model call and cannot reuse mixed read numbers as transaction amounts.
- State/decision/execution plan IDs, current draft/state session IDs, complete unique step sets, matching tool and EXECUTED step status, committed timestamp, and absence of top-level/step error codes are checked. Missing/mismatched values yield the conservative detail-verification message.
- The helper uses the paired draft amount (TransferResult does not echo amount) and the actual per-step result available balance; reversed result order cannot swap recipient/amount/balance. Existing graph ingest clears previous-round draft/execution and evaluate establishes the plan-ID/draft pair. This graph invariant remains the source of draft-to-plan binding; the helper cannot independently authenticate a deliberately replaced same-session draft because the frozen execution contract does not carry its hash.
- Non-fund actions distinguish add/cancel/card no-op semantics, subscription savings, AA collected/outstanding amounts, and scheduled future transfers. Fund receipts bind account/product/type/units or amount and distinguish accepted/pending/settled/failed states.
- Display names come only from the corresponding read tool and entity ID, use quoted short labels and reject the tested injected claim/number/control/markup strings. Card tails are ID-bound, and final long-number masking remains applied.
- Mixed EXECUTED results with failed/rolled-back/incomplete/duplicate/mismatched steps do not receive a success receipt. Provider 402/403/429 negative controls still raise after exactly one mocked HTTP request.

## Tests actually run

1. `.venv/bin/pytest tests/agent/test_execution_receipt_facts.py tests/agent/test_respond.py tests/agent/test_respond_e2e_fixes.py tests/agent/test_verify.py tests/agent/test_provider_stop.py -q --tb=short -p no:cacheprovider` — **205 passed**.
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:packages/contracts/src:packages/bankcore/src .venv/bin/pytest /private/tmp/sbg-demo-repairs-20261031/test_receipt_review_negatives.py -q --tb=short -p no:cacheprovider` — **2 failed**, expected review negative controls described above.
3. Ruff `check --no-cache` and `format --check --no-cache` on the eight changed Python files — **passed**, eight files formatted.
4. An initial direct Python probe omitted the repository package PYTHONPATH and failed during import (`safebank_contracts` absent); rerunning with the explicit package paths produced the two observed bad outputs. This import failure is not counted as a product failure.

Not run: full Agent suite, full CI, real DB/API/model/browser/record/replay acceptance. Source HEAD remained `72d0a05625e1f20147b7a1728daacd1c7d9524cf` at verification. An author-created untracked `docs/acceptance/customer-event-notifications.md` appeared during review and was left untouched.


## Follow-up review — APPROVE

Exact head: `eb398e4fbc980be66e1a860539f255bd7e1aa547` (the author's final amendment only adds evidence documentation relative to `f9fe794`). Diff from the initial reviewed head changes only verify, its two regression cases and the acceptance record. FAILED now returns `executed_failed_fallback()` and UNKNOWN returns the existing UNKNOWN template before any draft/repair/provider path; EXECUTED still reconstructs the bound receipt. The two originally observed false-success outputs therefore cannot pass through this path.

The author's worktree was still at `72d0a05` with unrelated notification edits, so this reviewer did not switch branches or alter any source file. Instead `git archive eb398e4...` was extracted into `/private/tmp/sbg-demo-repairs-20261031/receipt-recheck-eb398e4` for exact-head offline verification using the existing virtual environment. The independent external negative controls were updated from `llm.calls == 1` to `== 0`, the correct expectation for the requested deterministic fix; their false-success output assertions remain.

Fresh checks on that exact snapshot:

- The same five specified pure test files: **207 passed** (205 original plus two added regressions).
- Independent external FAILED/UNKNOWN negative controls: **2 passed**, model calls exactly zero.
- Ruff check/format on the two follow-up Python files: **passed**.

No new actionable issue found in this bounded follow-up. Full CI and actual business/API/DB acceptance remain coordinator gates and are not claimed here.
