# Schedule calendar source acceptance (2026-10-31)

Scope: only schedule_transfer.period / first_run_at may use deterministic calendar
values from their credential-bound, live, persisted typed scope. The declared span
must identify its period marker or matching first-date anchor. No generic equality-to-any-scope-field rule.
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

First live candidate03e1295 refused with REPLAN and no new instruction. Stored plan used
period span “每月20号” and first_run_at span “20号”; requiring the first span to repeat “每月”
was too strict. New real-credential minimal-spans regression failed before the narrow correction.
Period span now proves only its period marker; first-date span proves the matching day/weekday,
with both exact persisted scope values still required. Independent follow-up APPROVE,12 cases
personally rerun. W2/parser413 passed /2 skips. Fresh live and final CI remain pending.


Candidate5c3d0cf clean generation demo_20261030174427_66333d44: actual L1 case06 confirmed
through yellow approval, creating a20000-cent monthly transfer to Zhao Liu. Real D3 advance
executed Oct20 and Nov20 at08:00 Asia/Shanghai once each (40000 cents total), next Dec20
08:00. Original plan COMMITTED/EXECUTED/YELLOW, restored grant bound to that plan and original
first time. Ledger balanced; duplicate instants zero. Machine evidence:
schedule-provenance-live.json. Final exact-head full local CI pending.
