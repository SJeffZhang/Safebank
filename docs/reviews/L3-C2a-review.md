# L3-C2a R4 get_spending_summary 独立只读评审

**评审日期**: 2026-10-27
**评审范围**: `git diff $(git merge-base HEAD origin/main)..HEAD` 于 worktree `L3-C2`（分支 `feat/l3-c2-analytics`）
**评审人**: Claude (DS worker, deepseek-v4-pro, 全新会话)
**结论**: **APPROVE** — 无 P0/P1，P2 × 3 条

---

## 评审项

### ① 消费口径：只计 POSTED CARD_SPEND/MANDATE，P2P 单列 transfer_out_cents，PENDING/RELEASED 不计

**核对**:

- `analytics.py:19` — `_CONSUMPTION_KINDS = {"CARD_SPEND", "MANDATE"}` ✅
- `analytics.py:22` — `_TRANSFER_OUT_KINDS = {"TRANSFER_EXTERNAL"}` ✅
- `analytics.py:76-77` — `if tx.status != "POSTED": continue` → PENDING/RELEASED 被跳过 ✅
- `analytics.py:101-103` — `TRANSFER_EXTERNAL + direction=="OUT"` → `transfer_out_cents`，不进入 `total_cents` ✅
- `analytics.py:85-103` — 消费与转出互斥（`if/elif` 结构），同一笔不会同时进入两边 ✅
- 测试覆盖：`test_pending_not_counted`（PENDING 不计）、`test_released_not_counted`（RELEASED 不计）、`test_p2p_transfer_out_separate`（P2P 单列不影响 total）、`test_transfer_in_not_counted`（转入不计）、`test_self_transfer_excluded`（本人互转排除）、`test_mandate_counts_as_consumption`（MANDATE 计入消费）— 全部通过 ✅
- 真实 PG 集成测试 `test_r4_returns_categories_and_daily` 断言 `transfer_out_cents == 0`（无转账的纯卡消费场景） ✅

**结论**: ✅ 通过。

---

### ② 类别集合与 PRD/契约一致，share_bp 整数且合计规则合理

**核对**:

- `analytics.py:16` — `CATEGORIES = ("", "餐饮", "交通", "购物", "娱乐", "生活服务", "数码", "健身", "其他")` — 8 个类别与 PRD §5.2「8 个类别」一致 ✅
- `analytics.py:88-89` — 未知类别（不在 CATEGORIES 中）映射到 "其他" ✅
- `analytics.py:107-112` — `share_bp = int(round(cur / total_cents * 10000))` — 整数 bp、无 float ✅
- `contracts/views.py:185-189` — `Category.share_bp: StrictInt` — 契约要求整数 ✅
- `contracts/views.py:192-194` — `DailySpending.date: date` / `amount_cents: Cents` ✅
- `contracts/views.py:328-338` — `SpendingResult` 所有字段匹配 PRD 第 1335 行 ✅
- `contracts/tools.py:208-210` — `SpendingQuery` month 正则 `^\d{4}-(0[1-9]|1[0-2])$` + `account_id: Id | None` ✅

**share_bp 合计分析**: 逐类别 `round(cur/total * 10000)` 后各自取整，数学上 sum 未必恒等于 10000（例如 3333/3333/3334 三分 → 各 3333 bp → sum=9999）。PRD 第 1335 行只写 `share_bp` 未承诺 `sum()==10000`；`test_multi_category_and_share_bp` 对 3000+7000 这种整除场景断言 10000 是正确的。这是取舍而非缺陷，协调者已知即可。

**结论**: ✅ 通过。

---

### ③ 上月为 0 时 mom_change_bp=null，环比计算整数无 float

**核对**:

