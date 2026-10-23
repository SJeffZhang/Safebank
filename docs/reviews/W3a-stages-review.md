# W3a-stages 独立评审（首次）

候选 `df67cc0`（相对 `80485ef`），评审者 DeepSeek V4 Pro 只读 worker。已读 W3a-stages 任务/进度、PRD §7.7/§7.8/§7.9/§10.1/§10.3、wave1-common、W3-W4-integration-order；逐文件实读 pipeline 四阶段、workflow/sequence、policies、tables、bankcore `apply_plan`/`resolved_steps`/`projected_shortfall`。已实际复现 2 个 P0/P1 缺陷与 1 个 P2 展示缺陷。非 DB 单元 27 passed（复跑确认）。

## 结论：REQUEST_CHANGES

## P0

**P0-1 决策阶段在 preflight 被跳过时解引用 `ctx.preflight=None` 崩溃，无法产出 DENY Decision（500）。**
- 证据：`decision.py:127-144` `_preflight_view` 无 None 守卫直接 `pre.steps`；`context.py:151` `preflight: PreflightResult | None = None`，且只有 `PreflightStage.run`（preflight.py:88）才赋值；`pipeline/__init__.py:40` `SKIP_AFTER_DENY={"snapshot","preflight","workflow"}` 与 `context.py:62-69` `DISABLED_BY_PROFILE`（B0/B2_NO_PREFLIGHT 禁用 preflight）。
- 失败路径（已实跑复现）：
  1. `profile=B0` 或 `B2_NO_PREFLIGHT`：`DecisionStage.run` → `AttributeError 'NoneType' object has no attribute 'steps'`。
  2. 任意 ①–④ 硬 DENY 短路（NOT_FOUND/TAINTED_SENSITIVE_ARG/ORIGIN_NOT_ALLOWED/CONTRACT_INVALID 等）：全量 `evaluate()` 复现 `CRASH AttributeError`——即注入/污点 DENY 不会返回 DENY Decision 而是 500，安全网关核心路径失败。
- 修复建议：`_preflight_view` 对 `ctx.preflight is None` 回退 `PreflightResult()`（空 steps/balances），或 decision 阶段在 preflight 缺失时构造默认 `PreflightView(executable=True)`。测试补 B0/B2_NO_PREFLIGHT 与早 DENY 全 pipeline 用例。

## P1

**P1-1 SCHEDULED_SHORTFALL 的 replan hint 可放大转账金额（违反 §7.9「hint 只减不增」），且产出自相矛盾的 hint。**
- 证据：`preflight.py:215` 把 SCHEDULED_SHORTFALL 一律归到第一个 transfer 步（`next(s.step_id for s in ctx.steps if s.tool=="transfer")`）；`preflight.py:249-268` `_failed_step_max` 返回该步**之前** shadow 的 `max_outflow_cents`；`decision.py:185` `transfers[i]=transfer.model_copy(update={"amount_cents": maximum})` **无 `maximum < old_amount` 守卫**。
- 失败路径（已实跑复现）：available=600000、reserve=4500（max_outflow=595500），两步转账 s1=300000（pye_b）、s2=298000（pye_ext）。预演成功但 shortfall 增大 → finding 归 s1、`hint_max_outflow["s1"]=595500`；决策 hint 输出 `最多 5,955 元 amounts=[595500, 298000]`，把 s1 从 300000 **放大**到 595500。采纳后总流出 893500>600000 又会 INSUFFICIENT_FUNDS——hint 既扩权又不可执行。
- 修复建议：`_hints` 强制 `if maximum >= old_amount: return ()`（或 continue 该 finding）；同时 SCHEDULED_SHORTFALL 应归因到真正超 max_outflow 的步并只做减小。INSUFFICIENT_FUNDS/CARD_* 路径因失败步必超剩余上限，天然是收缩，不受影响。

## P2

**P2-1 `_format_cents` 用 float 除金额（`decision.py:67-70`）。** `cents/100:,.2f` 违 §0.1「金额禁止 float」；大额失真已实跑复现：`9999999999999999 → 99,999,999,999,999.98 元`（应为 .99）。建议整数 `cents//100` + `cents%100:02d`。金额日常有界、属展示层，故 P2。

