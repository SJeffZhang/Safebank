# W4b2b Approval Endpoints — progress log

> DeepSeek V4 Pro worker, 2026-10-24. Task brief: `docs/plans/briefs/W4b2b-approval-endpoints.md`; design notes: `docs/plans/briefs/W4b2b-design-notes.md`.

## Coordinator rulings (override task body)

- **D0**: No global `configure()`. Adapter extracts key from `request.app.state`. Endpoints use `verify_binding(key, ...)` directly.
- **D1**: G6/G7 success → plan `PENDING_APPROVAL → CONFIRMED/REJECTED` in same short tx with approval status change.
- **D2**: G9 returns persisted view_json. `status` and `expires_at_wall` are NOT in the content hash domain — override from approvals row. Fields in hash domain must not be changed.
- **D3**: G17 risk via JOIN evaluations.
- **D4**: risk_ack check: only when `risk_ack_required=true` in PlanView. When false, allow `risk_ack=false`.
- **D5**: No new migration. G17 cursor uses approval_id (ULID, time-ordered) DESC.
- **D6**: No new error codes. Red confirm → 503 `DEPENDENCY_UNAVAILABLE`. DB unchanged, approval stays PENDING.

## D2 detail: content hash domain

Verified from `approval/planview.py:_content_dict()` — hash covers:
plan_id, session_id, origin, risk, steps, preflight, findings, disclosures, risk_ack_required, otp_required.

NOT in hash: approval_id, status, evaluation_id, expires_at_wall, view_hash.

Therefore G9 can safely override `status` and `expires_at_wall` from the approvals row.

## Commits

### Commit 1 (783a864): Adapter + G6/G7/G9/G17 endpoints

Files: `approval/adapter.py`, `api/approvals.py`

- `adapter.py` (27 lines): `approval_hmac_key()`, `profile()`, `wall_now()` from `request.app.state` (D0)
- `approvals.py` (~500 lines): G9 panel, G6 confirm, G7 reject, G17 list, G10 stub
- G6: closure-based `conn` capture for atomic `request_idempotent`
- G7: same closure pattern applied (originally had isolation bug)
- G17: JOIN evaluations for risk (D3), cursor pagination by approval_id DESC (D5)
- G9: overrides status/expires_at_wall from approvals row (D2 — fields outside hash domain)
- Red confirm → 503 DEPENDENCY_UNAVAILABLE (D6)

### Commit 2 (f7c8b6e): Test suite

Files: `tests/gateway/w4b2b/__init__.py`, `test_adapter.py`, `db/__init__.py`, `db/conftest.py`, `db/test_g9_get.py`, `db/test_confirm.py`, `db/test_reject.py`, `db/test_list.py`, `Makefile`

- **Adapter pure tests** (5): key extraction, profile, wall_now, instance isolation (D0)
- **G9 DB tests** (9): panel read, auth, ownership, LOCKED, CONFIRMED/REJECTED/EXPIRED still readable, scope check
- **G6 DB tests** (15): confirm, idempotency, risk_ack (D4), red→503 (D6), expiry, view_hash mismatch, binding MAC tamper, session revocation, plan status, scope check, wrong customer
- **G7 DB tests** (11): reject, idempotency, auth, ownership, expiry, view_hash, already-confirmed/rejected, session revocation, scope check
- **G17 DB tests** (8): empty, single, status filter, invalid status, cursor pagination (D5), customer boundary, scope check, risk via JOIN (D3)
- **Makefile**: `test-w4b2b` target (GATEWAY_DB_TESTS=1)

### Exit commands verified

These were NOT run by the worker (no Docker access in sandbox); coordinator must verify:

- [x] `make setup` — worker ran `make setup` to install 78 locked packages (already cached)
- [x] `make test` — adapter 5 passed (0.15s); DB tests NOT run (need Docker for tmpfs PG)
- [ ] `make lint`
- [ ] `make test-gateway`
- [ ] `make test-gateway-db`
- [ ] `make test-shared-runtime`
- [ ] `make test-w1`
- [ ] `make test-w4b1`
- [ ] `make test-w4b2b`
- [ ] `make contracts-check`

## Open issues / 待决

- G10 OTP send / red confirm real OTP → W4b2c
- G8 execution → W4c
- G5 GET /plans/{id} → W3b/W4c
- Notifications → W5
- Red confirm currently returns 503 DEPENDENCY_UNAVAILABLE (D6), cannot be re-confirmed
- DB test conftest creates FastAPI app with `httpx.ASGITransport` — requires Docker for tmpfs PG