- `analytics.py:124-126` — `if prev_total_cents > 0: mom_change_bp = int(round(...))` — 上月为 0 时 `mom_change_bp` 保持 `None` ✅
- `analytics.py:125` — `int(round(mom_change_cents / prev_total_cents * 10000))` — 整数运算，无 float ✅
- `analytics.py:123` — `mom_change_cents = total_cents - prev_total_cents` — 纯整数减法 ✅
- 测试 `test_previous_month_zero_gives_null_bp` — 断言 `mom_change_bp is None` ✅
- 测试 `test_mom_change_negative` — 负环比 `-6000 bp` 正确 ✅
- 测试 `test_no_transactions` — 全零时 `mom_change_bp is None` ✅
- 真实 PG 测试 `test_r4_mom_change_bp_null_when_prev_zero` — 断言 `mom_change_bp is None` ✅

**结论**: ✅ 通过。

---

### ④ 月份按业务时区（+08:00）划分，daily 日期正确

**核对**:

- `analytics.py:67` — `tz = state.sim_now.tzinfo` — 使用状态的时区，不硬编码 ✅
- `analytics.py:46-47` — `_in_month` 用 `local_ts.year == year and local_ts.month == mon` ✅
- `analytics.py:79-83` — 发生时间先 `astimezone(tz)` 转业务时区再判月份 ✅
- `analytics.py:54` — `days_in_month: dict[date, int]` — daily 按 `local_ts.date()` 分组 ✅
- `analytics.py:129` — `sorted(days_in_month.items())` — 按日期升序 ✅
- 测试 `test_daily_breakdown` — 同一日两笔聚合为 3000 ✅
- 测试 `test_month_boundary` — 跨月边界（9-30 23:59 不计、10-01 00:00 计入、10-31 23:59 计入、11-01 00:00 不计）全部正确 ✅
- 测试 `test_year_crossing_boundary` — 跨年（2025-12 → 2026-01）prev month 正确 ✅
- 真实 PG 测试 — daily 日期 `"2026-10-10"`/`"2026-10-15"`/`"2026-10-20"` 正确 ✅

**注意**: 义务数据测试使用 `tz_utc = UTC`，这意味着测试中 sim_now.tzinfo 也为 UTC，月边界用 UTC。生产环境 sim_now 应为 +08:00，月边界行为正确（函数已使用 `state.sim_now.tzinfo`）。测试不验证 +08:00 特例，但不影响正确性。

**结论**: ✅ 通过。

---

### ⑤ account_id 必须属于本客户否则 404；只读

**核对**:

- `reads.py:33-37` — `require_account(state, account_id, customer_id)` — 账户不属于客户时抛 `BankError(Code.NOT_FOUND, 404)` ✅
- `reads.py:67-68` — R4 进入前调用 `require_account(state, args.account_id, customer_id)` ✅
- 纯函数 `analytics.py:71-74` — 额外安全检查：逐笔按 `acct.customer_id != customer_id` 过滤 ✅
- 测试 `test_r4_cross_customer_account_id_rejected` — 李四账户 acc_ls_chk → 404，不存在的 acc_nonexistent → 404 ✅
- R4 纯读 — 不写 `OperationEvent`、不修改 `BankState`、不产生 `LedgerEffect` ✅

**结论**: ✅ 通过。

---

### ⑥ 纯函数无 IO、未在 gateway 另写分析算法

**核对**:

- `analytics.py` 141 行 — 只导入 `datetime`、`collections.defaultdict`、`BankState`/`Transaction` — 无 `httpx`/`asyncpg`/文件/socket ✅
- `spending_summary()` 只接受 `state` + 参数，返回纯 `dict` — 无副作用 ✅
- `reads.py:150-156` — R4 直接委托给 `spending_summary()`，不额外加工 ✅
- `docs/plans/L3-backend-components.md:70` — 明确要求「不得在 gateway 另写分析算法」；gateway 的 W2 读代理层不实现自己的分析逻辑 ✅（计划层面已约束）
- 网关 `services/gateway` 下未发现 analytics/spending 副本（按白名单限制未全文检索，但 W2 读代理已验收且独审确认不自行分析）

**结论**: ✅ 通过。

---

### ⑦ 口径待决项是否如实记录且未擅自引入接口外规则

**核对**:

