# GW S1 system event handling

**commit**: dcb7b8e  
**date**: 2026-10-30  
**worktree**: `GW-s1-sys` (`feat/l2-s1-system-events`)

## What

S1 `/internal/scheduled/authorize` now handles system events that bypass grants:

| Event kind | Behaviour |
|---|---|
| `FUND_CONFIRM` | Fresh snapshot → `OrderEvidence(order_id=source_id)` → `l3.event_execute` |
| `FUND_SETTLE` | Same as FUND_CONFIRM |
| `FUND_EXPIRY` | Same as FUND_CONFIRM |
| `DEMO_EVENT` | Blocked when demo/eval off; `DemoEvidence` when on |

- `FAILED` results from `event_execute` are blocked with `INSUFFICIENT_FUNDS`.
- `L3Error` releases the claim and returns `RETRYING`.
- Existing `SCHEDULED_TRANSFER` / `MERCHANT_CHARGE` paths unchanged.

## Files changed

- `services/gateway/app/internal/scheduled.py`: +140 lines (imports, `_handle_system_event`, `_handle_demo_event`, routing branch after event fetch)
- `tests/gateway/s1/conftest.py`: `build_app` accepts `demo_mode` param; `make_event_def` accepts `kind` param
- `tests/gateway/s1/test_system_events.py`: 7 new tests (262 lines)

## Tests

- 23 passed (16 existing + 7 new), real tmpfs PG
- Full suite: 3451 passed / 974 skipped, no regressions
- Red-green verified: removing `FUND_CONFIRM` from the kind check causes 3 test failures

## Not covered

- FAILED path from L3 `event_execute` (FakeL3 always returns SUCCESS; real L3 path verified by type)
- `MERCHANT_CHARGE` is not yet implemented in L3 — falls through to existing grant path when it arrives
- DEMO_EVENT with `kind` other than `EXTERNAL_CREDIT` is not tested (contract only supports that one kind)