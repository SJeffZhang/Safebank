# L1 Respond Fix (2026-10-30)

**Branch**: `fix/l1-respond-fix`  
**Base**: `origin/main` (c1a8ce0 at time of work)

## Defects addressed

### Fix A: REPLAN reply empty body (commit `46d9b27`)

**Symptom**: When REPLAN findings contained codes not in `_REPLAN_USER_CODE_LABELS`, the reply body after "需要您确认以下信息：" was empty.

**Root cause**: `_REPLAN_USER_CODE_LABELS` dict had only 6 entries. Codes like `AMOUNT_ABOVE_YELLOW_CAP`, `CARD_SINGLE_LIMIT`, `CUMULATIVE_OUTFLOW`, `INSUFFICIENT_FUNDS`, `DAILY_OUTFLOW_LIMIT`, `SCHEDULED_SHORTFALL`, `HOLDING_INSUFFICIENT`, `SUITABILITY_MISMATCH`, `ASSESSMENT_MISSING`, `CARD_DAILY_LIMIT` produced no labels.

**Fix**:
- Expanded `_REPLAN_USER_CODE_LABELS` from 6 to 16 entries
- Added `_UNKNOWN_CODE_LABEL` constant for per-code deterministic fallback
- `_replan_user_items()` now appends fallback per unknown code instead of silently skipping

**Tests**: 6 tests in `TestReplanMissingCodeLabels` (all codes, unknown fallback, mixed, never-empty-tail)

### Fix B: no_op "success" reply (commit `e180f8e`)

**Symptom**: Adding a payee that already existed (`no_op: true` in step result) produced reply "已新增收款人孙九".

**Root cause**: Neither `_execution_steps_summary` nor `_build_fallback` accounted for `no_op` in step results.

**Fix**:
- `_execution_steps_summary`: appends explicit CN no-op fact `"[no_op：该账号已存在于您的收款人列表中，未重复添加]"` per no_op step
- `_is_no_op()`: new helper, handles dict/model `bool`/`string` no_op
- `_build_fallback` EXECUTED path: returns `_NO_OP` constant when any step is no_op
- `_is_no_op_step_text()`: new thin scanner

**Tests**: 4 tests in `TestExecutedNoOp` (summary shows CN text, false-positive rejection, prompt includes fact, fallback says unchanged)

## Verification

```bash
# Full agent test suite
.venv/bin/pytest tests/agent -q
# 577 passed, 38 skipped in 11.22s

# Ruff
.venv/bin/python -m ruff check services/agent/app/llm/respond.py tests/agent/test_respond_e2e_fixes.py
.venv/bin/python -m ruff format --check services/agent/app/llm/respond.py tests/agent/test_respond_e2e_fixes.py
# All clean
```

## Scope

Only files modified:
- `services/agent/app/llm/respond.py`
- `tests/agent/test_respond_e2e_fixes.py` (new)

No docker/up/push/real API calls. No changes to contracts, gateway, L3, or graph routing.