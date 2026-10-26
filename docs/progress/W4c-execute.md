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

## T2e/T2f + 协调者收尾（2026-10-26）：G8 ALLOW 路径转绿

- worker T2e（W4c-T2e-r1）与 T2f（W4c-T2f-r1）均用满轮数；T2f 已加 `replay_with_context`，并按 G4 同一表达式比对 plan_hash。
- 协调者逐层定位并修复剩余问题（execution.py / support.py / 测试，每处原因已核实）：
  1. `plans → EXECUTING` 必须先于插入 live job：`_protect_transition` 在 live job 存在时拒绝 plan 迁移，原顺序导致 409。
  2. 审计改用连接内 `audit.append`（关键字参数，同 G4）；原 `append_tx(conn, ...)` 签名错误。
  3. 5.2 写事务内按 `plans.scope_ids` 插入 `authorization_claims(CLAIMED)`（PRD §7.4；主键 scope_id 保证一个作用域只喂一个 plan；被他人 CLAIMED → 409 EXECUTION_IN_PROGRESS，已用过 → 409 STATE_CHANGED）。任务书原文只写「校验 claims」，未写由谁创建，属协调者疏漏。
  4. prepare/execute 回包不明、业务错误或结果非 EXECUTED 时，job 已持久 EXECUTING：一律 `mark_execution_unknown`（claim 保留）并返回 202，不再返回 503/409 把 job 滞留在 EXECUTING；STATE_CHANGED 屏障留 T4，对账留 T5。
  5. 夹具：w3b 的 gateway 时钟固定为 2026-10-24，而真实 L3 用真实时间核 deadline，导致 EXECUTION_DEADLINE_EXPIRED；在 allow_plan_ctx 中把种入原话的 expires_at_wall 锚定到 now()+30min。
  6. 红测本身的错：查卡状态误用 gateway `tables.cards`，改为 L3 `models.cards`。
- 新增 test_g8_unknown.py：L3 execute 回包丢失 → 202 UNKNOWN、job/plan UNKNOWN、claim 仍 CLAIMED、原话未消费、L3 plan_executions 为 0。
- 变异核验：去掉 claims 插入 → test_g8_allow 3 failed；去掉 EXECUTED 幂等返回 → 1 failed；UNKNOWN 分支改回 503 → test_g8_unknown 1 failed；均还原。
- 协调者复跑全部 exit 0：lint 347；test 2702/716 skip；test-gateway 2018/623 skip；gateway-db 17；wave1-state 231；w1 1688；w3b 33；w4b1 57；w4b2b 58；w4b2c 56；**test-w4c 11**；shared-runtime 79；contracts PASS；actionlint。
- 已知未覆盖（后续任务）：复演不等价导致 409 的路径尚无测试（T3/T4 会构造状态变化）；CONFIRMED（T3）；STATE_CHANGED 屏障（T4）；UNKNOWN 对账（T5）；并发/迟到/撤销（T6）；G5 与 request_id 终态重放（T7）。

## T3a（2026-10-26）：已确认审批夹具

- `approval_plan_ctx`（support.py）：真实 L3 + 真实 G4 得到收紧卡限额的 REQUIRE_APPROVAL/YELLOW 计划，再经真实 G9 取面板 view_hash、G6 确认；test_approval_fixture.py 断言 approvals 与 plans 均为 CONFIRMED、L3 为真实客户端。
- worker W4c-T3a-r1 用满 51 轮未提交（快照 refs/wip/W4c-T3a-r1），卡在 G9 KeyError。协调者定位：夹具未校验 G9 状态码（实为 403）；w3b 的 app_jwt 只有 `app` scope，G9/G6 需要 `approve`（改为按 w4b2b 写法直接 sign_jwt）；该 app 令牌的 jti 未种入 auth_sessions（401）。修后 1 passed；make test-w4c 12 passed、lint 348。

## T3b（2026-10-26）：G8 CONFIRMED 路径红测

