# W4c execute 进度（G5 计划状态 / G8 执行与恢复）

2026-10-26；worktree `codex/w4c-execute`，基线 W3b 候选 `b8fbb8e`。
任务书：`docs/plans/briefs/W4c-execute.md`；设计笔记：`docs/plans/briefs/W4c-design-notes.md`。

## Task 0 检查点（C6 前提验证）——完成

### 交付

- 建立 `tests/gateway/w4c/db/conftest.py`：复用 `gateway_db`、`keys`、`obligations_db`、`obligations_0002_db` 四个既有隔离 fixture（与 W3a/W3b 同模式）。
- 新增 `tests/gateway/w4c/db/test_c6_fence_prepare_premise.py`：真实 L3 前提验证（见下）。
- `Makefile` 新增 `test-w4c` 目标（仿 `test-w2`/`test-w3a`，同时置 `GATEWAY_DB_TESTS=1 OBLIGATIONS_DB_TESTS=1`，因为 W4c 真实 L3 场景需要 obligations fixture）。

### C6 前提验证结论：**通过**

用真实 bank-backend（`create_app` + uvicorn 线程 + `install_core`/`install_obligations` + 真实 `L3Client`）实测：

1. 首次 `prepare`（attempt=1，`deadline_wall = now + 5min`，`deadline_cap_wall = now + 10min`）→ `{"status": "PREPARED", "attempt": 1}`。
2. **同 attempt**、同 evidence/steps/plan_hash/deadline_cap_wall，仅 `deadline_wall = FENCE_AT = 1970-01-01T00:00:00Z`（已过期）的第二次 `prepare` → `{"status": "PREPARED", "attempt": 1}`，**未被拒绝**。

与 L3 源码一致（`services/bank-backend/app/execution.py:108-119`）：同 attempt 的 PREPARED 只做
`deadline_wall = min(旧, 新)`，没有 `deadline_wall > now` 校验；只有首次 prepare（`:74`）与 attempt+1（`:121-125`）才拒绝过期 deadline。

因此 ADR-008 屏障的 fence prepare（同 attempt + 过期 deadline）可以走通，屏障设计成立。若此验证不通过，按裁定 C6 应停止上报——现通过，继续 T1。

### 基线退出命令（Task 0 只新增测试与 Makefile 目标，无生产代码改动）

| 命令 | 结果 |
|---|---|
| `make setup` | exit 0（78 锁定包，9ms） |
| `make lint` | exit 0（340 files formatted；首跑 I001 已修复） |
| `make test` | 2702 passed / 706 skipped |
| `make test-gateway` | 2018 passed / 613 skipped |
| `make test-gateway-db` | 17 passed |
| `make test-w4c` | **1 passed**（真实 PG + 真实 L3，6.80s） |
| `make contracts-check` | PASS |
| `make test-agent-db` | 34 passed / 454 deselected |
| `actionlint` | 外部阻塞：命令需审批未执行（T0 未改 `.github/workflows/checks.yml`，基线 workflow 已由 W3b 验证） |

### 待决

- 无（C6 前提已通过，无需调整屏障设计）。

## 下一步

- T1：实现 `replay()`（EXECUTE_REPLAY 复演，新 EvalContext + L3 snapshot + `pipeline.evaluate(mode=EXECUTE_REPLAY)`），红测先行。
