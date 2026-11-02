# Customer event notifications independent review

Target: `a5767d7cec3a1a1e9254e6c2e7cf7ed1c1b08f17`; parent: `72d0a05625e1f20147b7a1728daacd1c7d9524cf`.

**Verdict: APPROVE for this helper/producer unit.** No actionable P1/P2 found in its seven-file scope. This reviewer did not implement it. The root's subsequent L3 REMINDER/S1 transaction integration remains a separate review and is not approved by this result.

Exact-head `git archive` snapshot: `/private/tmp/sbg-demo-repairs-20261031/notification-review-a5767d7`. No worktree source was modified; no real DB/API/LLM/Docker/browser/deployment was run.

## Findings and checked boundaries

### Fixed templates and terminal notification facts

- The six notification types are fixed; event handling maps only supported kinds and terminal statuses to templates. Unknown/nonterminal statuses, successful merchant charges and unsuccessful reminders produce no notification.
- Notification customer/event/source identities are required top-level data. Any supplied execution event/customer/status must agree. Related plan links come only from `source_plan_id`; free merchant/memo/customer/plan text from payload/source strings is not parsed into a plan or displayed in message bodies.
- Specific fund confirmation/settlement/expiry claims require both SUCCESS and the corresponding authoritative `execution.result.order_status`. A successful no-op is suppressed; FAILED/no_op does not silently disappear. Missing business details get a conservative processing/record-check message rather than a false funds-arrival or expiry assertion.
- The helper writes through the producer's supplied connection, does not open/commit its own transaction, and propagates notification-write errors. `UNIQUE(event_id,type)` / ON CONFLICT preserve one identity under retries; no customer-dependent string from callback text becomes the destination.
- Integration assumption is explicit: `status` and the event definition must originate from actual L3 terminal evidence, including recovery. This pure helper does not fetch or authenticate that evidence. The root's promised producer integration must prove the actual terminal and same-transaction boundary before deployment; acceptance documentation does not claim it is already wired.

### Reminder time, authorization and identity

- The shared pure helper admits a complete authoritative billing/mandate pair with matching billing/customer/account/merchant identity, ACTIVE mandate and billing, strict positive integer amount within cap, creation no later than the scan, and authority lasting through the actual charge instant.
- Eligibility is `(scan_at, scan_at+72h]` at exactly Asia/Shanghai 09:00. A scan before 09:00 returns none, equivalent UTC instants preserve identity, and job IDs/period keys use the concrete charge instant rather than the scan date. Cross-day/resume scans therefore reuse the first inserted job/due time.
- Execution recheck compares the old immutable billing/customer/account/amount/charge instant/instruction identity with current rows and rejects cancellation, replacement, expiry or a charge already due. It does not grant permission from matching values; job-due fencing and current L2 grant/revocation are explicitly assigned to the integration layer.
- Scheduler SQL selects authoritative rows with the same owner/account/merchant join and current authorization predicates, then reruns the pure check. The notification template never incorporates merchant/memo free text.

### Clock advancement and recurring merchant cursors

- `scan_due(until=target)` still scans reminders against a fresh current `read_clock`, so a future clock target does not create a future-date reminder early.
- The next daily 09:00 becomes a clock boundary even when the event queue is empty. At that boundary the loop returns to scanning before dispatch, so same-time fund rank 0 precedes reminder rank 5 and reminders become visible at their own scan instant.
- The existing merchant event applier consumes both SUCCESS and BLOCKED periods and advances `next_due_at`; existing `EventService._persist_period_cursor` saves that cursor in the event transaction. The changed scanner does not replace the cursor with stale scan state.
- Independent pure multi-period tests used the real merchant-charge applier with the new ClockAdvancer and dynamic source cursor: two weekly periods completed, ACTIVE mandates produced exactly two distinct reminder identities, DELETED mandates produced no reminders while both blocked charges still advanced the cursor, and the clock reached its target without looping. This is additional pure evidence, not a claim of actual SQL persistence or lease/concurrency behavior.

## Tests actually run

1. Exact snapshot, the four acceptance-listed pure files: `tests/gateway/w5/test_event_notifications.py`, `tests/gateway/w5/test_notify_imports.py`, `tests/obligations_data/test_reminders.py`, `tests/obligations_data/test_scheduler_wiring.py` with `-q --tb=short -p no:cacheprovider` — **74 passed**.
2. Independent temporary tests `/private/tmp/sbg-demo-repairs-20261031/test_notification_review_boundaries.py` — **8 passed**: changed instruction ID/billing/merchant/account/customer/cap, plus active/deleted weekly multi-period clock and reminder uniqueness cases.
3. Ruff `check --no-cache` for the six Python files in this commit — **passed**.

The implementation's documented original helper-missing red tests and the corrected FAILED/no_op suppression regression remain historical author evidence; this reviewer did not rerun missing-entry red states or relabel them as an end-to-end production failure. No real PostgreSQL tests, full CI, live API/model, browser, reminder delivery or production replay acceptance were executed in this review. Root's event-definition/REMINDER execution/S1 finalization integration and final-head database checks remain pending.
