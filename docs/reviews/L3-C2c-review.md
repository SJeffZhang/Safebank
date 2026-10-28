# L3-C2c 独立评审（R9 detect_subscriptions）

范围：`git diff origin/main...f3652aa`（analytics.py、reads.py、tests/bankcore/test_analytics.py、tests/obligations_data/test_reads.py，共 5 文件，940 增 4 删）

## 结论

**APPROVE**

四项核对全部通过，无 P0/P1。识别阈值、业务时区、跨客户隔离、金额整数性均与 PRD §5.4 及既有 L3 约定一致；测试为纯新增（0 删除），断言严格且真实走 tmpfs PG + HTTP。1 条 P2（纯函数对 `state.subscriptions` 未按 customer 过滤，属防御性缺口，当前 L3/L2 均按客户加载状态，不构成可利用泄露）。

## P0

无

## P1

无

## P2

1. **detect_subscriptions 对 `state.subscriptions` 未按 customer_id 过滤**（analytics.py:411–418）：`sub_by_merchant` 遍历整个 `state.subscriptions`，只排除 `DELETED`，不校验订阅所属客户。交易侧（charge 候选与 usage evidence）都做了 `acct.customer_id != customer_id` 过滤，订阅侧缺失。当前 `load_bank_state`（state.py:206→_derive_views:222 `if item.customer_id != customer_id: continue`）已按客户过滤，L2 快照亦按客户，故不可利用；但若未来有人传入多客户 BankState，同 merchant_id 会串到他人 `instruction_id/billing_id`。建议在纯函数内同样按账户归属过滤订阅，作为纵深防御。

## 已核对项

1. **识别阈值与 PRD §5.4 一致**（通过）：PRD 第 616 行「同一 merchant_id、金额波动 ≤ 5%、间隔 28–31 天或 7 天、≥ 3 次」。代码：`min_occurrences=3`、`variance_tolerance_bp=500`（500bp=5%）、`_PERIOD_MONTHLY_RANGE = range(28, 32)`、`_PERIOD_WEEKLY_DAYS = 7`（analytics.py:341–343）。月度判定 `all(d in range(28,32))`、周度判定 `set(intervals) == {7}`，混入 7 与 28–31 时 period=None 正确跳过。

2. **典型金额取中位数显式**（通过）：`typical_method: str = "median"` 关键字参数，docstring 说明 `"median" (default) or "floor_mean"`，`typical = int(median(amounts))` 再转 int（analytics.py:413–421）。方差以该典型值为基准比较，语义明确。

3. **闲置判定与 PRD S-5 一致**（通过）：PRD 第 617 行「只对 usage_expected=true（健身、培训）判定：近 60 天无非扣费类交易，且 60–120 天前有」。代码 `is_idle = usage_expected and recent == 0 and previous > 0`，recent=(sim_now-60d, sim_now]、previous=(sim_now-120d, sim_now-60d]，usage evidence 排除 `kind == "MANDATE"`（analytics.py:432–449）。

4. **业务时区经 reserve._business_time**（通过）：analytics.py 头部 `from safebank_bankcore.reserve import _business_time`；`sim_now = _business_time(state.sim_now)`、cutoff/间隔/usage 窗口均用 `_business_time(tx.occurred_at)`。`_business_time` 定义于 reserve.py:63–66，`astimezone(BUSINESS_TIMEZONE)`，`BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")`（reserve.py:30）。

5. **reads.py R9 分支只读本客户数据**（通过）：reads.py:348–353 `detect_subscriptions(state, customer_id=customer_id, lookback_days=args.lookback_days)`。纯函数对 charge 候选与 usage evidence 均 `acct = state.accounts.get(tx.account_id); if acct is None or acct.customer_id != customer_id: continue`。R9 接口无 account_id 参数（PRD §9 R9 仅 `{lookback_days}`），故「他客户 account_id 返回 404」不适用；跨客户不可见由 `test_r9_cross_customer_invisible` 覆盖（插入 cus_ls 在 mer_video 的 3 笔月扣费，cus_zs 查询返回空）。

6. **金额无 float**（通过）：输出字段 `typical_amount_cents=int(median(...))`、`annual_cost_cents=typical*12/52`、`occurrences`、`usage_evidence` 计数均为 int。`variance_ratio = variance_tolerance_bp / 10000.0` 仅用于比较阈值，不出现在返回值。

7. **余额曲线时区修复（4ebe304）正确**（通过）：reads.py:84 由 `t.occurred_at.astimezone(state.sim_now.tzinfo).date() <= date` 改为 `_business_time(t.occurred_at).date() <= date`，统一投影到 Asia/Shanghai，修复 UTC sim_now 下北京凌晨交易被归到前一日的缺陷。配套红测 `test_r4_cross_month_tz_plus0800_boundary`（10-01 00:30+08:00 归十月）与 `test_r4_returns_categories_and_daily` 的 UTC 白天归属均真实断言。

8. **测试无删除/放宽，真实 PG 用例真实**（通过）：两测试文件均为纯新增（numstat 473/0、205/0），无删除断言。真实 PG：`tests/obligations_data/test_reads.py` 用 `obligations_db` tmpfs fixture + `create_app` + uvicorn 真实 HTTP + `install_core/install_obligations` 真实种子，非 FakeL3。R9 用例断言精确值（period、typical_amount_cents=3000、occurrences=3、annual_cost_cents=36000、instruction_id/billing_id 关联、is_idle=False），含非周期商户不计数与跨客户不可见两条负例。
