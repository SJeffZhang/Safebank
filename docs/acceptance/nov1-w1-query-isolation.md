# November 1 W1 query test isolation

Base:2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f. Branch fix/nov1-w1-query-isolation. Scope: only tests/gateway/w1/db/test_nov1_query_cancel.py and this acceptance note. Product/auth/parser/lifecycle and shared fixture data are unchanged.

Root full W1 run retained unrelated prior UNKNOWN plans/jobs in its session-scoped PostgreSQL fixture. All four new tests reached their business assertions successfully, then failed because they assumed the entire shared plans/execution_jobs table was empty. A separately passing four-test PG run did not prove whole-suite isolation. The full2b51CI remains failed.

The corrected checks cover every newly created sid, using current-session plan IDs and linked execution_jobs. A before/after global plan/job ID snapshot must also stay identical, catching accidental cross-session inserts or removal of old data. Each test's isolated L3 fake may only receive snapshot/read calls, preserving zero prepare/execute/event_execute attempts. All existing NO_WRITE, fixed FORM, candidate selection/parent and exact persisted scope assertions remain. No shared DB cleanup, deletion, truncation or financial execution was introduced.

Private pure SQL-boundary controls first reproduced the old empty-table assumption:1 failed/7 passed with an unrelated old UNKNOWN plan/job and no current write. The same controls after the test-only correction must allow the retained unrelated rows and still reject a new current plan, another-session plan, new job, deletion of old jobs, and prepare/execute/event_execute attempts. Results pending final verification below.

No DB/service/API/provider/model/Docker/browser run is authorized for this worker. Root owns actual PG reruns, full W1, exact-head full CI and independent review. Pure boundary checks and skipped default PG tests do not count as real PostgreSQL acceptance.

Verification completed in this worktree without DB/service calls:

- Red on the untouched base using the private SQL-boundary controls: **1 failed /7 passed**. Only the positive “unrelated old rows must not imply a current write” control failed. `/private/tmp/sbg-nov1-repairs/w1-query-isolation-red.log`.
- Green using the same controls plus collection of the four actual PG tests with `GATEWAY_DB_TESTS=0`: **8 passed /4 skipped**. `/private/tmp/sbg-nov1-repairs/w1-query-isolation-green.log`; controls `/private/tmp/sbg-nov1-repairs/test_review_w1_query_isolation.py`. The skipped PG cases are not runtime acceptance.
- Ruff check/format with `--no-cache` and `git diff --check` passed. An earlier lint invocation failed only because its cache attempted to write outside the sandbox writable roots; rerun with cache disabled passed, without granting access to product/runtime services.
- The root full-W1 log proves the four failures occurred after their query/scope/FORM/parent assertions, at unscoped final table-emptiness checks. Its old UNKNOWN rows belong to preceding sessions. No need to delete or hide those rows; the new checks keep the before/after global ID sets equal and explicitly verify all current sid scopes.

Required root gates remain: run these four actual PG tests in the full W1 ordering (not only an empty isolated fixture), retain prior UNKNOWN rows, verify no current plans/linked jobs and no L3 financial calls, independently review this two-file change, and rerun the final full-head CI. No passing PG or complete-CI claim is made by this worker.
