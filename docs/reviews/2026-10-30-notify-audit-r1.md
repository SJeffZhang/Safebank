# PR #96 independent review — REQUEST CHANGES (2026-10-30)

Reviewer: Codex read-only subagent review_notify_audit. No files modified by reviewer.

- P1: notify.py top-level new_id import causes state -> notify -> utterance.service -> state cycle. Fresh subprocess importing notify or utterance.service fails; importing main can conceal it.
- P2: notification read_at_wall and dedup commit before separate NOTIFICATION_READ audit transaction; audit failure loses the record permanently on retry. Concurrent request IDs can duplicate audit/overwrite timestamp. Use row lock and same short transaction.
- P2: cursor.isdigit permits Unicode and unbounded integers; reject invalid/non-ASCII/out-of-bigint inputs with 422.

Confirmed producer notifications run in caller approval/execution transactions, unique(event_id,type) supports idempotency, F+app/customer filters present, templates fixed.
Reviewer tests: diff check PASS; 9 pytest tests skipped without real-PG flag (not acceptance); fresh-process import failure reproduced.

Coordinator follow-up: new red tests reproduced 7 failures/11 passes; fixes pending final independent verification. Full logs in /private/tmp/sbg-notify-review-red.log.
