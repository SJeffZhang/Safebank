# 任务书 W3：快照、预演、工作流、决策与 G4

分支 `feat/l2-decision`。先完整阅读并遵守 `docs/plans/briefs/wave1-common.md`。

## PRD 章节
§5.0（阈值）、§7.2（⑤–⑨ 与 plan 持久化）、§7.7（含 7.7.1 与 7 天预留算法）、§7.8、§7.9、§7.10 第 4–5 项（复演语义）、§9.5 G4 与 Decision、§9.8、§10.1（profile 与 shadow B1）。

## 可以改的文件
`services/gateway/app/api/plans.py`（G4）、`pipeline/snapshot.py`、`pipeline/preflight.py`、`pipeline/workflow.py`、`pipeline/decision.py`、`pipeline/audit.py`（⑨）、`evaluation/repo.py`、`execution/replay.py`、`workflow/**`（新建）、`services/gateway/policies/thresholds.yaml`、`policies/workflow.yaml`（新建）、`tests/gateway/w3/**`、`docs/progress/W3-decision.md`、Makefile 末尾追加 `test-w3`。

## 交付
1. ⑤ 快照：复用 `ctx.bank_state()` 的同一份快照，钉住 versions / clock_version，不重复拉取；app_form 计划没有 PlanDraft 时，用 AppFormPlanRequest 生成的 steps 取快照（评审 R5）。
2. ⑥ 累积预演：调用 `bankcore.apply_plan`（不写业务算术）；违规按 §7.7 表区分 L3 强制与 L2 策略；7 天预留算法（`max_outflow`、SCHEDULED_SHORTFALL，扣费时刻 06:00）；B1 模式为非累积的单步预演。
3. ⑦ 工作流：§7.8 全表规则；序列匹配器 `workflow/sequence.py`（RISKY_SEQUENCE 顺序不限、含无卡储蓄）；窗口 sim / wall 区分（DUPLICATE_PLAN、REJECTED_RETRY 用 wall）；冷静期；数据源为快照中的 `ops_7d` 与 gateway `workflow_events`。
4. ⑧ 决策：优先级 DENY > REPLAN > REQUIRE_APPROVAL > ALLOW；风险等级只升不降；resolver 与 evidence_refs；hint 生成只收缩（§7.9 白名单条件）；`GATEWAY_PROFILE` 各档开关（`Stage.enabled`）；SHADOW_B1 只在内存比较。
5. ⑨ 审计阶段：用 `audit.append_tx` 记录评估。
6. G4 组装：按调用方判别 PlanDraft（A）/ AppFormPlanRequest（F）；plan_id 分配；request 幂等；`plans` 落库、`append_evaluation`；REQUIRE_APPROVAL 时调用 W4 的 `approval.service.create_for_evaluation`（W4 合入前是占位，你的测试用替身）。
7. `execution/replay.py: replay`：执行前复演入口，用**新的** `EvalContext` 跑 `pipeline.evaluate(mode=EXECUTE_REPLAY)`，返回 Decision 与 view 相关数据供 W4 判断等价。

## 退出条件
`make lint`；`make test-gateway`；`make test-gateway-db`；`make test`；`make test-w3` 全部通过。必须有：A1 拆分升红；A3（含换序、无卡储蓄变体）DENY；PRD §12 第 2 幕 REPLAN 且 hint 为「赵六最多 2,955 元」；SCHEDULED_SHORTFALL；B0 / B1 / B2 三档结论差异测试；预演结果与 L3 执行一致（对 fake 与真实 snapshot 形状）。
