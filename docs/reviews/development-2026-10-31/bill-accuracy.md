# Bill accuracy independent review

Final verdict: **APPROVE** for `32817d9058f223fda18ee24dcb58fe0fe5f4f5fd` after the R1 fix; no remaining blocking finding in the reviewed scope. Coordinator live/SQL and exact-final-head full CI remain required.

Initial verdict: **REQUEST_CHANGES** for candidate `46c8401b75fc1f38d8fbec7d35fde89f092d7d6f` (includes `f98a9d7e4b562054ffc3e117c5a6e1bdd0164055`), base `ccf5d3330b44f4d5049fbbdb5bf5d78ac352ba1b`.

## Scope

Read current AGENTS §§1–3/§8 and relevant PRD §§5.2/6.1/6.7/9.3 under the coordinator's §8.7 targeted-task exception; reviewed all eight changed files, surrounding parser/driver routing, production gather wiring, response/verification behavior and read-envelope preservation. No implementation/shared-doc edits. No real provider, Docker, DB, deployment, credentials, push/merge/tag or helper agent calls.

## R1 — P2: fixed analysis failure is reported as COMPLETED

**Location:** `scripts/try_agent.py:1055–1067`, introduced NO_WRITE terminal branch.

The branch treats any nonempty final AI message plus any reads as a completed analysis when parse_status=NO_WRITE, needs_write=false and no route_error/plan/decision/execution exists. However a fixed analysis failure has exactly that shape: `RespondAdapter._respond_analysis` returns `respond_template("GENERIC_ERROR")` when `_monthly_report_reply` refuses incomplete/mismatched evidence, and ordinary non-monthly response inference errors also return that fixed template. `OutputVerifier` retains this failure message (with normal NFKC punctuation) without setting route_error. The driver consequently returns EXIT_COMPLETED (1) for an explicit failure, where EXIT_UNKNOWN (5), or another structured failure outcome, is required.

This does not bypass L2 write authorization. It does hide a real read/report failure from the acceptance driver, violating the requested no-error completion boundary. Complete SQL/oracle evidence must remain a separate gate as well.

### Pure Mock reproduction

No repo changes, network, DB or credentials are needed. Against the exact candidate, import the existing synthetic helpers:

```python
from _pytest.monkeypatch import MonkeyPatch
from tests.agent.test_bill_accuracy import _state, _report_reads, _context, NoInference
from tests.try_agent.test_bill_readonly import _analysis_values, _run
from services.agent.app.llm.respond import RespondAdapter
from services.agent.app.llm.fixed_templates import respond_template
from services.agent.app.llm.verify import OutputVerifier

state = _state()
state["reads"] = _report_reads()[:-1]  # absent anomalies: intentional fail-closed report
reply = RespondAdapter(NoInference())(state, _context())
assert reply == respond_template("GENERIC_ERROR")
state["reply_draft"] = reply
final = OutputVerifier(NoInference())(state, _context())
values = _analysis_values()
values["reply_draft"] = final
with MonkeyPatch.context() as monkeypatch:
    result, urls = _run(values, monkeypatch)
assert result == 1  # observed; must not be COMPLETED
```

Observed final message: `请求无法处理,请重新发起。未执行任何操作。`, driver exit1. `_run` uses only httpx.MockTransport. The same branch lacks any structured indication distinguishing a successful report from this exact fixed failure.

**Narrow fix requirement:** prevent fixed analysis failures from upgrading to COMPLETED. Prefer a truthful structured analysis-failure marker if practical; any main/graph ownership expansion requires root clearance. As a conservative driver-only fix, exact recognition of canonical fixed-error templates after the same normalization can only downgrade to UNKNOWN; do not infer general success/error from loose prose/substrings or reject a valid report merely because it quotes such words as data. Add an old-fail/new-pass full-driver Mock test for the fixed failure and retain successful B4, unclear=true/NO_WRITE, actual clarification, route_error, DENY, missing-message and mixed-write negatives. Root explicitly agrees this blocks integration pending a narrow fix.

## Other reviewed boundaries

