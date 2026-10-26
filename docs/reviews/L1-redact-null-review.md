# L1 Redact Null Skip — 独立只读评审

**评审范围**: `8383a2c..704fff6` (2 提交: 红测 `70ee0ff`, 修复 `704fff6`)
**评审文件**: `services/agent/app/llm/redact.py`, `tests/agent/test_redact.py`
**评审日期**: 2026-10-27
**结论**: **APPROVE** — 无 P0/P1；P2×1 不阻塞合并

---

## 变更摘要

L2 在 live 评测中返回 `/items/0/alias` 为 untrusted 且值为 `null`。旧代码 `_wrap_at` 在 `isinstance(None, str)` 处抛 `ValueError`，导致 gather 失败。修复在 `_wrap_at` 的 dict 与 list 两分支中，`isinstance(current[leaf], str)` 检查之前插入 `if current[leaf] is None: return`，使 `null` 值被静默跳过而非报错。

---

## 逐项核对

### ① None 跳过是否可能让含文本的不可信内容绕过包装？

**结论：不会。**

- 检查是精确的 `is None`，不是宽松的 falsy 判断。空字符串 `""`、数字 `0`、布尔 `False` 都不会命中。
- `None` 之后 `isinstance(current[leaf], str)` 检查仍然存在并执行（对非 None 值）。dict/list/int/float/bool 类型的值全部继续 `raise ValueError`。
- 嵌套结构（dict/list 内嵌字符串）不会绕过：指针指向的是叶子节点本身。若叶子是 dict，`isinstance(dict_val, str)` → False，仍报错。
- `wrap_untrusted` 的目的（头注释）是 "Untrusted text is wrapped in a source-tagged marker"。`null` 没有文本可包装，跳过是语义正确行为。

### ② 指针不存在 / 越界 / 非字符串非 None 是否仍报错？

**结论：仍然报错。**

| 场景 | 守卫 | 是否受 None skip 影响 |
|---|---|---|
| 指针 key 不存在 (dict) | L119 `if leaf not in current: raise` | 否 — 在 None 检查之前 |
| 索引越界 (list) | L131 `if not 0 <= index < len(current)` | 否 — 在 None 检查之前 |
| 非字符串非 None (int/bool/dict/list) | L123/L135 `isinstance(current[leaf], str)` | 否 — None 已提前 return，其余仍走到此检查 |
| 无效 `~` 转义 | `_tokens` 未改动 | 否 |
| 指针穿越标量 | `_step` 未改动 | 否 |

`test_non_string_non_null_still_raises`（新增）覆盖了 dict（`/a` → `123`）与 list（`/items/0` → `123`）两分支的确仍然抛错。

### ③ 是否影响 PRD untrusted 包装语义？

**结论：不影响。**

头注释（redact.py L1–7）所述语义核心：
> "Untrusted text is wrapped in a source-tagged marker and escaped so the content cannot close or open the wrapper tag; this is an assistant-only aid, never an authorization."

None 值不含任何文本内容，不存在「需要标记但未标记」的语义缺口。头注释中「targets a non-string raises」的说法变得不精确（None 现为唯一例外），但这属于文档措辞问题（见 P2），不影响运行时安全。

### ④ 测试是否覆盖 dict 与 list 两分支？

**结论：是，三新增测试覆盖充分。**

| 测试 | 覆盖分支 | 验证内容 |
|---|---|---|
| `test_null_dict_leaf_skipped_no_error` | dict 分支 (L118–126) | `{"alias": None, "memo": "ok"}` — None 被跳过、同层的 str 仍被包装 |
| `test_null_list_element_skipped_no_error` | list 分支 (L127–138) | `["a", None, "c"]` — None 被跳过、相邻 str 仍被包装 |
| `test_non_string_non_null_still_raises` | dict + list | int 值在两种容器中均抛 ValueError |

测试还隐含验证了：None skip 不会阻止同一 `apply_untrusted_wrapping` 调用中其他指针的正常包装（`memo` 与 `tags[0]` 断言）。

---

## 发现

### P2-1：头注释「non-string raises」描述不再精确

`redact.py:7`：「A pointer that misses, goes out of bounds, uses an invalid `~` escape, or targets a non-string raises instead of silently skipping。」

修复后 `None`（JSON `null`）作为非字符串被静默跳过，而非 raise。头注释应同步为：「…or targets a non-string, non-null raises…」（或等效措辞）。

**严重程度**: P2 — 文档不精确，不影响运行时安全，不阻塞合并。

---

## 未发现问题

- 无 P0（资金安全/授权绕过/数据泄露）
- 无 P1（功能缺陷/测试缺失/规范不一致）
- 代码改动极小（+4 行生产，+24 行测试），风险可控
- 协调者已亲验红绿核验通过（旧代码 2 failed → 修复 12 passed）