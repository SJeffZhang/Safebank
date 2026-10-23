# W2b 真实预留接线独立只读评审

日期：2026-10-24。范围：`ff244bb..252455c`（codex/w2b-reserve-integration，HEAD 252455c）。只读评审，唯一写入本报告。亲跑 `tests/gateway/w2 --ignore tests/gateway/w2/db` = **186 passed**（2.42s）；`test_reserve_default.py + test_derived_cancelled.py` = 8 passed。DB 专项（test_reserve_integration.py）依赖 docker 临时 PG，本 worker docker 被拒，未亲跑；结论基于源码静态核对 + 协调者六退出/CI 记录。

## 逐项核对

1. **默认实例真实 bankcore 接线** ✓：provenance.py:90–94 `self.reserve = projected_shortfall if reserve is None else reserve`；默认实例直接使用唯一 `safebank_bankcore.projected_shortfall`，测试显式注入仍保留，生产不再默认 None。✓
2. **None/coverage/无目标账户 fail-closed、不裸 KeyError** ✓：`_reserve_max_outflow`（:287–304）用 `report.accounts.get(account_id)` 取 None → 503；`except ValueError`（:291）把 `projected_shortfall` 的「obligations 未加载 / coverage 不一致 / 数据非法」ValueError 转 503 并 `raise ... from exc`（保留链、非宽 except；KeyError/TypeError 等编程错误会传播而非吞掉）。bankcore reserve.py 确认所有 incomplete/invalid 信号均为 ValueError。✓
3. **负 max_outflow 不 clamp** ✓：bankcore `max_outflow_cents = amount - reserves`（无 clamp），gateway `_reserve_max_outflow` 原样返回，比较用 `type(expected) is int and expected == value`，负值不被改写成 0。✓
4. **295500/295501 真PG + 实际L3 HTTP + 版本/时刻拒绝** ✓：test_reserve_integration.py 用真实 bank PG（obligations_db）+ 真实 uvicorn L3（create_app + service_token）→ 默认 `ProvenanceStage()` → 295500 derived / 295501 unknown / 二次转账 bump 版本后 unknown / 推进模拟时钟后 unknown。默认实例非 fake reserve。✓
5. **cancelled_monthly_fees 逻辑** ✓：`_derived`（:257–285）去重（`len(cancellations) != len(selected)`）、逐订阅 MONTHLY/ACTIVE/同账户、目标 `_is_own_savings`（:306–311，同客户 SAVINGS）、WEEKLY/跨账户/重复拒绝；test_derived_cancelled.py 4 项覆盖。✓
6. **CI/Makefile/无 app 导入污染** ✓：Makefile test-w2 仅加 `OBLIGATIONS_DB_TESTS=1`；checks.yml p1-postgres 追加 `make test-w2`；db/conftest 仅 re-export `obligations_db`（env 门控，未设置时 skip，import 无副作用）。`from app import ...`（bank-backend）与 `services.gateway.app`（gateway）命名空间不冲突（pyproject pythonpath 已含 services/bank-backend，S3a 建立）。✓

## 发现

P0：无。

P1 — **503 可遮盖已有 hard DENY（违反任务书「已有 hard DENY 不被后续 503 遮盖」）**
- 证据：provenance.py:255 `_derived` 对 `available_less_7d_charges` 调 `_reserve_max_outflow`；:292/:299 在 obligations 不完整或目标账户缺失时 `raise GatewayError(DEPENDENCY_UNAVAILABLE, 503)`。该调用位于逐 step/逐 param 循环内（:198），而 `run()` 仅在入口 :100 检查一次 `has_hard_deny()`，循环内不再检查。
- 失败场景：两步骤计划 `[transfer(memo 来自 untrusted read) + subscribe_product(amount_cents=derived available_less_7d_charges)]` 且 obligations 未加载/覆盖不一致 → step1 已加入 `TAINTED_SENSITIVE_ARG/DENY`（:208–209），step2 派生金额抛 503 并传播出 run/evaluate，DENY 被 503 取代、findings 丢弃、Decision 未产出；L1 会当临时错误重试，DENY 信号丢失。
- 修复：`_derived` 的 `available_less_7d_charges` 分支在调 `_reserve_max_outflow` 前 `if ctx.has_hard_deny(): return "unknown"`（DENY 已在 findings，无需再算预留）；补一条「先 DENY 后 derived」回归测试。

