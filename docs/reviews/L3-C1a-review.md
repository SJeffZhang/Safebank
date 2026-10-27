# L3-C1a 独立评审：R7 list_scheduled 与 R10 list_subscriptions

**评审范围**：54d08b4..c41c29c  
**评审文件**：`services/bank-backend/app/reads.py`、`tests/obligations_data/test_reads.py`  
**评审日期**：2026-10-27  
**评审方式**：只读静态核对  
**审查者**：DeepSeek 只读 worker

---

## 结论

**APPROVE** — 无 P0/P1。3 条 P2（跨工具游标碰撞、R10 缺分页/空集合测试、last_run 覆盖浅），均不阻塞合并。核心安全属性（跨客户不可见、只读、合计仅 ACTIVE）已验证；年化口径与 PRD §9.3 R10 行完全一致；`paginate` 排除 `limit` 的改动正确且不影响安全性。

---

## 核对项

### ① 跨客户不可见 — 通过

- `_derive_views`（state.py:222）逐条 `item.customer_id != customer_id → continue`，确保 `state.instructions` 与 `state.subscriptions` 仅含本客户数据。
- `paginate` 的游标 `scope` 绑定 `customer_id`（reads.py:38–40），无法通过伪造游标跨客户读取。
- 测试覆盖：`test_r7_cross_customer_invisible`（cus_ls 查询返回空、不含 cus_zs 的 si_zs_mom）、`test_r10_cross_customer_invisible`（cus_zs 查询不含 si_ls_video/si_ls_music）。两条均经真实 tmpfs PG + HTTP 断言。

### ② 只读、不写状态、复用 S3a loader — 通过

- `read()` 分支（reads.py:208–248）仅从 `state.instructions` / `state.subscriptions` 读取并序列化，无任何 DB 写、无 `execute()` 调用。
- `state.instructions` 与 `state.subscriptions` 由 `_derive_views`（state.py:218）从 S3a 的 `ObligationSnapshot.standing_instructions` 派生，复用 S3a 全部加载管线（`load_bank_state` → `_load_obligations` → `_derive_views`）。

### ③ 年化折算口径 — 通过

与 PRD §9.3 R10 行匹配：

| 项目 | PRD | 实现 | 位置 |
|------|-----|------|------|
| WEEKLY→年度 | ×52 | `billing.amount_cents * 52` | state.py:253 |
| MONTHLY→年度 | ×12 | `billing.amount_cents * 12` | state.py:253 |
| WEEKLY→月度 | 年度÷12 floor | `annual_cost_cents // 12` | reads.py:224 |
| MONTHLY→月度 | 不变 | `amount_cents` | reads.py:222 |
| normalization_basis | 输出因子 | `{weekly_to_annual_factor:52, monthly_to_annual_factor:12, annual_to_monthly:"floor_div_12"}` | reads.py:237–241 |

测试数字验算（`test_r10_annualization_weekly_monthly`）：
- 4 条 MONTHLY：video=3000, music=1500, cloud=2100, gym=19900
- 1 条 WEEKLY：amount=1000
- annual = 26500×12 + 1000×52 = 318000 + 52000 = 370000 ✓
- monthly = 26500 + 52000//12 = 26500 + 4333 = 30833 ✓

### ④ 合计只计 ACTIVE — 通过

- reads.py:218：`active = [v for v in state.subscriptions if v.status == "ACTIVE"]`
- `total_annual` 与 `total_monthly` 仅从此 `active` 列表求和。
- 测试覆盖：`test_r10_totals_only_active`（标记 video 为 DELETED 后 totals 排除它，status="ALL" 时 items 仍返回 4 条但 totals 仅 3 条 ACTIVE）、`test_r10_status_filter_deleted`（DELETED 筛选返回 1 条但 totals 仍只计 ACTIVE）。

### ⑤ paginate 排除 limit 的改动 — 通过

c41c29c 把游标 scope 从 `exclude={"cursor"}` 改为 `exclude={"cursor", "limit"}`。

**改动用途**：允许翻页时改变每页条数（如第一页 limit=1、第二页 limit=50），游标基于排序键定位而非行偏移。

