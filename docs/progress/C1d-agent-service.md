# C1d Agent Service: Progress Log

> Worker: DeepSeek V4 Pro via Claude Code CLI
> Branch: codex/c1d-agent-service (base main 1ce15f0, later 45b9b45, finally 8ad47d0)
> Started: 2026-10-24

## Task 0: Dependency Verification (8dd42c3)

### Verifications
- `langgraph==1.2.16` in `uv.lock` ✓
- `langgraph-checkpoint-postgres==3.2.0` in `uv.lock` ✓
- `psycopg[binary,pool]==3.3.3` in `uv.lock` ✓
- `starlette` in `uv.lock` (FastAPI transitive dep) ✓
- `fastapi==0.161.1` in main deps ✓
- `uvicorn==0.54.0` in main deps ✓
- `graph.ainvoke()` exists ✓
- `graph.astream()` exists, returns `AsyncIterator[dict[str, Any] | Any]` ✓
- `AsyncPostgresSaver.from_conn_string(url)` returns `AsyncIterator[AsyncPostgresSaver]` (async context manager) ✓
- `AsyncPostgresSaver.setup()` creates tables ✓

### D3: Move agent deps from poc to main
Packages moved: `langgraph==1.2.16`, `langgraph-checkpoint-postgres==3.2.0`, `psycopg[binary,pool]==3.3.3`

No new packages, no version changes. Regenerated `requirements.txt` and `wheels/`.

### Deleted debug files
- `_debug_astream.py`
- `_debug_intr.py`
- `_jwt_debug.py`

## Task 1: Temp PG fixture + checkpoint tables (f55235a)

Docker `postgres:16.10-alpine` on tmpfs (`/var/lib/postgresql/data`), deterministic
image hash. Container tagged `sbg-agent-db-{pid}-{hex}`, port bound to 127.0.0.1.

`AsyncPostgresSaver.setup()` creates `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`.
Repeat setup is idempotent. Checkpoint round-trip passes: `ainvoke` → new saver instance
→ `aget_state` recovers `plan_id`.

## Task 2: Checkpoint no-token and thread isolation (b9aa011)

After a full round (ALLOW), psycopg `LIKE` search finds zero tokens in `checkpoints`,
`checkpoint_writes`, `checkpoint_blobs` (three independent table scans). Two different
thread_ids have fully isolated state (`config["configurable"]["thread_id"]`).

## Task 3: Thread lock tests (b9aa011)

`pg_try_advisory_lock(hashtext(thread_id))` — three cases on real PG:
1. **Same thread concurrent** → second lock returns `False`
2. **Different threads** → both acquire `True`
3. **Lock released on close** → after conn close, new conn acquires

## Task 4: P0 experiment E7 — interrupt replacement (7d5ca78/6ee08ef)

Two tests on real PG checkpoint:
1. **New input discards old interrupt**: After REQUIRE_APPROVAL creates
   `apv_e7_old`, new input (DENY) on same thread → old interrupt not in task list.
2. **Old resume does not trigger execute**: After new input, resuming with old
   approval_id either raises (no pending interrupt) or returns with `execution=None`.

## Task 5: HTTP/SSE endpoints (services/agent/app/main.py)

Three endpoints with JWT auth (Ed25519/EdDSA, `verify_aud: False`, requires `sub/sid/exp`):
- `POST /threads` — create or verify thread (idempotent, 200 either way)
- `GET /threads/{thread_id}/state` — checkpoint state (404 if nonexistent, via `saver.aget_tuple()`)
- `POST /threads/{thread_id}/runs/stream` — SSE stream (input or resume, advisory lock, 409 on conflict)

SSE events: `updates` (with `__interrupt__`), `values`, `messages`, `error`.

### Critical fix: `ainvoke` vs `astream`
`graph.astream()` in langgraph 1.2.16 does NOT properly save interrupt checkpoints
to PostgreSQL. Switched to `graph.ainvoke()` with manual SSE event yielding.
Same-graph `aget_state()` after `ainvoke()` correctly shows interrupts.

### State check for non-existent threads
`graph.aget_state()` returns a default empty StateSnapshot (not None) for threads
with no checkpoint. Added `saver.aget_tuple(config)` pre-check → proper 404.

## Task 6: Process restart recovery

Tests confirm interrupt survives graph rebuild and resume works after rebuild
(same PG, new `AsyncPostgresSaver` instance).

## Task 7: Final Gates

| Command | Result |
|---|---|
| make setup | exit 0, 78 locked packages |
| make lint | 297 files, exit 0 |
| make test | 2498 passed / 587 skipped |
| .venv/bin/pytest tests/agent -q | 266 passed, zero skip |
| make test-agent-db | 26 passed, zero skip (real tmpfs PG) |
| make contracts-check | PASS: contracts v1 match frozen schemas |
| make wheels | Verified 75 locked wheels |
| make demo | exit 2 (not implemented in P1) |
| actionlint | skip (not available, YAML syntax verified) |

### D5: CI integration
Added `- run: make test-agent-db` to `.github/workflows/checks.yml` p1-postgres job.

### Lint fixes across existing tests
- `test_checkpoint_isolation.py`: F841 unused `result` → `_result`, E501 line break
- `test_interrupt_replacement.py`: F841 unused `result1` → `_result1`, `result2` → `_result2`
- `test_lock.py`: E501 line breaks for pg_try_advisory_lock queries
- `conftest.py`: E501 line break for docker exec psql command

## Uncovered / Not Done
- No `make up` / `test-p1*` / `test-integration` (D8: not allowed)
- No real gateway/L3 integration (fake ports — `_make_fake_ports`)
- No C1e output/cards/replay
- Real checkpointer with production postgres-agent volume: tested only with tmpfs containers