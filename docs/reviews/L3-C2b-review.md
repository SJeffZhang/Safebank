# L3 C2b 独立评审报告

**评审范围**: `git diff 844ef75..b683b1b`
**评审日期**: 2026-10-28
**评审类型**: 只读独立评审

---

## 结论

**APPROVE**

---

## P0（阻塞合并）

无

---

## P1（合并前应修复）

无

---

## P2（可后续修，不阻塞合并）

无

---

## 已核对项

1. ✅ ANOMALY_* 阈值与时间窗核对通过。
   - `ANOMALY_NIGHT_LARGE`: `analytics.py:240` → `0 <= local_hour <= 4 and tx.amount_cents > 50_000`。PRD 第 1218 行「夜间大额：00:00–05:00 且 > 50,000 分」，`<= 4` 即 00:00–04:59，与 `00:00–05:00`（不含 05:00）一致；`>` 严格大于正确。
   - `ANOMALY_DUPLICATE`: `analytics.py:254` → `diff_min <= 5`。PRD 第 1218 行「5 分钟内」，`<=` 包含恰好 5 分钟是正确的（「5 分钟内」含边界）。
   - `ANOMALY_NEW_MERCHANT_LARGE`: `analytics.py:262-265` → 前 90 天无该 `merchant_id` 且 `> 100_000`。PRD 第 1218 行「前 90 天无此 merchant_id 且 > 100,000 分」，完全一致。90 天窗口用 `h.occurred_at >= cutoff`（含左边界）正确。
   - `ANOMALY_CATEGORY_OUTLIER`: `analytics.py:275-282` → 前 90 天同类别 `≥ 10` 笔，`tx.amount_cents > int(threshold)`（`threshold = μ + 3σ`）。PRD 第 1218 行「此前 90 天同类别 ≥ 10 笔且金额 > 均值 + 3σ」，使用 Decimal 算术（`analytics.py:276-280`），整数和 Decimal 计算与 PRD 要求一致。

2. ✅ 所有按日/月/小时判断均经过 `_business_time`，无 sim_now.tzinfo 或宿主时区残留。
   - `spending_summary`: `analytics.py:96` → `local_ts = _business_time(tx.occurred_at)`，后续月份判断（lines 104, 109-111）均使用 `local_ts`，包括 `_in_month` 的 `ts.year == year and ts.month == mon`。
   - `detect_anomalies`: `analytics.py:237` → `_business_time(tx.occurred_at).hour` 用于夜间判断。90 天窗口计算（lines 262, 271）用 `tx.occurred_at - timedelta(days=90)` 保持原时区，与 `h.occurred_at >= cutoff` 同 tz 比较正确。`from_at/to_at` 范围过滤（lines 210-212）用原始 datetime 比较，均为用户传入带 tz 的时间，无宿主时区泄漏。
   - `_business_time` 实现（`reserve.py:63-66`）：`value.astimezone(BUSINESS_TIMEZONE)`，`BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")`。
   - 所有测试文件时间戳均使用 `+08:00` 或 `BUSINESS_TIMEZONE`，不再有 `.replace(tzinfo=None)` 残留（`test_reads.py` 旧行 `.replace(tzinfo=None)` 已删除）。

3. ✅ R4/R5 分支只统计本客户账户，account_id 为他人时报错，金额无 float。
   - R4 `get_spending_summary`: `reads.py:152-157` 调用 `spending_summary(state, customer_id=...)`，analytics 内 `analytics.py:88` 逐笔检查 `acct.customer_id != customer_id → continue`。
   - R5 `detect_anomalies`: `reads.py:340-346` 调用 `detect_anomalies(state, customer_id=..., account_id=args.account_id)`，analytics 内 `analytics.py:206` 逐笔检查 `acct.customer_id != customer_id → continue`，`analytics.py:208` 按 account_id 可选过滤。
   - L3 reads.py:69 全局 `require_account(state, args.account_id, customer_id)` 在 account_id 非空时验证所有权，非本客户返回 404。
   - 金额全链路均为 `int`（分），无 float 或 Decimal 混入（analytics.py 仅 category outlier 的 σ 计算局部使用 Decimal，结果转为 int）。

4. ✅ 测试文件中无删除或放宽的断言。
   - `tests/bankcore/test_analytics.py`: 仅新增 666 行（27 个新测试），无删除行。
   - `tests/obligations_data/test_reads.py`: 仅删除 2 行。第 1 行（旧 line 1080）`.replace(tzinfo=None)` 移除时区信息→已改为保留时区（`occurred = occurred_at.astimezone(tz_utc)`），属于修复而非放宽。第 2 行（旧 line 1160）`occurred_at=dtm(2026, 10, 20, 18, 0, 0, tzinfo=tz_utc)` → 改为 `dtm(2026, 10, 20, 10, 0, 0, tzinfo=tz_utc)`（10:00 UTC = 18:00 +08:00，修正 UTC 时间与业务时间错位，非放宽）。
   - 新增测试均含严格断言：`test_r4_cross_month_tz_plus0800_boundary` 断言跨月边界正确落入 October、`test_r5_cross_customer_404` 断言他人 account_id 返回 404、`test_r5_account_id_filter` 断言仅返回指定 account_id 的异常（显式排除 `acc_zs_sav`）、`test_r5_no_anomalies_empty_result` 断言安全交易返回空列表。无任何原有断言被删除、放宽精度或改 `==` 为 `>=`。

---