- 新增 `tests/gateway/w4c/db/test_g8_confirmed.py`：3 条红测（G8 当前对 CONFIRMED 计划返回 503，故全部预期失败）：
  1. `test_g8_confirmed_executes`：G8 200 EXECUTED + 真实 L3 card single_limit_cents==100000 + approvals USED + plans EXECUTED + L3 plan_executions count==1。
  2. `test_g8_confirmed_view_changed_is_not_executed`：调 G8 前把 crd_zs_1 freeze → 409 STATE_CHANGED + L3 plan_executions count==0 + card limit 仍 500000。
  3. `test_g8_confirmed_rejects_approval_not_confirmed`：调 G8 前把 approvals status 改为 REJECTED → 409 + L3 plan_executions count==0。
- 运行：**3 failed / 12 passed**（3 条全部 503 DEPENDENCY_UNAVAILABLE，预期）；lint 349 files 通过。

## T3c + 协调者收尾（2026-10-26）：G8 CONFIRMED 路径转绿

- worker W4c-T3c-r1 实现了 CONFIRMED 分支（取最新 CONFIRMED 审批并加锁、过期/归属校验、复演须仍为 REQUIRE_APPROVAL 且 build_view 的 view_hash 逐字节等于审批、证据带审批字段、cap 取原话与审批有效期最小值、plan CONFIRMED→EXECUTING 与审批 CONFIRMED→USED 同事务），用满 51 轮未提交。
- 协调者收尾：
  1. 夹具时钟：G4 以 w3b 固定时钟（2026-10-24）生成审批有效期，真实 L3 按真实时间判 EXECUTION_DEADLINE_EXPIRED。新增 `_use_real_clock`（app.state.wall_now 与 approval_service 的时钟改为真实时间）与 `_anchor_to_real_time`（把该会话的 sessions/auth_sessions/utterances 时间重锚到 now），替换原先只改原话有效期的做法；read_records 的 observed_at 保持与 L3 一致（重锚它会导致 UNKNOWN_SENSITIVE_ARG，已验证并撤回）。
  2. 红测前提修正：原测试「执行前把卡冻结」不改变面板内容，且 L3 允许冻结卡改限额，执行是正确行为。改为把当前单笔限额由 500000 改为 300000：仍是黄级收紧（仍 REQUIRE_APPROVAL），但面板的「改前值」变化，view_hash 必然不同。另修错误体取值（`resp.json()["error"]["code"]`）。中途试过 80000（变为放宽），会先被「复演决策已非 REQUIRE_APPROVAL」挡住，无法单独证明 view_hash 守卫，故弃用。
- 变异核验：去掉 view_hash 比对 → test_g8_confirmed 1 failed；审批不迁到 USED → 1 failed；去掉审批状态过滤时由后续 CONFIRMED→USED 迁移兜底仍 409 且 L3 未执行（多层防护，接受）；均还原。
- 复跑全部 exit 0：lint 349；test 2702/720 skip；test-gateway 2018/627 skip；gateway-db 17；wave1-state 231；w1 1688；w3b 33；w4b1 57；w4b2b 58；w4b2c 56；**test-w4c 15**；shared-runtime 79；contracts PASS。
- 未覆盖（后续）：复演不等价时持久化新评估/新审批并在 details.decision 返回（T4）；STATE_CHANGED 屏障（T4）；UNKNOWN 对账（T5）；并发/撤销（T6）；G5/重放（T7）。

## T4a（2026-10-26）：ADR-008 STATE_CHANGED 屏障红测

- 新增 `tests/gateway/w4c/db/test_g8_fence.py`（3 条红测，只改测试不碰生产代码）。
- 3 条测试均使用 `allow_plan_ctx` + 包装 `l3.execute`（首次调用前通过 `ctx.l3_admin` 把 `state_versions` 中 `cus_zs` 的 version +1，使 L3 因版本不符返回 STATE_CHANGED）。
- **`make test-w4c`：3 failed / 15 passed**（15.63s），3 条新测均在 409 断言失败（G8 返回 202 UNKNOWN），预期红。
- **`make lint`：exit 0**（350 files formatted）。

