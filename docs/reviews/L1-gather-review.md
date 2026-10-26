# L1 Gather 补读开关 — 独立只读评审

**分支**: `codex/l1-gather-accounts`
**范围**: 5a80bd6..aa36945（8 次提交）
**文件**: 3 个文件（gather.py +74, main.py +1/−1, test_llm_adapters.py +160）
**结论**: **APPROVE** — 无 P0/P1，3 条 P2 不阻塞合并。

---

## 核查清单

### ① 补读只读、经 gateway 代理、无绕过 L2 ✅

`_supplement_reads` → `_execute_supplemental` → `self._read_executor(tool, args)`。
生产路径 `_build_production_ports` 注入真实 gateway 客户端，补读与其他 gather 读走同一 `ReadExecutor` 接口，
无绕过 L2 的路径。

**source_ref 来源**: `_execute_supplemental` 的 `ReadRecord.source_ref` 取 `result.source_ref`，
即 `ReadExecutor` 返回值（gateway 响应）。源码中虽有 `source_ref` 形参（`"src_gather_accounts"` / `"src_gather_payees"`），
但该参数在函数体内**从未使用**——两个调用点（`apply_untrusted_wrapping` 与 `ReadRecord`）都用 `result.source_ref`。
source_ref 确实来自 L2 响应，非本地编造。→ **P2-1（见下）**

### ② 补读结果同样做 untrusted 包装与去 wall ✅

`_execute_supplemental`（gather.py:203–205）与 `_execute`（gather.py:146–148）：
```python
wrapped = apply_untrusted_wrapping(
    strip_wall(result.data), result.untrusted_paths, result.source_ref
)
```
完全相同。补读数据经 `strip_wall` 去掉 L2 的 wall 包装，再经 `apply_untrusted_wrapping`
做 untrusted 标记，与其他 gather 读一致。

### ③ 意图匹配 ✅（过宽不成立，中文条目为死代码）

`_TRANSFER_LIKE_INTENTS`（gather.py:29–37）:
```python
{"transfer", "schedule_transfer", "智能转账", "转账", "定时转账"}
```
匹配逻辑（gather.py:180）:
```python
if not any(i in _TRANSFER_LIKE_INTENTS for i in understanding.intents):
```
understand adapter 返回的 intents 为英文（`transfer`、`schedule_transfer` 等），
所以 `"transfer"` / `"schedule_transfer"` 正确命中，中文三条目不会匹配到任何真实 intent，
属防御性死代码。→ **P2-2（见下）**

不会过宽：非转账意图如 `card`、`subscription`、`product` 的 English intents 均不在集合中，
测试 `test_gather_no_supplement_for_non_transfer` 已覆盖。

### ④ 补读失败行为 ✅

`_execute_supplemental` 调用 `self._read_executor`，无 try/except。
executor 抛出则直接传播到调用方。
测试 `test_gather_supplement_read_failure_propagates` 断言 `RuntimeError` 不被吞掉，通过。

### ⑤ with_read_executor 保留开关 ✅

gather.py:71–82:
```python
new = object.__new__(type(self))
new.__dict__.update(self.__dict__)   # 复制全部属性，包括 supplement_transfer_reads
new.__dict__["_read_executor"] = read_executor
```
`supplement_transfer_reads` 是实例属性（`self.supplement_transfer_reads = ...`），
`__dict__.update` 会一并复制，只在 `_read_executor` 上做替换。
并发 gather 节点每次 `with_read_executor` 得到的浅拷贝保留开关值。

### ⑥ 默认 False 的取舍 ✅ 合理

- 构造器默认 `supplement_transfer_reads=False`（gather.py:61）。
- 生产 `_build_production_ports` 显式传 `True`（main.py:204）。
- 现有测试不传该参数，沿用默认 `False`，行为不变——协调者实测 475 passed、零删改。

设计合理：默认关闭保证测试隔离（旧测试不意外触发补读），生产显式开启。
测试中需要补读行为的新用例显式传 `supplement_transfer_reads=True`。

### ⑦ plan 缺 rationale 的重试反馈 ✅

`PlanBody.rationale: str`（contracts/plan.py:93）为必填字段。
LLM 返回缺少 `rationale` 的 JSON 时，`PlanBody.model_validate` 抛 `ValidationError`，
被 `PlanAdapter.__call__` line 292 的 `except (LlmToolError, ValidationError, ValueError)` 捕获，
以 `validation_summary(exc)` 生成反馈文本，`max_retries=1` 时重试一次。
测试 `test_plan_retries_on_missing_rationale` 断言 2 次 LLM 调用且反馈含 `"rationale"`，通过。

注意：此逻辑并非本次新增（plan.py 未在 diff 中），测试是本次新增的。
测试验证现有重试机制能正确处理缺失 rationale 的情况。

---

## 发现

### P2-1: `_execute_supplemental` 的 `source_ref` 形参为死代码

**位置**: gather.py:200, 188–190, 193–195

`_execute_supplemental` 接受 `source_ref: str` 参数，调用方传入 `"src_gather_accounts"` / `"src_gather_payees"`，
但函数体内从未引用该参数——两处 `result.source_ref` 取的是 gateway 返回值。
参数名暗示可能来自本地，实际行为是正确的（来自 L2），但死参数误导读者。

**建议**: 删除 `source_ref` 形参，调用方去掉第三个参数。函数行为不变。

### P2-2: `_TRANSFER_LIKE_INTENTS` 中三条中文条目为死代码

**位置**: gather.py:33–36

`"智能转账"`, `"转账"`, `"定时转账"` 三个中文条目。
understand adapter 返回的 intents 为英文枚举值（`transfer`、`schedule_transfer` 等），
中文条目不会被命中。不影响正确性（English 条目已覆盖），但增加维护者疑问。

**建议**: 删除或加注释说明"防御性保留". 保留不阻塞（无害）。

### P2-3: `_execute_supplemental` 与 `_execute` 的包装逻辑重复

**位置**: gather.py:203–205 vs 146–148

两方法对 `strip_wall` + `apply_untrusted_wrapping` + `ReadRecord` 构造完全相同，
区别仅在于 `_execute_supplemental` 不追加 `ToolMessage`（因 LLM 已停止，无需反馈）。
若将来 ReadRecord 构造规则变化（如新增字段），需同步两处。

**建议**: 抽取共享的 `_build_read_record(result, tool, args)` 辅助方法。
当前不阻塞（两处仅 4 行重复）。

---

## 未覆盖场景（不阻塞，记录为后续参考）

- 补读返回空账户列表 / 空收款人列表时，plan 阶段的行为（依赖现有 PlanAdapter 的错误反馈）。
- 补读与 LLM 自己读了部分（如读了 get_accounts 但没读 list_payees）的组合——现有逻辑正确处理（tools_attempted 与 read_tools 两组检查），但缺少此场景的显式测试。
- `schedule_transfer` 意图的补读——理解模型当前返回 `schedule_transfer` 还是 `transfer`？若理解模型将所有定时转账归为 `transfer`，则 frozenset 中的 `schedule_transfer` 也是死代码。