**P2-2 工作流阈值硬编码，thresholds.yaml 相关键是死配置。** `workflow.py:293-310,321` 硬编码 `3`/`500_000`/`1_000_000`/`30分钟`；`thresholds.yaml` 的 `split_transfer_min_count/split_transfer_min_total_cents/cumulative_outflow_24h_cents/new_payee_first_transfer_minutes` 全仓库无引用（已 grep 证实）。违反「阈值放 YAML」，改 YAML 不生效。建议由 workflow.py 加载这些键。

**P2-3 DUPLICATE_PLAN 查询缺 `id != ctx.plan_id` 且窗口锚在创建时间。** `workflow.py:130-143` 未排除自身 plan（对比 `rejected_rows` 的 `t.plans.c.id != ctx.plan_id`，workflow.py:153），已 EXECUTED 的同一 plan 复演会自命中 DUPLICATE_PLAN；且 10 分钟窗按 `created_at_wall` 而非「已执行」时间（PRD §7.8），慢审批快执行会漏报。当前 G4 未实现、暂不可达，故 P2，W3b 前应修。

## 未确认（非发现）

- shadow B1 在共享 `ctx.conn`/`ctx.authorization` 上重跑 ③④：已查 provenance.py/intent.py 无 conn.execute/insert/update，但 taint 辅助函数未穷举，写行为以 W2 既有语义为准。
- L3 `ops_7d` 仅含已提交 operation_events 的假设未在本轮核实（依赖 L3 state 装载，超出 W3a diff）。

## 复核范围说明

真实 PG/HTTP 门禁（test_real_preflight / test_workflow_db）已读且由协调者另跑完整退出；本轮仅定点复现上述缺陷。评估 id/revision 占位（`decision.py:93-94`）与 G4 未实现已在进度明示，不判为缺陷；不支持领域写返回 TOOL_NOT_ALLOWED 与 B1 单步语义符合 PRD。

---

## 修复轮复核（候选 b5f8fa7，df67cc0..HEAD）

结论：**REQUEST_CHANGES**（P0-1 / P1-1 / P2-1 / P2-2 已闭合；P2-3 锚点仍错，残留 1 项 P1）。

### 已闭合

- **P0-1 闭合**。`decision.py:79-103` 不再回退 `bank_state()`；`_preflight_view`（decision.py:131-137）对 `pre is None` 返回 `PreflightView(executable=not ctx.has_hard_deny())`；`_hints`/`_shadow_b1` 在 `state is None` 时跳过。亲跑全 pipeline 复现：B0/B2_NO_PREFLIGHT（pye_b 非 DENY）→ 稳定 Decision、versions/clock 正常；B2/B0 + `pye_nonexistent` 早 DENY → `DENY`、`executable=False`、versions={}/clock=0，无 500、无 L3 依赖。`test_pipeline_profiles.py` 六 profile + 早 DENY + 无 L3 全绿（38 非 DB passed 复跑）。
- **P1-1 闭合**。`preflight.py:230-246` `_offending_shortfall_step` 用共享 `apply_plan` 逐前缀找首个越预留边界步；`decision.py:194-197` 加 `maximum >= old_amount → return ()`。亲跑两步 300000+298000：finding 归 `s2`、hint `{"s2":295500}`、hint scope `[300000,295500]`（只减 s2）；`test_shrunk_hint_repreflights_successfully` 证 shrink 后计划再预演通过。
- **P2-1 闭合**。`_format_cents` 整数 `//`/`%`（decision.py:67-72），`9999999999999999 → 99,999,999,999,999.99 元`。
- **P2-2 闭合**。workflow.py 经 `_load_thresholds()` 读 thresholds.yaml（split count/total、cumulative、new_payee），构造器支持注入；`test_workflow_split_thresholds_are_loaded_from_config` / `test_new_payee_window_is_configurable` 证改配置改变行为。

### 未闭合（残留 P1）