P2 — **cancelled_monthly_fees 的真实持久化视图仅以合成 SubscriptionView 验证**：test_derived_cancelled.py 用 `replace_state(subscriptions=SubscriptionView(...))` 直接构造视图，未走 S3a loader→真实义务数据；真实 loader→SubscriptionView 由 S3a 覆盖，但 W2b 自身未证明「真实持久化视图」路径。非代码缺陷；建议补一条真实 PG 用例或明确登记该覆盖由 S3a 提供。

## 未确认

- test_reserve_integration.py（真实 PG/HTTP）本 worker 未亲跑（docker 被拒）；其正确性依据静态核对 + 协调者六退出/CI。CI 中 `make test-w2` 零 skip 依赖 runner 内 docker 可用（与 S3a `make test-obligations` 同机制），未亲验。
- `_is_own_savings` 只测「自有 CHECKING 失败」，未测「他人 SAVINGS」——代码 `customer_id == customer_id` 已正确覆盖该分支，仅缺断言。

结论：**REQUEST_CHANGES**。P0 无；P1 一项（503 遮盖 hard DENY，须修 + 补测）；P2 一项（cancelled_monthly_fees 真实视图覆盖缺口，可补测或登记）。

## 修复轮 1 定点复核（252455c..4c84019）

- **P1 关闭** ✓：provenance.py:255–258 在 `available_less_7d_charges` 分支调 `_reserve_max_outflow` 前加 `if ctx.has_hard_deny(): return "unknown"`；DENY 留在 findings、不被 503 遮盖、不降级为 REPLAN。新回归两条：test_reserve_default.py:179（多 step：transfer memo 污染→DENY + subscribe derived）、:208（同 step 参数序：account_id read→DENY + amount derived），均断言 DENY 存在且不抛 503。亲跑 test_reserve_default.py + test_derived_cancelled.py = **10 passed**；W2 非 DB 全量 = **188 passed**（原 186 无回归）。无 DENY 时缺数据仍 503：test_fail_closed_when_obligations_not_loaded / incomplete_coverage / unknown_target 三项仍通过（亲跑通过）。
- **P2 关闭** ✓：test_reserve_integration.py:314 `test_real_s3_cancelled_monthly_fees` 真实 bank tmpfs PG → 真实 L3 HTTP snapshot → 默认 ProvenanceStage；先断言 `state.subscriptions` == 真实 persisted 账单（video3000/music1500/cloud2100/gym19900/weekly1000），证明走真实持久化视图而非局部伪装。正例 cancel[video,music] → derived 4500；负例重复 cancel、来源缺失（ScheduledInstruction 非订阅）、跨账户（from acc_zs_chk2）、目标非本人储蓄（acc_zs_chk2 CHECKING）、WEEKLY 均 unknown。进度文明确「只证明来源/范围，未声称取消领域写或实际定时执行」，未把取消领域写说成通过。
- **范围** ✓：`git diff --stat` 仅 4 文件（progress、provenance.py +4、test_reserve_default.py +90、test_reserve_integration.py +264）；未改 Makefile/CI/PRD/bankcore/L3/迁移/依赖。

## 未确认 / 备注

- `_truncate`（test_reserve_integration.py:270–273）对 session 级 tmpfs `obligations_db` 执行 `TRUNCATE ... CASCADE` 以在两次测试间重置；该库为随机名、`--rm` 销毁的临时容器，非长期业务卷（未违反「禁止长期业务库 reset/TRUNCATE」硬禁），但任务书「临时测试 schema 重置只允许新建 tmpfs 独立测试库」字面倾向新建库而非 TRUNCATE 复用。无安全影响，交由协调者裁定是否要求换独立 fixture。DB 专项本 worker 未亲跑（docker 被拒），以协调者六退出/CI 为准。

结论：**APPROVE**。原 P1/P2 均已关闭，无新增 P0/P1；一条 TRUNCATE 复用备注（非阻塞）。
