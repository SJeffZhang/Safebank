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

## T1a 红测（`replay()` 契约）——已提交，红

### 交付

- 新增 `tests/gateway/w4c/db/test_replay.py`（245 行，3 条红测），只改 `tests/gateway/w4c/**`，未动生产代码。
- 复用 w3a `_serve_l3` 模式拉起真实 L3（`install_core`/`install_obligations` + `create_app` + uvicorn 线程 + 真实 `L3Client`）；余额变更走 `post_transfer` + `persist`（与 fixture 同一持久化路径）。

### 3 条红测

1. `test_replay_uses_execute_replay_mode`：monkeypatch `pipeline.evaluate`，断言 `replay` 以 `mode=EvalMode.EXECUTE_REPLAY` 调用并返回其产出的 `Decision`。
2. `test_replay_fetches_fresh_snapshot`：先让 `ctx` 缓存一份快照（`ctx.bank_state()`），再经真实 L3 把 `acc_zs_chk` 余额从 600000 改为 555000（改后经 `l3.snapshot` 探针确认），断言 `replay` 内部给 `evaluate` 的 ctx 取到的是**新**余额（证明不复用 `_lazy_state` 缓存）。
3. `test_replay_uses_fresh_context`：把 `ctx` 的 provenance/snapshot/preflight/workflow/decision 填成旧值，断言 `evaluate` 收到的 ctx 这五个槽位全部为 `None`（新对象或已清空）。

### 运行结果（`make test-w4c`）

- **3 failed, 1 passed**（3.95s）：三条新测均因 `replay()` 抛 `NotImplementedError` 失败（预期红）；既有 `test_c6_fence_prepare_premise` 通过。
- 三条新测在到达 `await replay(ctx)` 前的前置（真实 L3 启动、`bank_state()`=600000、余额变更与 555000 探针、旧槽位填充）均真实执行通过，失败点唯一是未实现的 `replay`。

## T1b 实现 `replay()`——完成，绿

### 交付

- 只改 `services/gateway/app/execution/replay.py`（生产文件仅此一个）。
- `replay(ctx)`：新建 `EvalContext`，只沿用身份与输入字段（`request`、`identity`、`authorization`、`plan_draft`、`plan_id`、`origin`、`customer_id`、`session_id`、`profile`、`wall_now`、`conn`、`l3`、`audit`）；不复制任何阶段结果（`provenance`/`intent`/`snapshot`/`preflight`/`workflow`/`decision`）、`findings`/`stage_status`/`short_circuit` 及 `_lazy_state` 快照缓存。
- 以 `pipeline.evaluate(fresh, mode=EvalMode.EXECUTE_REPLAY)` 调用（经模块属性 `pipeline.evaluate`，保证 monkeypatch 与真实调用一致），返回其产出的 `Decision`；若未产出 `Decision` 则抛 `RuntimeError`，不伪造。
- 未改动 `pipeline/*` 与其他生产文件；未改测试断言。

### 运行结果

| 命令 | 结果 |
|---|---|
| `make lint` | exit 0（341 files formatted） |
| `make test-w4c` | **4 passed**（3.69s，3 条红测转绿 + 既有 C6 前提验证） |
| `make test-w3b` | 33 passed |
| `make test-gateway` | 2018 passed / 616 skipped |

### 下一步

- T2：G5 计划状态（/plans/{id}/execute 状态前置校验）与 G8 执行，待 T1 合入后继续。

### T2a：真实 L3 + 真实 G4 的 ALLOW 夹具（2026-10-26）

- 新增 `tests/gateway/w4c/db/support.py`（`allow_plan` 夹具：w3b `build_app` 后把 `app.state.l3` 换成真实 `L3Client`，obligations 种子 `cus_zs`/`acc_zs_chk`/`crd_zs_1`，GREEN `freeze_card` 计划）与 `tests/gateway/w4c/db/test_allow_fixture.py`（`test_real_l3_allow_plan`：断言 decision==ALLOW、`app.state.l3` 为 L3Client 且非 FakeL3Client、plans 表该 plan status==EVALUATED）；`conftest.py` 补 `app_config` 导入。
- 命令：`make test-w4c` → **5 passed**（0 skipped，6.73s）。
- 未改生产代码 / w3a / w3b 测试；不写 G8。

### T2b：G8 ALLOW 红测（2026-10-26）

- `support.py`：新增 `AllowPlanContext` dataclass 与 `allow_plan_ctx()` async context manager（复用 `_build_allow_plan` 的完整步骤，yield `test_app`/`headers`/`plan_id`/`utt_id`/`db_url`/`l3_admin`/`response`，退出时 dispose L3 引擎）；`_build_allow_plan` 改为调用 `allow_plan_ctx` 保持 `test_allow_fixture.py` 不变。
- `test_g8_allow.py`：3 条红测——`test_g8_allow_executes_once`（G8 200 + real L3 crd_zs_1 FROZEN + 幂等）、`test_g8_allow_job_and_plan_terminal`（execution_jobs l3_attempt=1/EXECUTED + plans EXECUTED）、`test_g8_allow_consumes_claims`（utterances CONSUMED）。
- 运行：**3 failed / 5 passed**，全部失败因 G8 stub 返回 503 `DEPENDENCY_UNAVAILABLE`。所有测试期待真实 G8 实现后转绿。

## T2d（2026-10-26）

- 0003_plan_draft：`plans.plan_draft JSONB NULL`（只增，不回填）；`GATEWAY_REVISION` 改为 0003；G4 插入 plans 时写入 `PlanDraft.model_dump(mode="json")`；test_migration / wave1 test_schema 版本断言与 test_ddl_drift 增量登记同步。
- 新测试 test_plan_draft.py 2 passed（G4 持久化原始计划且 arg_sources 一致；0002 库升级后旧行保留、plan_draft 为 NULL）。
- worker W4c-T2d-r2 提交 2c3057d/4f6985b/d63f427/9b801d0 后用满 51 轮；协调者收尾 ruff（import 排序、超长行、格式）并复跑：lint 346；test-gateway-db 17；test-wave1-state 231；test-w3b 33；test-gateway 2018/622 skip；make test 见提交；test-w4c 仅 G8 三条红测失败（预期，T2e 实现）。
- 注意：长期栈 gateway 库当前为 0002，合入后需协调者在全栈阶段 `make up`（引导迁移至 0003）再验收。