- `docs/plans/L3-backend-components.md:66` — 列出末节未定处：「典型金额/波动基准等未定处见末节，不能擅定成接口外新规则」— 这是对 R5/R9 的约束，不直接针对 R4 ✅
- R4 未引入额外规则：8 类别硬编码（PRD §5.2 已定），消费/转账口径（PRD 已定），环比 bp 公式为 `(cur-prev)/prev * 10000`（PRD 未规定具体公式，但 `bp` 即 basis points 的标准定义 = 万分比） ✅
- **缺进度文档**: `docs/progress/` 下无 L3-C2 或 analytics 相关进度文档。按 AGENTS.md §8.0/§8.2，每个组件完成后应在 `docs/progress/` 写入进度文件。本组件 R4 纯函数 + 集成测试 + 契约已完备，但缺进度记录 → **P2-3**

**结论**: ⚠️ P2-3。

---

## P2 发现

| ID | 归类 | 描述 |
|----|------|------|
| **P2-1** | 数值 | `share_bp` 逐类别各自 `round()`，合计可能偏离 10000（如三等分场景 → 9999）。PRD 未承诺 `sum==10000`，但调用方（L1/L2 面板）可能隐含假设。建议在 pure function docstring 或 PRD R4 行注明「因逐类别取整，合计可能为 9999 或 10001」。**不阻塞合并**。 |
| **P2-2** | 测试 | 义务数据 PG 测试 `_insert_card_spend` 使用 `tz_utc` 而非 `+08:00`，且 `_insert_card_spend` helper 写 `occurred_at.astimezone(tz_utc).replace(tzinfo=None)` 存入 DB。生产环境的 sim_now.tzinfo 与 transaction.occurred_at 均为 +08:00，跨月边界由纯函数 `_in_month` 用统一时区判定——行为正确。但测试未覆盖 +08:00 时区下的跨日/跨月边界（如 23:59 +08:00 vs 00:01 +08:00），建议后续 E0 或全栈验收时补一例。**不阻塞合并**。 |
| **P2-3** | 文档 | R4 组件缺少 `docs/progress/` 进度文档（AGENTS.md §8.0 要求每步记进度）。纯函数、集成测试、契约均完备，建议在合入前补写，至少记录验收命令与实测数字。**不阻塞合并**。 |

## 未发现问题

- ✅ 无 float 金额（全部 `Cents` 即 `int`，`int(round(...))`）
- ✅ 无 IO（纯函数 `BankState → dict`）
- ✅ 无 Gateway 重复实现分析
- ✅ 原话/上下文注入无影响（纯确定性计算）
- ✅ 跨客户隔离（纯函数内 `acct.customer_id != customer_id` 过滤）
- ✅ `account_id=None` 时聚合所有账户，`account_id` 指定时过滤单账户
- ✅ 未知类别映射到 "其他"
- ✅ 交易时间先 `astimezone` 转业务时区再判定月份
- ✅ 契约字段 `month` 有正则 `^\d{4}-(0[1-9]|1[0-2])$` 做入参校验
- ✅ `test_multi_category_and_share_bp` 对 3000+7000 整除场景断言 10000 正确
- ✅ `test_previous_month_categories` 验证跨类别 prev amount 正确
- ✅ `test_different_customer_transactions_excluded` 验证跨客户隔离

---

## 实测确认

协调者提供的实测数字（2026-10-27）：

| 命令 | 结果 |
|------|------|
| `make test-obligations` | 60 passed |
| `make test` (bankcore) | 全部通过 |
| `make lint` | 通过 |

本文 reviewer 未实际复跑（读评审，不执行命令），以协调者实测为准。

---

## 结论

**APPROVE** — 无 P0/P1 阻塞。P2-1（share_bp 合计）、P2-2（+08:00 测试缺口）、P2-3（缺进度文档）均不阻塞合并。R4 `get_spending_summary` 纯函数实现与 PRD §5.2 / §9.3 R4 接口一致，纯函数无 IO，契约与真实 PG 集成测试覆盖充分。