## T4b（2026-10-26）：ADR-008 STATE_CHANGED 屏障转绿

- 仅改 `services/gateway/app/api/execution.py`：
  - 新增 `_handle_state_changed` 函数：begin_fencing → HTTP fence prepare（deadline_wall=FENCE_AT）→ acknowledge_fence → HTTP replay → append_evaluation（含 view_hash/view_json）→ build_view → create_for_evaluation（如 PENDING_APPROVAL）→ publish_paused_decision → 返回 409 STATE_CHANGED（details.decision 含完整新 Decision 与 approval_id）。
  - 新增 `_DECISION_TO_STATUS` 映射（ALLOW→EVALUATED、REQUIRE_APPROVAL→PENDING_APPROVAL、DENY→DENIED、REPLAN→REPLAN）。
  - execute 的 except 块拆分为：L3BusinessError STATE_CHANGED → `_handle_state_changed`；其他 L3BusinessError → `_unknown`；L3NotDelivered/L3OutcomeUnknown → `_unknown`。
  - 新增导入：`PrepareResult`（contracts.protocol）、`create_for_evaluation`（approval.service）、`latest_revision`（evaluation.repo）。
- 所有 `async with conn.begin()` 前加 `await conn.rollback()`（同主流程 L414 模式）。
- `build_view` 返回的 PlanView 以 `.model_dump(mode="json")` 序列化为 view_json。
- **`make test-w4c`：18 passed 零 skip/fail**（15.09s），原 3 条红测全部转绿、原 15 条保持绿。
- **`make test-wave1-state`：231 passed**；**`make test-w4b2b`：58 passed**；**`make test-w3b`：33 passed**；**`make test-gateway`：2018 passed / 630 skipped**；**`make lint`：exit 0**（350 files formatted）。
- 未改生产代码；不 push。

## T4c + 协调者收尾（2026-10-26）：屏障后以 attempt+1 恢复执行

- worker W4c-T4c-r1 先提交红测 ea2eee3，再实现恢复分支（job 为 PAUSED 时：新快照复演、对最新评估做 view_hash/plan_hash 等价判定、不插 claims/job、`resume_paused_execution` 得 attempt 2 后 prepare/execute/归档），用满 71 轮未提交。
- 协调者收尾：
  1. **红测前提修正**：注入方式「bump 客户状态版本」会让计划引用的读取记录过期（provenance.py:43–44 要求读取记录版本等于当前快照），屏障复演判 REPLAN——这是正确的 L2 行为，此类计划本就不能恢复。改为只 bump L3 时钟版本（`clock.version`，模拟时间不变）：L3 仍以 clock_version 不符返回 STATE_CHANGED，读取记录保持有效，恢复才有合法前提。
  2. 恢复分支在写事务前未结束读事务（连接已 autobegin）→ 照正常路径做法先 rollback 再 begin。
  3. 新增安全用例 `test_no_resume_when_state_change_makes_reads_stale`：状态版本变化 → 屏障发布 REPLAN → 第二次 G8 拒绝，execute 只调用 1 次、L3 无执行、claim 仍 CLAIMED。
- 变异核验：恢复后仍用 attempt 1 → test_g8_resume 2 failed；还原通过。
- 复跑全部 exit 0：lint 351；test 2702/726 skip；test-gateway 2018/633 skip；gateway-db 17；wave1-state 231；w1 1688；w3b 33；w4b1 57；w4b2b 58；w4b2c 56；**test-w4c 21**；shared-runtime 79；contracts PASS。
- 已知待改：恢复分支构造 ExecutionIdentity 时 `attempt=1` 写死，若 attempt 2 再次遇到屏障需从 job 行读取当前 attempt（T6 并发/迟到任务中处理并补测）。
