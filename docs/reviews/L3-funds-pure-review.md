# L3 Funds Pure — Review

Scope: `git diff origin/main..HEAD` (pure bankcore fund domain: apply_subscribe, apply_redeem, apply_fund_confirm, apply_fund_settle, apply_pending_expiry, holdings_view; subscribe_product/redeem_product registered; contracts snapshot additive change).

## Verdict

**REQUEST_CHANGES** — 1 P1. The domain logic is otherwise correct and well-tested (413 bankcore tests pass), Decimal-only, ledger-conserving, and the contracts change is additive under the ADR-011 reserved-effect redefinition. Blocking item: the shared pure functions never validate that the source/payout account is type CHECKING (PRD §8.2), so a customer's SAVINGS account can be used for subscribe/redeem.

## Findings

### P0

无。

### P1

**P1-1 — 未校验「账户类型 CHECKING」（PRD §8.2）**
`apply_subscribe`（funds.py:204-207）与 `apply_redeem`（funds.py:327-330）只校验 `account.customer_id == ctx.customer_id`（本人），从不校验 `account.type == AccountType.CHECKING`（`AccountType` 甚至未导入）。
- PRD 1210：申购「扣款账户属于本人且**类型 CHECKING**」。
- PRD 1214：赎回到账「转入原指定**本人 CHECKING**」。

失败场景（可复现）：fixtures 中 `acc_sav` 为 cus_a 的 SAVINGS 账户；`subscribe_product(account_id="acc_sav", product_id=..., amount_cents=...)` 会成功并借记储蓄账户，违反定稿领域规则。该纯函数同时被 L2 预演与 L3 执行共用，规则全链路失守；测试 `test_funds.py` 无任何账户类型用例。

### P2

**P2-1 — 系统事件未校验时间边界**
`apply_fund_confirm` / `apply_fund_settle` / `apply_pending_expiry` 不校验 `at >= confirm_at / settle_at / expires_at`，早于计划时间的确认事件会以较早日期的 NAV 确认。当前由调度器驱动且错误状态为幂等 no-op，属防御性缺口而非现有正确性缺陷。

**P2-2 — 确认/结算/到期事件不产生 OperationEvent**
`_op` 仅在申购/赎回下单时调用；confirm/settle/expiry 只产生 `UpdateFundOrder` 效果与账本转账。账本转账本身有 paper trail，仅为审计完整性备注。

## Checks

- [x] **测试通过**：`.venv/bin/pytest tests/bankcore -q` → 413 passed（0.35s），含 test_funds.py 全部用例。
- [x] **source account 属于本人**：`account.customer_id == ctx.customer_id` 已校验（funds.py:206, 329）。
- [ ] **source account 类型 CHECKING**：缺失（见 P1-1）。
- [x] **适当性：申购时检查**：`_assessed_level` 为 None → ASSESSMENT_MISSING；`level < product.risk_level` → SUITABILITY_MISMATCH（funds.py:214-218）。
- [x] **适当性：确认时复查**：`apply_fund_confirm` 重新 `_assessed_level`，失败走 `_fail_confirm`（VOID_PENDING 释放冻结 + FAILED，funds.py:479-483, 512-524）。
- [x] **最小金额**：`amount_cents < product.min_amount_cents` → MIN_PURCHASE_NOT_MET（funds.py:219-220）。
- [x] **15:00 截止 + 交易日 T**：`trade_date`（calendar.py:75-86）正确应用 15:00 截止、周末顺延；测试 `test_trade_date_cutoff_and_weekend` 覆盖 3 组（当日 14:00/16:00、周末）。
- [x] **MMF 即时 vs 非即期冻结**：MMF 直接 POSTED（funds.py:224-275），非 MMF PENDING 冻结（funds.py:276-324）。
- [x] **份额 floor4 + 确认日 NAV**：确认用 `_business_time(at).date()` 的 NAV，`_floor4`；demo 1,195,500 分 @ 1.2311 → **9710.8277**（test 断言 exact，funds.py:484-485）。NAV 1.2311 = 基准 1.2310 + 1 交易日 × 0.0001，正确。
- [x] **赎回预留份额、禁止重复卖**：`available_units < units` → HOLDING_INSUFFICIENT；下单 `reserved_units_delta=units`；`test_redeem_cannot_double_sell` 覆盖。
- [x] **赎回到账金额 floor_cents(units*nav*100)**：funds.py:345 `_floor_cents(units * nav_value * Decimal(100))`。
- [x] **预留成本舍入**：部分赎回 `_round_cents((cost−reserved_cost)*units/available_units)`；全额卖取全部可用成本（funds.py:346-353），与 PRD 公式一致（available_units == units − reserved_units）。
- [x] **T+settle_days 结算**：`settle_at = _at_09(_plus_trading_days(t_date, product.settle_days))`；`_plus_trading_days` 语义正确（第 n 个后续交易日）。
- [x] **pending 到期释放冻结**：`apply_pending_expiry` 走 VOID_PENDING，pending 清零（测试 `test_pending_subscribe_expires_and_releases_freeze`）。
- [x] **账本守恒**：`total_equity == 0` 在全部资金流测试中断言；申购借客户/贷结算账户，赎回借结算/贷客户，方向正确。
- [x] **Decimal/int 无 float**：全程 Decimal；`test_no_float_in_fund_arithmetic` 断言 units/nav 为 Decimal、金额为 int。
- [x] **契约变更 additive**：state.py 仅新增 `FundOrderRecord`、`BankState.fund_orders` 字段；FUTURE_EFFECTS 中 `InsertFundOrder/UpdateFundOrder.order` 由 `FundOrderView` → `FundOrderRecord`（ADR-011 保留占位「可重定义一次」）。全仓 grep 确认无其他生产/消费者引用这两个效果，重定义合法。v1.json 仅 5 行删除（恰为该效果 `$ref` 与定义键重排），其余为新增。

**核对结论**：除 P1-1 外全部通过。修复 P1-1（`apply_subscribe`/`apply_redeem` 增加 `account.type == AccountType.CHECKING` 校验并补红测，验证 SAVINGS 账户被拒）后建议复审。
