# 任务书 W4：审批与执行

分支 `feat/l2-execution`。先完整阅读并遵守 `docs/plans/briefs/wave1-common.md`。

## PRD 章节
§7.4（claims 与授权消费）、§7.8 末段（锁连接规则）、§7.10 全部、§8.5（prepare / execute / reconcile 与 attempt）、§9.1（STATE_CHANGED 的 details）、§9.5 中 G5–G10、G17、PlanView、ExecutionResult、§9.8、§10.2（authorization_evidence 判别联合）。

## 可以改的文件
`services/gateway/app/api/approvals.py`、`api/execution.py`（G5–G10、G17）、`approval/**`、`execution/**`（**除** `execution/replay.py`，它归 W3）、`locks.py`、`tests/gateway/w4/**`、`docs/progress/W4-execution.md`、Makefile 末尾追加 `test-w4`。

## 交付
1. `approval/planview.py: build_view`：PlanView 与 view_hash（覆盖目标、金额、memo、频次、逐步余额、风险、不可逆标记、揭示版本；不含倒计时）。步骤标题、不可逆、是否需 OTP 从 `policy.registry` 取。
2. `approval/service.py: create_for_evaluation`：创建审批（binding_mac 绑定 plan_hash、evaluation_id、versions、clock_version、policy_version、profile、view_hash、expires_at_wall）；每会话只有 1 个 PENDING，新审批使旧的 SUPERSEDED；ALLOW 时创建策略审批。
3. G9、G17；G6 确认（view_hash、OTP、risk_ack，严格按PRD §9.5既有wire）；G7 拒绝；G10 OTP（HMAC 存储、2 分钟、重发 ≤ 3 次间隔 30 秒、错 5 次锁）。OTP 明文不进日志与响应。

协调裁定（2026-10-23）：原任务书额外要求的disclosure_token不在PRD G6或PlanView中，删除该冲突要求，不扩冻结schema。揭示全文/版本仍必须纳入view_hash及审批绑定，risk_ack按PRD校验；G9不得在字典中偷偷附加token字段。
4. `locks.py: customer_lock`：独占 `lock_conn`、`SET lock_timeout = '5s'`、按客户 ID 排序加锁、超时 409 `EXECUTION_IN_PROGRESS`、`asyncio.Semaphore(GATEWAY_MAX_EXECUTIONS)`；必须能被 W5 的 S1 与 demo 入口复用。
5. G8 执行编排：客户锁 → 复核审批与绑定 → `authorization_claims` 占用 → 调 W3 的 `replay`（W3 合入前用替身）→ 等价唯一判据 view_hash → 构造 authorization_evidence → L3 prepare / execute（attempt、deadline_cap）→ L3 STATE_CHANGED 时内部复演最多 2 次并 attempt+1 → 409 带 `details.decision` → 202 UNKNOWN；`execution_jobs` 状态用 `state.transition`。G5 查询。
6. UNKNOWN 恢复工作器：查询 → reconcile（CANCELLED 墓碑）→ 只有拿到 COMMITTED / CANCELLED 才终结；UNKNOWN 重试沿用原 attempt。

## 退出条件
`make lint`；`make test-gateway`；`make test-gateway-db`；`make test`；`make test-w4` 全部通过。必须有：同一作用域并发双花只成功一次；确认后篡改计划、plan / approval 错配、复用审批都失败；STATE_CHANGED 重新审批路径；UNKNOWN（丢回包、commit_then_drop、延迟 execute 晚于 reconcile）恢复不重复记账——用 fake L3 的故障注入覆盖，另用 `make test-gateway-db` 的临时库验证 gateway 侧状态一致；OTP 状态机测试。
