# L3 基金纯函数（fund domain pure functions）

日期：2026-10-29
分支：`feat/l3-funds-pure`
范围：`packages/bankcore/src/safebank_bankcore/funds.py`（纯函数，无 IO / 无 DB / 无随机）

## 函数清单

| 函数 | 签名 | 说明 |
|---|---|---|
| `apply_subscribe` | `(state, args, now, ctx)` | 申购。MMF 立即 POSTED 结算并记持仓；非 MMF 冻结 PENDING 转入待确认。校验：账户归属、产品存在、结算账户存在（`DEPENDENCY_UNMET`）、适当性（`ASSESSMENT_MISSING` / `SUITABILITY_MISMATCH`）、最低申购额（`MIN_PURCHASE_NOT_MET`） |
| `apply_redeem` | `(state, args, now, ctx)` | 赎回。MMF 立即结算；非 MMF 保留份额进入 PENDING_SETTLEMENT。校验持有可用份额（`HOLDING_INSUFFICIENT`） |
| `apply_fund_confirm` | `(state, order_id, at, ctx)` | 确认事件（非 MMF 申购）。重查适当性，POST_PENDING 落账；失败走 `_fail_confirm`（VOID_PENDING + FAILED） |
| `apply_pending_expiry` | `(state, order_id, at, ctx)` | pending 申购超时释放（VOID_PENDING + EXPIRED）。原 `apply_fund_expiry`，按 PRD §8.2 改名，无别名 |
| `apply_fund_settle` | `(state, order_id, at, ctx)` | 赎回到账事件（PENDING_SETTLEMENT → SETTLED，结算账户→客户账户） |
| `order_view` | `(order)` | `FundOrderRecord` → 公开 `FundOrderView`（去掉内部键） |
| `holdings_view` | `(state, customer_id, account_id=None, product_id=None)` | `get_holdings` 读结果（持仓 + 订单） |

事件函数幂等：订单不是目标前置状态时返回 `(state, (), ())`，不重复记账。确认/到期竞争只有一终态。

## 测试清单（`tests/bankcore/test_funds.py`，14 passed）

- `test_mmf_subscribe_posts_immediately`
- `test_non_mmf_subscribe_freezes_then_confirms_demo`（演示数字，见下）
- `test_trade_date_cutoff_and_weekend` ×3（15:00 截断、周末顺延到交易日）
- `test_suitability_mismatch_and_missing_assessment`
- `test_min_purchase_not_met`
- `test_redeem_reserves_and_settles`
- `test_redeem_cannot_double_sell`
- `test_pending_subscribe_expires_and_releases_freeze`
- `test_confirm_suitability_failure_releases_and_marks_failed`
- `test_holdings_view_and_redeem_flow`
- `test_subscribe_via_apply_plan_result_shape`
- `test_no_float_in_fund_arithmetic`（份额/净值 Decimal4，金额整数分）

## 演示数字核对（PRD §12 第 6 幕 李四申购债券）

申购 1,195,500 分（¥11,955.00，`prd_bond`，nav 1.2310，confirm_days=1）：

- 申购后：`acc_a.debits_pending == 1_195_500`，`available == 2_000_000 - 1_195_500`，订单 `PENDING_CONFIRM`，`confirm_at == 2026-10-16T09:00:00+08:00`
- 确认后：订单 `SETTLED`，`units == 9,710.8277`，`nav == 1.2311`，`debits_posted == 1_195_500`、`debits_pending == 0`，持仓 `cost_cents == 1_195_500`
- 守恒：`total_equity == 0`（客户资金与内部结算账户互相抵消）

## 契约变更（`git diff 0b9684e..HEAD -- packages/contracts docs/contracts`）

- `packages/contracts/src/safebank_contracts/state.py`：
  - 新增 `FundOrderRecord`（内部持久化记录；`type` SUB|RED，`status` PENDING_CONFIRM|PENDING_SETTLEMENT|SETTLED|FAILED|EXPIRED，含 `pending_transfer_id`/`confirm_at`/`settle_at`/`expires_at`/`failure_code` 等）
  - `BankState` 新增 `fund_orders: tuple[FundOrderRecord, ...] = ()`
  - `FUTURE_EFFECTS` 中 `InsertFundOrder` / `UpdateFundOrder` 的 `order` 字段类型由 `FundOrderView` 改为 `FundOrderRecord`
  - 从 `.base` 引入 `Decimal4`
- `docs/contracts/v1.json` / `v1.sha256`：冻结 Schema 重新生成（新增 `FundOrderRecord` 定义，+249 行）

## 本次收尾改动

- 重命名 `apply_fund_expiry` → `apply_pending_expiry`（PRD §8.2 名称；更新 `funds.py` 与 `test_funds.py`，无别名、无旧引用）
- 替换 `tests/gateway/w3a/test_preflight.py::test_unsupported_domain_write_is_tool_not_allowed`：`subscribe_product` 现已实现，改为断言 `set(WRITE_ARGS) <= bankcore.SUPPORTED_WRITES`。未知工具名的负向覆盖不再有意义（`PlanDraft.steps` 是精确 `WRITE_ARGS` 的判别联合，未知工具名在构造期即被拒，到不了 preflight 的 `TOOL_NOT_ALLOWED` 分支）
