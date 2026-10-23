# W3a：快照、预演、工作流、决策阶段 — 进度与验收

2026-10-24。实现 worker（DeepSeek V4 Pro，无头 Claude Code）。工作树 `codex/w3a-stages`，基线 main `383cd6f`（W2b #18 已合）。只本地提交，未 push/PR/merge。

## 做了什么

| 文件 | 内容 |
|---|---|
| `services/gateway/app/pipeline/snapshot.py` | ⑤ 发布 `ctx.bank_state()` 缓存到 `ctx.snapshot`，钉住 versions/clock_version/sim_now；不重复拉取 L3、不写 L3 |
| `services/gateway/app/pipeline/preflight.py` | ⑥ 唯一 `bankcore.apply_plan` 累积预演；B1 从初始快照逐步独立（仅 `resolved_steps` 结构化 payee 引用）；§7.7 决策效果映射（INSUFFICIENT_FUNDS/CARD_*/DAILY_OUTFLOW_LIMIT→对应 REPLAN/DENY）；AMOUNT_ABOVE_YELLOW_CAP（单笔红线，B1/B2 都做）；IRREVERSIBLE_STEP（挂失/申购）；7 天预留 `projected_shortfall`（SCHEDULED_SHORTFALL、负 max_outflow 有符号、缺义务 fail-closed、已有 DENY 不被 503 遮盖） |
| `services/gateway/app/pipeline/workflow.py` | ⑦ 跨会话工作流：L3 `ops_7d` + gateway `workflow_events(COMMITTED)` 按 event_id 去重；SPLIT_TRANSFER/CUMULATIVE_OUTFLOW/FIRST_TRANSFER/NEW_PAYEE/RISKY_SEQUENCE（顺序不限、含无卡储蓄）/DUPLICATE_PLAN/REJECTED_RETRY；冷静期读 `cooldowns` 并命中后持久化；业务窗口按 sim、防重试窗口按 wall |
| `services/gateway/app/pipeline/decision.py` | ⑧ DENY>REPLAN>REQUIRE_APPROVAL>ALLOW；风险只升不降；replan hint 只收缩（金额减小、不新增动作/目标/源账户；TAINTED/UNKNOWN/INTENT_SCOPE/INJECTION 时 `replan_hints=[]`）；B2 影子计算 SHADOW_B1（同一快照、内存、不写库/审计/审批/冷静期） |
| `services/gateway/app/workflow/{__init__,sequence}.py` | 纯函数序列分析：merge_committed 去重、窗口过滤、risky_sequence/split_transfer/cumulative_outflow |
| `services/gateway/policies/thresholds.yaml`、`workflow.yaml` | 阈值与窗口（阈值按 sim，防重试窗口按 wall） |
| `tests/gateway/w3a/**` | 27 单元 + 5 真实 PG/HTTP 用例 |
| `Makefile` | 末尾追加 `test-w3a`（GATEWAY_DB_TESTS=1 + OBLIGATIONS_DB_TESTS=1） |
| `.github/workflows/checks.yml` | p1-postgres 追加 `make test-w3a`（保持 job 名，协调者已授权） |

## 退出命令实际结果

| 命令 | 结果 |
|---|---|
| make lint | ruff check + format --check 通过（256 files） |
| make test-gateway | 1917 passed, 476 skipped |
| make test-gateway-db | 17 passed（真实 tmpfs PG16） |
| make test | 2168 passed, 536 skipped |
| make test-w3a | **32 passed 零 skip/xfail**（含真实 bank PG + 真实 L3 snapshot HTTP + 真实 gateway PG） |
| make test-w2 | 246 passed 零 skip/xfail |
| make test-obligations | 30 passed（既有 FK 环 SAWarning 保留） |
| make contracts-check | PASS: contracts v1 match frozen schemas |

真实 PG/HTTP 证据：`test_real_preflight.py` 用真实 bank PG（tmpfs）+ 真实 uvicorn L3 `/internal/snapshot` HTTP，证明余额/版本/义务输入与 scene 2 数字（600000 余额、reserve 4500、第 2 步 hint 295500）；`test_workflow_db.py` 用真实 gateway PG 证明 DUPLICATE_PLAN/REJECTED_RETRY 的 wall/sim 分离与 RISKY_SEQUENCE 冷静期落盘。

## 边界与未覆盖（不属本组件）

- 完整 G4/落库/复演/审批/execute 仍待 W4b → W3b → W4c（本组件只做纯阶段，不伪造 G4）。`Decision.evaluation_id`/`revision` 目前是 `evl_<plan_id>`/1 占位，由 W3b 真实分配。
- 支持工具仅 P1 子集（transfer/add_payee/卡类）；schedule_transfer/cancel_*/subscribe/redeem/split/assessment 在预演返回 `TOOL_NOT_ALLOWED`（DENY），未假实现成功。
- FIRST_TRANSFER/NEW_PAYEE 等跨会话规则按 §7.8 属工作流阶段，B1（禁用 workflow）不评估（§10.1 B1「单步校验」只含单笔阈值与独立预演）。
- shadow B1 只在 profile=B2 且 mode=EVALUATE 时计算；复用同一快照、SHADOW_B1 模式不写库/审计/审批/冷静期。

## 待决 / 需协调者注意

1. **PreflightResult 字段扩展**：按 wave1-common「各阶段结果类只能由拥有者新增字段」，W3 给 `PreflightResult` 新增 `steps/balances/holdings/scheduled_reserve_cents/shortfall_before_cents/shortfall_after_cents/hint_max_outflow`（均带默认值）。`WorkflowResult` 未改。
2. **REJECTED_RETRY 的 action+target 签名**：持久计划的卡类 LOOSEN/TIGHTEN 方向无法从 `resolved_steps` 静态重建，`_step_signature` 对卡类回退到工具名（方向并入待 W3b 有真实 decision 持久化后再细化）；转账方向是精确的。
3. **REJECTED_RETRY「原 DENY 条件仍成立则 DENY」**：实现为升红 REJECTED_RETRY + 当前评估重跑全部 DENY 规则（冷静期/危险序列自然再次 DENY），不单独回放旧 finding。
4. **冷静期写入时机**：RISKY_SEQUENCE 命中即在评估阶段写 `cooldowns`（§7.8 冷静期从 DENY 起算）；W3b 需把它纳入 G4 同一事务原子提交。
5. **IRREVERSIBLE_STEP 与未实现工具的叠加**：subscribe_product 当前既 TOOL_NOT_ALLOWED(DENY) 又 IRREVERSIBLE_STEP(RED)，领域写实现后仅保留 IRREVERSIBLE_STEP。
6. `scheduled_reserve_cents` 为展示用预留（before 投影 total_reserve）；失败计划只做 best-effort 展示，绝不因预留 503 遮盖已有 REPLAN 结论。
