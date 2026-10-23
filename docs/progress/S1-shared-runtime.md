# S1 shared runtime — Codex implementation record

Scope: authorized S1 brief only; baseline `8555bf0`, branch `feat/l2-shared-runtime`. No push, PR, merge, subagents, full-stack restart, or named volume mutation. AGENTS status/log updates are owned by the coordinator per brief.

## Component 1 — clarification transitions

Added `pending_clarifications` OPEN → RESOLVED/CANCELLED through the shared state entry point. Both terminal states have no outgoing edge; illegal transitions raise ValueError. Conditional UPDATE still provides one winner and a 409 STATE_CHANGED loser.

Actual commands:

- `.venv/bin/pytest tests/gateway/shared_runtime/test_clarifications.py -q`: expected red, 2 failed (missing transition registration), 1 skipped (PG not enabled).
- `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/shared_runtime/test_clarifications.py -q`: 3 passed, including concurrent UPDATE on real PostgreSQL 16. Temporary randomly named container used the existing isolated fixture; no data volumes mounted by the test command.

Files: `services/gateway/app/state.py`; `tests/gateway/shared_runtime/{conftest.py,test_clarifications.py}`. Component commit: `d2726bb` (`feat(gateway): centralize clarification transitions`).

## Component 2 — runtime / revocation

Central `gateway_runtime` allocates one AsyncEngine and one L3Client, installs db/l3/aware wall_now/settings/JWT keys on both apps, and disposes in reverse order once after both servers stop. Secret validation runs before allocating resources. Missing/unsafe/malformed/mismatched Ed25519 keys refuse startup. Partial startup and server failures release owned resources.

Global `caller_dependency` checks F/A customer JWTs with W1's dynamically resolved `tokens.check_revocation(conn, caller, now=...)` before entering handlers. Missing DB, clock, or checker returns 503. W1 GatewayError (revoked/missing/expired) propagates; database failures return sanitized 503. Service-only F/S/D and non-customer console JWT credentials retain their matrix behavior. The checker substitute only proves wiring, not W1's real database semantics.

W1 PIN module is absent at baseline: runtime records `pin_hashes=None` and installs no success placeholder. Once W1 is integrated, startup calls its configured_hashes exactly once and injects the returned hashes. Loader failure refuses startup. Coordinator approved this phased behavior; S1 does not duplicate PIN parsing/hashing. No actual login → session → revoke API acceptance has run.

Actual commands:

- New runtime/revocation tests before implementation: 23 failed / 3 passed (missing runtime and missing revocation enforcement).
- Added settings injection assertion: expected red, 1 failed / 10 passed / 1 skipped; then green.
- Added clean-listener shutdown regression: expected red TimeoutError; then fixed peer shutdown.
- `PATH=... GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/shared_runtime -q --tb=short`: 33 passed, including two real PG tests (clarification contention and shared runtime pool); L3 transport remains controlled MockTransport.
- `make lint`: passed, 150 files formatted.
- `make test-gateway`: 95 passed / 2 skipped (isolated PG tests intentionally not enabled in that target).

Files: `services/gateway/app/{main.py,config.py,runtime.py,auth/deps.py}`, `tests/gateway/shared_runtime/{test_runtime.py,test_revocation.py}`.

W4 locks integration remains pending: its fixed `customer_lock(customer_ids)` signature needs centralized initialization once W4 defines a configuration hook, e.g. `locks.configure(db,max_executions=settings.max_executions)`. S1 has no fabricated lock implementation or speculative hook call.

## Component 3 — Linux secret permissions

Not yet implemented or verified.
