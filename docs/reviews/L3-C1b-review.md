# L3-C1b R11 upcoming_charges 独立只读评审

**评审范围**: `feat/l3-c1b-upcoming` vs `origin/main`
**评审人**: DeepSeek V4 Pro (只读)
**日期**: 2026-10-27

---

## 结论

**APPROVE** — P0 无、P1 无、P2 1 条（不阻塞）。

---

## 核对清单

### 1. upcoming_window 算法正确性

#### 1.1 与 projected_shortfall 同一算法（无复制）

✅ **通过**。`reserve.py:326` 的 `_compute_window` 是两者共享的私有核心：
- `projected_shortfall(state, days=7)` 调用 `_compute_window(state, days=7)`（第 388 行）
- `upcoming_window(state, days=days, account_id=account_id)` 调用 `_compute_window(state, days=days, account_id=account_id)`（第 413 行）

两者共享 `_recurrences`（第 98 行）做日历展开，共享 `_raw_obligations`（第 264 行）做 obligation 展开。无复制粘贴。

#### 1.2 7 天结果一致（有测试）

✅ **通过**。两条测试：
- `test_upcoming_window_7_days_matches_projected_shortfall`（第 986 行）：逐条比对 obligation 和 account projection，`strict=True` 要求数量一致
- `test_upcoming_window_7_days_matches_prd_complete`（第 1010 行）：完整 PRD 状态（转账后余额 + 多商户）下 max_outflow/reserve/obligation ID 一致

#### 1.3 左开右闭、06:00、月末 anchor、闰年、weekly

✅ **通过**。逐一核实：

- **左开右闭**：`_recurrences:119` `if due_at > start`（排除起点），`due_at <= cutoff`（包含终点）。测试 `test_window_excludes_now_and_includes_end`（第 336 行）、`test_upcoming_window_left_open_right_closed`（第 1049 行）均通过。
- **06:00**：`test_upcoming_window_0600_execution_samples_correct_hour`（第 1065 行）断言 `due_at.hour == 6`。执行时间由 obligation 自身的 `time_of_day` 决定，不是硬编码 06:00；`_check_calendar`（第 78 行）校验 `next_run_at` 与 `time_of_day` 一致。
- **月末 anchor**：`_next_month`（第 69 行）使用 `monthrange(year, month)[1]` 做 day clipping。`test_upcoming_window_monthly_31_anchor_clips_to_feb_28`（第 1076 行）验证非闰年 2 月裁剪到 28 日；`test_upcoming_window_monthly_31_anchor_restores_after_nonleap_feb`（第 1096 行）验证 3 月恢复为 31 日。
- **闰年**：`test_upcoming_window_leap_year_feb_29`（第 1130 行）验证 2024 年 anchor_day=31 的 2 月出现 2-29。
- **Weekly**：`test_upcoming_window_weekly_keeps_monday_zero`（第 1267 行）验证 weekday=3→周四，`test_upcoming_window_weekly_in_30_days`（第 1305 行）验证 30 天内出现 4 次。Weekday 使用 `datetime.weekday()`（Monday=0），与 docstring 一致。

---

### 2. R11 读取工具正确性

#### 2.1 只计有效授权 + 真实 bill 金额（不用 max_amount）

✅ **通过**。`reads.py:268-296`：
- `obl.delta_cents` 来自 `_raw_obligations`，金额取自 bill 的 `amount_cents`（第 321 行 `-billing.amount_cents`），不是 mandate 的 `max_amount_cents`
- `max_amount_cents` 字段单独取 `mandate.max_amount_cents`（第 279-283 行），仅作参考信息
- 只展开 `status=="ACTIVE"` 的指令（第 270 行）和 `billing.active==True` 的账单（第 307 行）
- `_validate_mandate_billing`（第 240 行）校验 mandate↔billing↔account 的一致性

#### 2.2 只读

✅ **通过**。`reads.py` 的 `read()` 函数只调用 `upcoming_window()`（纯函数）并格式化结果。无 `apply_*`、`post_*` 或任何写操作。

#### 2.3 跨客户 account_id → 404

✅ **通过**。两条路径均返回 404：
- `test_r11_cross_customer_account_id_rejected`（第 834 行）：`account_id="acc_ls_chk"`（李四的账户）→ 404
- 不存在的 `account_id="acc_nonexistent"` → 404
- `reads.py:32-35` 的 `require_account` 校验 `account.customer_id != customer_id`；`reserve.py:346-348` 的 `_customer_account` 校验账户属于某客户

#### 2.4 不可信字段处理（merchant_name 等）

✅ **通过**。`merchant_name` 取自 `merchants_map[merchant_id].name`（`reads.py:274-278`），来源是 obligation snapshot 中的 merchants 表（银行种子数据），不是用户输入。`max_amount_cents` 同理来自 mandate。两者均为展示字段，不参与授权决策。无 PII 泄露（不含账户号、手机号等）。

