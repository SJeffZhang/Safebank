# Nov 1 W1 query test isolation review

**APPROVE** — target `481d769a7dfcc4b5a797a924e600fddee3e41b39` vs `2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f`. Exact archive: `/private/tmp/sbg-nov1-repairs/review-w1-query-481d769`. Read-only; no product/worktree/probe edits or DB/API/provider/model/Docker/browser execution.

The diff changes only the four query tests and acceptance note. Original NO_WRITE, parse status, fixed FORM guidance, real parent/candidate selection, exact target/amount and persisted instruction scope assertions remain. The persisted lookup adds sid binding. There is no truncation, deletion, shared-fixture clearing or weakened authorization expectation.

Every created current sid is recorded and must have no plan and no linked execution job. Global before/after plan-ID and job-plan-ID sets must remain exactly equal, so another-session insert or removal of prior data still fails. All calls to each test's isolated L3 fake must be snapshot/read; prepare/execute/event_execute fails. The old UNKNOWN rows are retained rather than excluded from the global snapshot.

Independent verification:

- Original private SQL-boundary probe, unchanged: **8 passed** (0.70s). It retains unrelated UNKNOWN rows and rejects a current plan, another-sid plan, new job, deletion of old jobs, and each financial fake-call type. Log: `/private/tmp/sbg-nov1-repairs/review-w1-query-isolation-pure.log`.
- Exact old base positive retention control: **1 failed** (0.59s) as expected at the global-empty assumption. Log: `review-w1-query-isolation-old-red.log` in the same directory.
- Four actual PG tests: **4 collected** (0.69s), not executed by this reviewer.

No new finding. This approves the test isolation correction; actual PG/full-W1/final-CI results remain root-owned gates.
