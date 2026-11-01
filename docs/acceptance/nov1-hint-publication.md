# Nov 1 T5: bound REPLAN hint publication checkpoint

2026-11-01. Base: d6a5df11d67baaa41c47862c11ab7c24e767f1cf. This is a development checkpoint; real PG and the coordinator-owned G3 hook remain required.

## Evidence and behavior

The production decision stage generated hints but no production path inserted them into `hints`, so a returned G4 hint had no G22a source. The first new pure test failed on zero persisted hint rows. Replayed against an exact d6a5df1 archive after implementation: **1 failed** (expected 1 row, actual 0), 1.17s; `/private/tmp/sbg-nov1-repairs/t5-old-missing-source-red.log`. G4 now saves only server-generated, strictly amount-shrinking agent hints in the same session-locked transaction as its plan/evaluation. FORM hints are withheld; virtual FORM authorization is not recast as typed utterance evidence.

`hints.view_hash` is SQLAlchemy `String` without a declared length cap. It stores a 64-character digest; no migration or JSON-in-varchar encoding is used. The digest binds customer/session/epoch, actual parent plan and hashes, latest evaluation facts/revision, saved live root/source lineage, exact public scope/label, and wall-clock TTL. Publication rechecks saved authorization after the pipeline; G22a rechecks the same binding. Private fields never enter `scope_json` or the G22a response. Candidate hints preserve every action, source, target, period and other scope field; at least one positive transfer amount must strictly decrease. No amount or target is guessed or automatically adopted.

The TTL is the earliest of the proposal deadline, ten wall-clock minutes, and the original authorization deadline. Datetimes are normalized to UTC before hashing and public serialization, preserving equivalent PostgreSQL timestamptz roundtrips. A raw Unicode/invalid digest is rejected as a controlled 409.

Before completing the binding, two new negative tests failed (2 failed / 30 passed): changing the actual plan draft while leaving its stored hash unchanged, and changing findings while retaining the evaluation ID/revision. Both are now bound and rejected. Red log: `/private/tmp/sbg-nov1-repairs/t5-binding-red.log` (local evidence only).

## Verification

- Pure G4 persistence -> G22a view, tampering/staleness, read/write phase races and equivalent timezones: **32 passed** (1.07s).
- New PG file: **8 tests collected**; not executed by this worker. Its positive path covers G4 retry, G22a, G3 adoption/retry, the adopted hint's new G4 and ordinary G9 panel, with no execution job. Negative paths cover stale/tampered binding and concurrent one-time adoption.
- Ruff check/format and `git diff --check`: passed.
- Default `.venv/bin/python -m pytest -q -p no:cacheprovider`: **4554 passed, 1129 skipped** (37.57s). New PG tests are among conditional skips; this is not PG evidence. Local full log: `/private/tmp/sbg-nov1-repairs/t5-default-suite.log`.

## Required integration and exclusions

The coordinator must add `await validate_hint_source(conn, session, row, now)` inside the hint branch of `utterance/service.py`, before constructing `Parsed`, while retaining its current session/source locks, used-by/expiry checks and idempotency. Exact narrow patch is `/private/tmp/sbg-nov1-repairs/t5-service-hook.patch`; this worker did not modify that file. Without the hook, adoption does not enforce the new binding and this checkpoint is incomplete.

The old PG test `tests/gateway/w1/db/test_utterances.py::test_hint_adoption_uses_server_label_and_expires_once` manually seeds an unbound hint with no parent plan/digest. That source must now be rejected; convert it to a legacy-source rejection check when integrating the hook. Exact patch: `/private/tmp/sbg-nov1-repairs/t5-legacy-hint-test.patch` (syntax and `git apply --check` verified, not run against PG). The new real-G4 PG test supplies the positive replacement.

Coordinator gates: execute new PG tests plus affected W1/W3b groups with the hook, independently review, then integrate. No model/provider/API/database/Docker/browser, financial execution or production replay was run by this worker. No original report, shared AGENTS/PRD, parser, grant state, scheduled execution or verifier was edited for T5.