**安全性分析**：
- 游标 scope 仍绑定 `customer_id` 与其余查询参数（如 `status`）；无法跨客户或跨状态复用游标。
- 游标 `after` 键是排序键（如 `(v.id,)`），不是行号；改变 limit 只影响返回条数，不影响定位正确性。
- 预改代码的游标 scope 含 limit，一旦部署所有旧游标失效（scope 不匹配 → 422），但游标为瞬时生成、不持久化，单次部署内无兼容性问题。

**对已有调用方的影响**（`list_payees`、`get_transactions`）：同理，允许改变页大小，安全属性无退化。

> 微瑕（见 P2-1）：`list_scheduled` 与 `list_subscriptions` 在相同 `(customer_id, status)` 下 scope digest 相同（`query` 均只含 `{"status": "..."} `），理论上可在两工具间互换游标。由于 L2 按工具路由、不会混用，且即使混用也不会跨客户泄露（`customer_id` 在 scope 中），实际风险极低。

### ⑥ 测试覆盖 — 通过，有可补项

| 覆盖维度 | R7 | R10 |
|----------|-----|------|
| 正常返回 | ✓ `test_r7_returns_scheduled_transfers` | ✓ `test_r10_returns_subscriptions_with_totals` |
| DELETED 筛选 | ✓ `test_r7_status_filter_deleted_returns_empty` | ✓ `test_r10_status_filter_deleted` |
| 跨客户不可见 | ✓ `test_r7_cross_customer_invisible` | ✓ `test_r10_cross_customer_invisible` |
| 无效游标 | ✓ `test_r7_invalid_cursor_returns_422` | ✓ `test_r10_invalid_cursor_returns_422` |
| 分页 | ✓ `test_r7_pagination_respects_limit` | **缺**（P2-2） |
| 空集合 | ✓（含在跨客户测试中） | **缺**（P2-3） |
| 合计仅 ACTIVE | N/A | ✓ `test_r10_totals_only_active` |
| 年化（WEEKLY） | N/A | ✓ `test_r10_annualization_weekly_monthly` |
| last_run 非 None | **缺**（P2-4） | N/A |

所有测试均经真实 tmpfs PG + HTTP 断言（非 mock），符合组件门禁要求。

---

## 发现

| 级别 | 编号 | 摘要 | 文件:行 |
|------|------|------|---------|
| P2 | P2-1 | `list_scheduled` 与 `list_subscriptions` 在相同 `(customer_id, status)` 下 `paginate` 的 scope digest 相同，游标可在两工具间互换（过滤结果无意义但不泄露数据） | reads.py:37–41 |
| P2 | P2-2 | R10 缺分页测试（R7 有 `test_r7_pagination_respects_limit`） | test_reads.py |
| P2 | P2-3 | R10 缺空集合测试（无 subscriptions 的客户如 cus_ls） | test_reads.py |
| P2 | P2-4 | R7 的 `last_run` 仅在无 run 记录时断言 None，缺「有真实 run 时返回正确 status/code/due_at」的覆盖 | test_reads.py:122, state.py:247 |

---

### P2-1 详析：跨工具游标 scope 碰撞

`paginate` 的 scope = `digest({"customer_id": cid, "query": args.model_dump(exclude={"cursor","limit"})})`。

`ListScheduledArgs(status="ALL")` 与 `ListSubscriptionsArgs(status="ALL")` 经 `model_dump(mode="json")` 后均得 `{"status": "ALL"}`，scope 一致。

如果用 list_subscriptions 的游标调 list_scheduled：
- scope 校验通过
- 但 `after` 键是 `billing_id` 元组（如 `["bill_zs_video"]`），与 list_scheduled 的 `(id,)` 键比较无意义
- 后果：过滤 `key(item) > cursor["after"]` 结果不可预测，但不跨客户、不修改数据

**建议修复**：在 scope 中加 `tool` 字段，或改用每个工具各自的 digest salt。风险极低，不阻塞合并。

### P2-2/P2-3：缺失测试

R10 有 6 项测试，R7 有 5 项，总量充足（11 条全部经真实 PG/HTTP）。缺失的两项覆盖（R10 分页、R10 空集合）不阻塞合并，可在后续补。

### P2-4：last_run 覆盖浅

`test_r7_returns_scheduled_transfers` 断言 `item["last_run"] is None`（fixture 中 si_zs_mom 无 run 记录）。缺「有 run 记录时 last_run 返回 `{status, code?, due_at}`」的覆盖。`_last_run` 实现在 state.py，逻辑为取最近一次 run 并返回其状态。不阻塞合并。