**P1-R1 DUPLICATE_PLAN 的 wall 锚点用 `execution_jobs.created_at_wall`，是 G8 开始时间，不是「已执行」时刻；慢执行 >10 分钟刚提交后会漏 duplicate。**
- 证据：`workflow.py:145-147` 用 `t.execution_jobs.c.created_at_wall` 做 10 分钟窗。而该列是 job 首次 INSERT 的 G8 开始时间（当前 main 尚无任何 execution_jobs INSERT，W4c 才写；且 `resume_paused_execution` 对同 plan_id 同一行只 UPDATE `l3_attempt`，`created_at_wall` 恒为 attempt-1 起始）。EXECUTING/UNKNOWN/PAUSED 停留 >10 分钟再 COMMITTED 时，`created_at_wall` 已过窗，刚提交后的同 steps_hash 计划不会命中 DUPLICATE_PLAN。
- 可信 wall 来源已存在，无需迁移：`state._job_update`（state.py:367 `.values(**values, updated_at_wall=func.now())`）在**每次**状态迁移都刷新 `updated_at_wall`，而终态归档 `archive_execution_terminal`（state.py:707-735）是终态 job 的**最后一次** `_job_update`（EXECUTED/FAILED/CANCELLED 无出边，state.py:130-132），故 `execution_jobs.updated_at_wall` = 终态提交墙钟。`result.committed_at` 确为 sim_now（L3 execution.py），不能混用（worker 此判断正确）。
- 修复建议：`workflow.py:145-147` 改 `t.execution_jobs.c.updated_at_wall`（>= `wall_now-10min` 且 <= `wall_now`）。无需新列/迁移、不越 W3 文件所有权。测试 `_insert_executed_plan`（test_workflow_db.py:43-78）把 `created_at_wall` 与 `updated_at_wall` 都设成 `executed_wall`，未覆盖「created 旧 / updated 新」场景，应补一条：`created_wall=WALL-30min`、`executed_wall=WALL-2min` 仍命中 DUPLICATE_PLAN。

### 未确认

- `updated_at_wall` 用 DB `func.now()`（事务开始墙钟），与 `ctx.wall_now()`（应用时钟）存在跨组件时钟偏差；演示同机可忽略，多机部署需统一时钟源（属 W4c/部署，非本组件）。

---

## 第二修复轮复核（候选 438c035，b5f8fa7..HEAD）

结论：**APPROVE**（P1-R1 已闭合，无新增残留 P0/P1/P2）。

### P1-R1 闭合核实

- 锚点改 `execution_jobs.updated_at_wall` 且 `job.status == "EXECUTED"` 双状态过滤（workflow.py:141-147），排除自身/跨客户，上下界 `[wall_now-10min, wall_now]`。
- 语义链正确：`state._job_update`（state.py:367 `updated_at_wall=func.now()`）在每次迁移刷新；`archive_execution_terminal`（state.py:707-735）是 EXECUTED 终态 job 的最后一次 `_job_update`，EXECUTED/FAILED/CANCELLED 无出边（state.py:130-132）。plan/job 终态同事务同值（`_finish_terminal`），故 `plans.status=EXECUTED ⟺ job.status=EXECUTED`，双过滤不产生假阴性。
- 真实 PG 用例覆盖齐：`test_duplicate_plan_anchors_on_terminal_time_wall`（created 旧 30min / updated 新 2min 仍命中——慢执行不漏）；`test_duplicate_plan_excludes_self_and_boundaries`（自身、跨客户、非执行状态、plan EXECUTED 而 job UNKNOWN 状态不一致、created 新 updated 旧不命中、过期 11min、未来 +5min）；`test_terminal_cas_persists_updated_at_wall_and_rejects_retry` 直接调用共享 `state.archive_execution_terminal` 证落盘 `updated_at_wall` 非空且终态重试 `GatewayError(STATE_CHANGED)` 不刷新时间。
- 未为测试改共享 state/迁移：`git diff b5f8fa7..438c035` 仅 `workflow.py`、`test_workflow_db.py`、`docs/progress` 三文件，未触碰 `state.py`/`tables.py`/`migrations`。测试直接 INSERT `updated_at_wall` 属合法造数，未改生产写路径。

### 回归保持

decision.py/preflight.py 本轮零改动（P0-1/P1-1/P2-1/P2-2 不受影响）；非 DB w3a 全量复跑 38 passed（含 test_pipeline_profiles / 扩金额 / 阈值注入 / 整数格式）。

### 未确认（非发现，沿用上轮）

- `updated_at_wall=func.now()`（DB 事务开始墙钟）与 `ctx.wall_now()`（应用时钟）跨组件时钟偏差；演示同机可忽略，多机部署需统一时钟源（属部署/W4c，非本组件）。