---

### 3. 契约改动范围

#### 3.1 仅 AccountReserve.max_outflow_cents 改为 SignedCents

✅ **通过**。`views.py:258`：`max_outflow_cents: SignedCents`（原为 `Cents`）。`base.py:31`：`SignedCents = Annotated[Int64, Field()]` — 无 `ge=0` 约束。

#### 3.2 其余 Cents 未放宽

✅ **通过**。逐一核对 views.py 中所有 `*_cents` 字段：
- `AccountReserve.reserve_cents: Cents`（`ge=0`，未改）
- `Charge.amount_cents: Cents`（未改）
- `Charge.max_amount_cents: Cents`（未改）
- `AccountView` 中 `balance_cents`/`pending_debit_cents`/`pending_credit_cents`/`available_cents` 均为 `Cents`（未改）
- `base.py` 中 `Cents = Annotated[Int64, Field(ge=0)]` 定义未变

#### 3.3 冻结快照同步

✅ **通过**。`docs/contracts/v1.json:17719-17723`：`max_outflow_cents` 的 JSON Schema 为 `ge: -9223372036854775808, le: 9223372036854775807`（`Int64` 全范围），`reserve_cents` 仍为 `ge: 0`。SHA256 已更新（`v1.sha256` 一行新 hash）。

#### 3.4 ADR-010 理由充分

✅ **通过**。`ADR-010-signed-max-outflow.md` 完整记录了：
- Context：bankcore 已返回负值，`Cents(ge=0)` 阻止序列化
- Decision：新增 `SignedCents`，仅改 `max_outflow_cents`
- Consequences：L1/L2 消费者需区分负值（既有缺口），`hint_max_outflow` 已正确处理
- Alternatives：3 种方案均有分析，拒绝理由合理

---

### 4. 测试覆盖

#### 4.1 days=1/7/30

✅ **通过**：
- `test_r11_days_1_returns_empty`（第 722 行）— 1 天窗口无 obligation
- `test_r11_days_7_returns_charges_and_totals`（第 678 行）— 2 条 charge
- `test_r11_days_30_returns_all_charges_and_scheduled`（第 755 行）— 4 条 charge + 1 条 scheduled

#### 4.2 账号过滤

✅ **通过**：`test_r11_account_id_filter`（第 793 行）分别查 `acc_zs_chk` 和 `acc_zs_sav`，验证 obligations 和 by_account 集合正确。

#### 4.3 account_id 可选

✅ **通过**：所有测试中混用 `account_id=None`（全客户）和 `account_id="acc_zs_chk"`（单账户）。

#### 4.4 空集合

✅ **通过**：`test_r11_empty_obligations_zero_totals`（第 861 行）— `cus_sb` 无 obligation，所有列表为空、totals 为 0、max_outflow 为余额。

#### 4.5 负 max_outflow

✅ **通过**：`test_r11_shortfall_max_outflow_negative`（第 897 行）— 余额 200,000，账单 250,000，`max_outflow_cents == -50_000` 且 `< 0`（双重断言确保签名语义）。

#### 4.6 越界参数

✅ **通过**：
- `test_r11_days_0_invalid`（第 1012 行）→ 422
- `test_r11_days_31_invalid`（第 1034 行）→ 422
- Bankcore 层 `test_upcoming_window_rejects_invalid_days`（第 883 行）：0, −1, 31, 365, 7.0, True, "7", None 全部拒绝

---

## 发现

### P0（阻塞合并）

无。

### P1（合并前应修）

无。

### P2（不阻塞，建议修）

**P2-1**: `reads.py:249` 的 `except ValueError` 过于宽泛。`upcoming_window` 可能因 "obligations were not loaded" 或 "obligation coverage does not match state versions" 抛出 `ValueError`，这些是服务器配置错误（应 500），而非 "账户不存在/无权访问"（应 404）。当前部署中 obligations 总是已加载且覆盖率完整，不会被触发，但语义不够精确。建议改为只捕获 `_customer_account` 抛出的 `ValueError`（账户不存在/无客户归属），或用更特异的消息匹配。

---

## 复审备注

- 协调者实测 `test-obligations 55 passed`、`test-reserve 120 passed`、`make test 2992 passed`、`contracts-check PASS`、`lint OK`，与本次静态核对结论一致。
- `test_upcoming_window_7_days_matches_projected_shortfall` 使用 `strict=True` zip，确保两函数 obligation 数量严格相等。
- `test_upcoming_window_account_filter_only_excludes_other_account_obligations`（第 1217 行）验证 OWN credit 在 account_id 过滤后仅返回目标账户的 +1000，不返回源账户的 −1000。
- `test_r11_reserve_total_consistent_with_projected_shortfall`（第 986 行）验证 `reserve_total_cents` 等于 `by_account` 各项之和。