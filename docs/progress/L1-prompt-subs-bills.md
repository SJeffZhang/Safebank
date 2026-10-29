# L1 订阅 + 账单分析提示词规则

分支：`feat/l1-prompt-subs-bills`（自 main fork）

## 背景

订阅（取消订阅 / 找出没用的订阅取消）与账单分析两个场景在 L3 已有真实实现，
但 L1 的 `PlanAdapter` / `GatherAdapter` 提示词未针对这两个场景给出收集与计划
约束，导致模型可能：

- 编造 `cancel_subscription.instruction_id`（未从已读订阅数据取 si_ 前缀 id）；
- X-2「取消闲置订阅并把省下的钱每月定时转入储蓄」中把 `schedule_transfer.amount_cents`
  填成原话字面金额，而不是 `cancelled_monthly_fees` 派生金额；
- 把账单分析（月度/商户账单、分类占比、异常交易、报告）这类只读任务写成写计划，
  或 gather 阶段读取了无关的账户/收款人数据而没读账单所需数据。

## 修复

### plan.py — `_SYSTEM` 追加 3 条规则

- `cancel_subscription.instruction_id` 必须从 `list_subscriptions` /
  `detect_subscriptions` 的 items 里取 si_ 前缀 id，用 read 来源引用，不能编造。
- 订阅组合里 `schedule_transfer.amount_cents` 只能用 `derived`（template=
  `cancelled_monthly_fees`），inputs 只允许订阅读取 / 取消步骤，不得引用原话字面金额；
  账户仍用 read 来源，`first_run_at` 按用户说的日期。
- 账单分析类问题是只读任务，不生成写计划。

### plan.py — 新增 `_derived_schedule_problems`

本地确定性校验：`schedule_transfer` 步骤的 `amount_cents` 若为 derived 来源且其
`inputs` 含 utterance 叶子（单参数只能有一个顶层来源，字面金额只能被塞进 derived
inputs），报 ReferenceError。该校验接入 `__call__` 的 `all_problems`，并加入
exhausted-clarify 守卫（`not der_problems`），避免把派生金额问题误转成澄清。

### gather.py — `_SYSTEM` 追加 2 条规则

- 订阅管理须读 `list_subscriptions` / `detect_subscriptions` 拿 si_ 前缀
  `instruction_id` 与 `is_idle`。
- 账单分析按自然月读 `get_spending_summary`（month=YYYY-MM）与
  `get_transactions`（日期范围），异常读 `detect_anomalies`，卡限额读
  `get_cards`；「10月/上个月/9月」按北京时间（Asia/Shanghai，+08:00）换算，
  不为只读分析读账户/收款人。

## 测试（`tests/agent/test_subs_bills_prompt.py`，4 条）

- 计划提示词含新规则（cancel_subscription 读来源 / cancelled_monthly_fees /
  账单分析只读）；
- gather 提示词含新规则（list_subscriptions / detect_subscriptions /
  get_spending_summary / detect_anomalies / get_cards / 北京时间）；
- read 来源的 si_ 指令 id 计划通过本地校验（来源 kind=read、ref、pointer 正确）；
- derived 金额携带 utterance 字面来源的计划被本地拒绝（ReferenceError 含
  「派生金额」）。

红测先行：实现前 3 failed / 1 passed；实现后 4 passed。
（首版红测的拒绝用例只给 1 条脚本响应，ReferenceError 会触发重试导致
IndexError，已改为 2 条同计划响应。）

## 门禁（tail -1）

| 命令 | 结果 |
|---|---|
| `make lint` | All checks passed；ruff format --check 通过（测试文件格式化后） |
| `.venv/bin/pytest tests/agent -q` | 536 passed, 38 skipped |
| `make test` | 3374 passed, 903 skipped |

提交：
- `54c004d` 红测
- `e8b3eb5` 提示词规则 + derived 金额守卫
- `9437ada` 行宽与测试文件格式化

未覆盖/备注：本组件只加提示词与本地校验，不新增 LLM 调用、不改授权逻辑；
derived 金额校验只拦「inputs 里塞 utterance 字面来源」这一种走私形态，其余
来源合法性仍由 L2 判定。
