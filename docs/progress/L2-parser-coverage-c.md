# L2 parser coverage — part C（跨场景联动：X-2 派生模板定时转账 + X-4 冻结可疑卡后应急转账）

任务：`feat/l2-parser-coverage-c`。修复两条在 G3 被标 `INTENT_UNCLEAR` 的跨场景原话，
使其各产出**一个**多动作 SCOPED scope（客户 `cus_zs`：活期 `acc_zs_chk`、储蓄
`acc_zs_sav`，卡 `crd_zs_1`）。

## 目标原话与期望

1. **X-2** `找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户`
   - `MANDATE_CANCEL`（谓词 `idle_subscriptions`，目标集合由 L2 求值，解析器不猜）
     + `SCHEDULE_CREATE`（每月 1 号 → 储蓄户，`derived_template = cancelled_monthly_fees`，
     金额占位 0，来源 = 活期 CHECKING）。

2. **X-4** `把这笔可疑交易对应的卡冻结，再从储蓄转2000到活期应急`
   - `CARD_FREEZE`（谓词 `suspicious_cards`）+ `TRANSFER_SELF` `acc_zs_sav → acc_zs_chk`，
     金额 200000 分。

安全边界（保持不变）：任一从句不完整则整句 `FORM_REQUIRED`；金额绝不凭空编造
（X-2 金额只能是派生模板）；否定 / 引号 / 疑问仍不产生 scope。

## 根因

X-4 的解析路径本就正确，无需改动（其测试作回归护栏）。X-2 的 `INTENT_UNCLEAR`
来自三处解析器缺陷：

1. `scheduled` 派生模板正则把 `future=True` 写死在 `Command` 里（`parser.py`），
   而 `parse()` 结尾对 `future` 一律置 `INTENT_UNCLEAR`。具体月度锚点应当与
   「每月 N 号给 X 转 Y」的普通定时转账一致——**完整**，不是 future。
2. `first_run_at` 的计算条件 `and not command.fields.get("derived_template")`
   跳过了派生模板定时转账，导致 `first_run_at` 永远缺失（`scope_is_complete`
   对 `SCHEDULE_CREATE` 要求 `first_run_at + period`）。
3. `scope_is_complete` 拒绝 `amount_cents <= 0`，而派生模板定时转账的占位金额
   合法为 0。

## 修复

`services/gateway/app/utterance/parser.py`：

1. `scheduled` 正则匹配的 `Command` 移除 `future=True`。
2. `first_run_at` 条件改为 `if tool == "schedule_transfer" and not command.future:`。
3. `scope_is_complete` 中，仅当 `kind == "SCHEDULE_CREATE"` 且带
   `derived_template` 时允许金额为 0（此时要求 `amount_cents == 0`），其余
   转账仍要求严格正金额。

派生模板守卫（要求同一原话中 `cancel_subscription` 在 `schedule_transfer` 之前）
已存在且通过，未改动。

## 测试

- 新增 `tests/gateway/w1/test_parser_coverage_c.py`：X-2 / X-4 各一条正向断言 +
  9 条攻击回归（否定 / 疑问 / 引号 / 不完整从句 / 缺谓词目标 / 无前置取消的派生模板）。
- 更新 `tests/gateway/w1/test_parser.py`：两条派生模板定时转账原话（含
  `取消闲置订阅，把省的钱每月1号转储蓄`）的预期由 `INTENT_UNCLEAR` 改为
  `SCOPED`，动作元组不变。

红测先行：修复前 X-2 为 `INTENT_UNCLEAR`（`1 failed, 10 passed`）。

## 评审修复（independent review APPROVE + P2）

评审报告 `docs/reviews/L2-parser-coverage-c-review.md`：APPROVE，无 P0/P1，两条 P2。

- **P2-1（纵深防御，已修）**：派生模板定时转账的目标在解析期必须只解析到
  **本客户唯一一个 SAVINGS 账户**（镜像 `provenance._is_own_savings`）。收款人
  目标（如「…每月1号转到王五」）或歧义目标一律 `FORM_REQUIRED`/`INTENT_UNCLEAR`，
  绝不 SCOPED。红测先行：`test_x2_derived_schedule_payee_target_rejected`。
  下游 `_derived` 本就 fail-closed，此修只是把非自转目标在解析期就拒掉。
- **P2-2（评测路径证明，已加）**：`tests/gateway/w2/test_derived_cancelled.py`
  新增 `test_cancelled_fees_reject_wrong_amount`：schedule 步金额与取消订阅费用
  之和不等（10000 ≠ 19900）时，`_derived` 返回 `unknown` → `UNKNOWN_SENSITIVE_ARG`
  REPLAN。（金额 0 的占位只存在于 scope.transfers；step args 的 `amount_cents`
  被契约强制 `> 0`，故错值用非零值验证。）

## 门禁（tail -1）

| 命令 | 结果 |
|---|---|
| `make lint` | 428 files already formatted |
| `make test` | 3355 passed, 901 skipped |
| `make test-w1` | 1794 passed |
| `make test-w2` | 257 passed |
| `make test-w3a` | 45 passed |

## 提交

- `31f1ee9` fix(parser): derived-template monthly schedule no longer future/INTENT_UNCLEAR
- `92dd183` style(parser): ruff format
- `46806d9` docs: add parser coverage c review
- `6280923` fix(parser): reject non-self target for derived-template schedule (P2-1)
- `133e7b9` test(w2): derived cancelled-monthly-fees wrong amount is REPLAN (P2-2)

未推送（worker 禁 push）；未运行 `make up` / `make test-integration` / docker 卷命令。
