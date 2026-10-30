# Schedule calendar source acceptance (2026-10-31)

Scope: only schedule_transfer.period / first_run_at may use deterministic calendar
values from their credential-bound, live, persisted typed scope. The declared span
must identify the same calendar anchor. No generic equality-to-any-scope-field rule.
Dates use Asia/Shanghai regardless of L3 timestamp transport offset. Intent matching
compares the first execution instant without weakening period, target, amount or end.

Red/green evidence:
- New real G3/JWT/PostgreSQL tests on a26c764:2 failed /6 passed. Chinese monthly
  calendar could not prove its values; UTC snapshot produced the wrong 08:00 instant.
- Added Sunday synonym regression:1 failed /10 passed before correction.
- Added full IntentStage assertion: equivalent UTC representation1 failed /10 passed;
  only first_run_at is now compared as an aware instant.
- New calendar cases11 passed, including period/date tampering, unrelated utterance,
  expired credentials, invalid/unrelated spans, Sunday and equivalent time offsets.
- W1+W2 combined2060 passed /2 conditional skips. One initial run exposed an existing
  collection-time +1-minute fixture expiring during preceding tests; moved that future
  timestamp construction to execution time. Follow-up W2+parser412 passed /2 skips.
- Independent read-only security review APPROVE for provenance and intent normalization;
  reviewer personally reran11 real-PG cases twice. Taint and source proof remain enforced.

Pending: clean-generation live case06 and final exact-head full local CI before merge.
This does not claim case36, form G4/G8 or complete six-scene freeze.
