# November 1 S1 revocation boundary

Owner: read-path worker. Parent query repair headbc01392. Scope: only internal/scheduled.py and new S1 pure tests. Root separately owns grant_revocation.py/state.py; read-only tombstone schema inspected in root worktree.

Plan: demonstrate INSERT-conflict local ACTIVE resurrection; re-read actual stored row after backfill. Demonstrate cancelled ordinary/tombstone grants misclassified BYPASS; permit only a fresh, strictly bound DELETED mandate to record MANDATE_NOT_ACTIVE/CHARGE_BLOCKED, never execute. ACTIVE bank with revoked grant and all forged identities/scopes/hashes/sources remain BYPASS. Pure tests first; real PostgreSQL and independent security review remain root gates. No live/API/provider/Docker operations.

Results pending.

## Red -> green evidence

Untouched scheduled.py plus the new boundary tests:4 failed /35 controls passed. Failures were conflict reconstruction returning local ACTIVE, original/tombstone revoked grants producing BYPASS instead of a correctly bound cancellation refusal, and inconsistent ACTIVE+revoked_at reaching event_execute.

Fix:

- _backfill_grant selects and returns the actual stored conflict winner inside the insert transaction; it never returns its local ACTIVE candidate.
- Only MERCHANT_CHARGE+REVOKED enters a fresh-snapshot deny-only path. It requires exact event identity; DELETED mandate; customer/account/merchant/billing/source-plan/scope/hash linkage; canonical immutable amount/account/billing payload and hash; matching aware due/period and current due billing. Both original consent and exact root tombstone markers are accepted only for refusal.
- Root marker is matched as cancellation proof: exact revocation keys, cancel_subscription tool, nonempty plan/step, marker plan equals stored cancellation source, customer/instruction tombstone digest, RED storage sentinel. This never reconstructs creation consent and cannot execute. Original revocations retain their original consent binding.
- Shared bankcore apply_merchant_charge must independently yield BLOCKED/MANDATE_NOT_ACTIVE before that code is recorded. All malformed/forged/ACTIVE-bank cases remain SCHEDULED_BYPASS. Any stored revoked_at also fails the normal ACTIVE-grant path closed.
- Real _block/_finalize_claim/notify_event_result are exercised in pure tests with only SQL execute, L3 transport, advisory claim and audit append replaced. Both valid revocation forms produce a terminal BLOCKED decision and CHARGE_BLOCKED notification with zero event_execute calls; bank balance remains unchanged.

Commands/results:

- New boundary file:46 passed (including forged customer/source/hash/scope/instruction/amount/payload/period/due, malformed marker, fake-ACTIVE tombstone, original seed-consent and valid ACTIVE grant controls).
- Entire tests/gateway/s1 default group:72 passed,36 skipped. All real PG cases remain skipped.
- Full default pytest:4555 passed,1121 skipped in38.03s.
- Ruff check/format and git diff --check passed.

No real API/provider/DB/Docker/browser was called. Root grant_revocation.py/state.py/WIP and shared AGENTS/PRD were not modified.

## Required integration hooks / unrun gates

Coordinator must combine the root committed-cancellation archive hook with this S1 consumer at the same final head. Actual PostgreSQL checks still required: missing-row INSERT racing a committed REVOKED tombstone, existing grant revocation preserving original source/scope, genuine DELETED merchant callback through real L3 event_block/instruction_run/period cursor with exactly one CHARGE_BLOCKED notification, and callback replay/no funds transfer. Root owns the real PG serial run, state/version fences, full exact-head CI and merge; this pure boundary evidence is not live acceptance. Independent B/C security review requested after commit.