- `parser.py:284` uses full-clause matching. Existing unknown-clause failure and independently parsed mixed writes remain active. No substring read keyword can absorb an appended write/unknown action.
- `gather.py:59` requires one complete typed utterance, actual L2 NO_WRITE, needs_write strictly false, and no actual clarification_id/payload. It does not reinterpret write scopes. `gather.py:168` refreshes actual get_profile, then routes summary and anomalies through the existing user-scoped read executor, preserving real source_ref/taint/version/time. Transfer/AA/redemption prerequisite regions are unchanged in this branch.
- `gather.py:82` converts fresh business time to Asia/Shanghai and constructs `[month-start,next-month-start)`, including December rollover and leap February. It never uses wall time or a transaction-page sum as monthly consumption.
- `respond.py:432` requires matching summary/month/anomaly boundaries; total/previous are strict integer fields. It formats tool-provided categories/shares/change and actual anomaly times/amounts/reasons. Merchant/counterparty/memo free text is omitted. The numeric verifier and masking were not loosened.
- `try_agent.py:1026` still returns CLARIFY for a real clarification before the new read-only completion branch; existing write Decision and execution routes retain priority.

No additional blocking finding in these reviewed paths.

## Independent verification

1. New focused tests: **43 passed**, exit0, `/private/tmp/sbg-bill-independent-focused.log`.
2. Existing parser/date/respond/verifier/try-agent neighboring tests: **437 passed**, exit0, `/private/tmp/sbg-bill-independent-neighbors.log`. These counts are separate overlapping suite observations, not an additive total.
3. Independent probes: six quoted/unknown-suffix/mixed-write parse cases plus a Shanghai/UTC year-boundary relative-month case passed. Quoted report text does not activate the deterministic path; mixed transfer/card clauses stay scoped; unknown suffixes remain INTENT_UNCLEAR.
4. Loaded the original four production modules from exact base into a separate in-memory Python process and reran the new focused tests without editing checkout files: **17 failed /26 passed**, exit1, `/private/tmp/sbg-bill-independent-old-red.log`. Failures cover actual month/anomaly reads/format, monthly read classification and driver completion; existing negative controls remain green.
5. Scoped ruff check/format and candidate whitespace diffcheck passed. Initial ruff cache creation hit sandbox permissions; the corrected `--no-cache` run passed. Initial ad-hoc probe omitted packages from PYTHONPATH, and first old-module harness initialized a module before imports completed; these setup errors were corrected before recording the results above. An initial exact-string verifier assertion was corrected to account for existing NFKC punctuation normalization; the successful R1 reproduction uses the actual verified final string.
6. Candidate worktree remains clean at exact46c8401. No PG/live result is claimed.

## Remaining coordinator gates

Fix R1 and review the incremental diff. Root must still run integrated real live16 with L2 NO_WRITE/no FORM_REQUIRED, exact September range/summary/three anomalies and SQL showing no bank writes, then full exact-final-SHA local CI. Serial merge must preserve AA edits to gather.py. No demo-v1 freeze approval is implied.


## R1 independent follow-up — APPROVE (2026-10-31)

Final candidate: `32817d9058f223fda18ee24dcb58fe0fe5f4f5fd`; incremental parent: `46c8401b75fc1f38d8fbec7d35fde89f092d7d6f`. Reviewed all three changed files; production changes are confined to `scripts/try_agent.py` (nine added lines).

R1 is closed. The new condition only downgrades an otherwise-completing NO_WRITE result when the **whole final reply**, normalized with NFKC and stripped of outer whitespace, exactly equals the current canonical GENERIC_ERROR/REINITIATE text. It does not guess from unknown prose, match substrings or strip arbitrary payload fragments. The actual current two template values are identical and match the driver's reserved constant. Real RespondAdapter incomplete-evidence failure → real OutputVerifier → full MockTransport driver now returns UNKNOWN. A legitimate numeric report containing an error fragment or quoting the entire fixed error continues to return COMPLETED. DENY/REPLAN/ALLOW/executed and actual clarification routing remain above this conservative read-only guard and retain their structured result.

Independent verification against the exact final SHA (no checkout/source edits, no real network/DB/Docker):

- Full try-agent suite plus monthly-report tests: **113 passed**, exit0, 3.77s; `/private/tmp/sbg-bill-independent-r1-green.log`.
- Loaded only the prior46c8401 driver code in a separate Python process, keeping the final regression tests: **4 failed /22 passed**, exit1; `/private/tmp/sbg-bill-independent-r1-old-red.log`. The four fail exactly the actual Respond→Verifier error and canonical/NFKC/outer-whitespace variants. Negative/legitimate/structured-result controls remain passing.
- Incremental scoped ruff check/format and whitespace diffcheck passed. Worktree was clean at final32817d9.

This approval covers the prior reviewed monthly-report implementation plus the incremental R1 closure. It does not claim integrated AA gather preservation, post-fix real live16, bank SQL verification, full PostgreSQL/network CI or demo-v1 freeze; those remain root's serial gates.
