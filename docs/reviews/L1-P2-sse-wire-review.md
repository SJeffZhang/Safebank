# L1-P2 SSE 结构化编码独立评审

**评审者**: Claude（无头 DeepSeek，全新会话）
**评审日期**: 2026-10-26
**评审范围**: `00aca15..efde4d7` 中 `services/agent/app/main.py` 与 `tests/agent/test_sse_wire.py`
**亲跑命令**: `ruff check services/agent/app/main.py tests/agent/test_sse_wire.py`（exit 0）

## 1. 背景

旧代码 `json.dumps(final_state.values, default=str)` 将 Pydantic `Decision`、`ExecutionResult` 等模型序列化为 Python repr 字符串（如 `Decision(plan_id='pln_test', decision=<DecisionValue.ALLOW: 'ALLOW'>)`），下游（try-agent、BFF）无法可靠解析。

修复引入 `_state_json_default`：`BaseModel.model_dump(mode="json")`、`Decimal` 转 `str`、`datetime`/`date` ISO、`set`/`frozenset` 转 `list`、`bytes` 转 `hex`，不支持类型抛出 `TypeError`。同时修复 `updates`（`__interrupt__`）事件和 `values` 事件。

## 2. 逐项核查

### 2.1 `json.dumps` 全部调用点核查（main.py）

| 行 | 事件 | 调用 | 是否需 `_state_json_default` | 结论 |
|---|---|---|---|---|
| 484 | `updates` (`__interrupt__`) | `json.dumps({"__interrupt__": interrupt_events}, default=_state_json_default)` | 是 — interrupt value 可能含模型 | ✅ 已修 |
| 494 | `values` | `json.dumps(final_state.values, default=_state_json_default)` | 是 — values 含 Decision/ExecutionResult | ✅ 已修 |
| 504 | `custom` (cards) | `json.dumps({"type": "cards", "cards": card_dicts})` | 否 — `card_dicts` 已提前 `model_dump(mode="json")` | ✅ 正确 |
| 515 | `messages` | `json.dumps([{"type": "ai", "content": reply_text}])` | 否 — 纯字符串 | ✅ 正确 |
| 523 | `error` | `json.dumps({"message": str(exc), "type": type(exc).__name__})` | 否 — `str(exc)` 与 `__name__` 均为字符串 | ✅ 正确 |

结论：**两个 Pydantic 承载事件均已修复，其余三个事件不承载模型，无需修复。** 无遗漏。

### 2.2 `_state_json_default` 类型覆盖

| 类型 | 处理方式 | 是否正确 | 备注 |
|---|---|---|---|
| `BaseModel` | `model_dump(mode="json")` | ✅ | `DecisionValue`(StrEnum)→`"ALLOW"`，`tuple`→`list`，嵌套模型递归展开 |
| `Decimal` | `str(obj)` | ✅ | 保留精度，不会变成 float |
| `datetime` / `date` | `.isoformat()` | ✅ | ISO-8601 |
| `set` / `frozenset` | `list(obj)` | ✅ | JSON 无集合类型 |
| `bytes` | `obj.hex()` | ✅ | 紧凑十六进制 |
| 不支持类型 | `TypeError` | ✅ | fail-closed，不静默降级 |

当前 AgentState 顶层值的实际类型：
- `Decision` (BaseModel) → `model_dump(mode="json")` ✅
- `ExecutionResult` (BaseModel, 来自 contracts) → `model_dump(mode="json")` ✅
- `Understanding` (BaseModel) → `model_dump(mode="json")` ✅
- `UtteranceRef` (BaseModel, `UtteranceKind` StrEnum) → `model_dump(mode="json")` ✅
- `str`, `int`, `bool`, `None`, `list[dict]`, `dict` → 标准 JSON ✅
- `PlanDraft` (BaseModel, 来自 contracts) — 若在 values 中 → `model_dump(mode="json")` ✅
- `ReadRecord` (BaseModel) → `model_dump(mode="json")` ✅

结论：**当前所有实际出现在 state.values 中的类型均有正确处理。** `model_dump(mode="json")` 确保 `StrictInt` 保持为整数（不变成 float）。

### 2.3 `updates` (`__interrupt__`) 路径

行 476–488：`_serialize_interrupts` 从 LangGraph tasks 提取 `intr.value` 和 `intr.id`，包装为 `{"value": ..., "id": ...}` 的纯 dict。`_state_json_default` 在此路径上对**当前中断值类型**（字符串/数字/纯 dict）无实际作用，但若未来 LangGraph 中断值含 BaseModel，会正确序列化。应用此编码器是正确的防御性做法。

### 2.4 测试覆盖（test_sse_wire.py，5 条）

| 测试 | 覆盖内容 | 评价 |
|---|---|---|
| `test_values_decision_is_dict_not_repr` | ALLOW 决策：`decision` 为 dict，`decision.decision == "ALLOW"` | ✅ 核心修复验证 |
| `test_values_execution_is_dict_with_status` | 执行结果：`execution` 为 dict，`status == "EXECUTED"`，`replayed` 为 bool | ✅ 验证 ExecutionResult 非 repr |
| `test_values_require_approval_decision` | REQUIRE_APPROVAL 决策含 `approval_id` | ✅ 验证审批路径 |
| `test_values_is_valid_json_parseable` | 递归遍历 values 所有节点，确保无 `Decision(` / `ExecutionResult(` / `<DecisionValue.` repr 片段 | ✅ 全面扫描 |
| `test_get_state_same_as_values_event` | GET `/threads/{id}/state` 与 SSE values 事件的 `decision.plan_id` 和 `decision.decision` 一致 | ⚠️ 见 P2-1 |

### 2.5 GET /state 端点（行 349–383）

行 383：`return result` — FastAPI 的 `jsonable_encoder` 处理嵌套 Pydantic 模型，理论上与 `model_dump(mode="json")` 行为接近。但两者是**不同代码路径**，`test_get_state_same_as_values_event` 仅断言 `plan_id` 和 `decision` 值，不验证完整结构等价。见下 P2-1。

### 2.6 安全：无 token 泄露

`_state_json_default` 不新增任何输出通道。`model_dump(mode="json")` 仅输出 Pydantic 模型字段值，不输出类名、模块路径或内部属性。旧 `default=str` 会泄露 Pydantic 类名（如 `Decision(`），新编码器反而不泄露。✅

### 2.7 变异核验（协调者已做，本评审核实逻辑）

协调者报告：换回旧 `default=str` 后 `test_sse_wire` 4 failed / 1 passed。4 条失败均因 `decision`/`execution` 为 repr 字符串而非 dict。逻辑自洽：`test_values_is_valid_json_parseable` 的递归扫描会命中所有 repr 片段。

### 2.8 其他文件中的 `default=str`

`services/agent/app/llm/` 下 6 处 `default=str` 调用（live.py、replay.py、respond.py、gather.py、plan.py）。这些是 LLM 内部路径（哈希键、工具调用构造、日志截断），**不使用 `_state_json_default` 是正确的**——`replay.py` 的哈希键需要确定性摘要（`default=str` 对 Pydantic 模型产生稳定的 repr），不应与 SSE 编码器混用。✅

## 3. 发现

### P0：无

### P1：无

### P2

#### P2-1 GET /state 与 SSE values 事件序列化路径不同，等价性断言过窄

**位置**: `main.py:375-383` vs `main.py:494`；`test_sse_wire.py:345-362`

**说明**: GET `/state` (行 383 `return result`) 经 FastAPI `jsonable_encoder` 序列化；SSE values 事件 (行 494) 经 `_state_json_default` 序列化。`test_get_state_same_as_values_event` 仅断言 `decision.plan_id` 与 `decision.decision` 值相等，不验证 `execution`、`understanding`、`reads` 等其他键的结构等价。若未来 contracts 模型新增 `bytes` 字段（Pydantic JSON 模式: base64；`_state_json_default` 顶层: hex）或 `Decimal` 字段（FastAPI jsonable_encoder: float？；`_state_json_default`: str），两条路径可能产生不同输出。

**严重度**: P2 — 当前真实 AgentState 中无触发分歧的类型，且狭义断言已验证核心字段一致。

**建议**: 至少断言 `execution.status` 与 `execution.plan_id`；若 contracts 新增非基本类型字段，补完整结构等价测试。

#### P2-2 `bytes` 在不同嵌套深度的编码不一致（设计已知）

**位置**: `main.py:56-57`

**说明**: `_state_json_default` 将顶层 `bytes` 编码为 `.hex()`；Pydantic `model_dump(mode="json")` 将模型内 `bytes` 字段编码为 base64（RFC 4648）。若同一逻辑值出现在不同嵌套层级（如顶层 state 键 vs 模型字段），消费者会看到两种编码。

**严重度**: P2 — 当前 AgentState 中无 `bytes` 值。`set`/`frozenset` 无此问题（Pydantic JSON 模式也序列化为 list）。

**建议**: 在 `_state_json_default` 文档注释中注明此设计选择。若未来有跨层级 `bytes` 值，统一为一种编码（倾向于 `.hex()` 与 JSON 文本兼容性更好）。

#### P2-3 缺少对 TypeError fail-closed 行为的回归测试

**位置**: `main.py:58` (`raise TypeError`)

**说明**: `_state_json_default` 对不支持类型抛出 `TypeError`（而非静默降级为 `str()`），这是正确的 fail-closed 设计。但无测试证明此行为不会被意外弱化（例如未来有人加 `except Exception: return str(obj)`）。

**严重度**: P2 — 设计正确，只是缺少回归护栏。

**建议**: 新增 1 条单元测试：构造含不支持类型（如 `object()`）的 dict 传入 `json.dumps(..., default=_state_json_default)`，断言 `TypeError`。

#### P2-4 中断事件路径未独立测试 `_state_json_default` 的作用

**位置**: `main.py:482-487`；`test_sse_wire.py` 无中断相关测试

**说明**: 当前 `updates` 事件（`__interrupt__`）在 5 条测试中未被覆盖。虽然当前中断值为纯 dict（`_state_json_default` 仅作防御），但缺少测试意味着未来中断值格式变化（如 LangGraph 升级后中断值含 Pydantic 模型）无回归信号。

**严重度**: P2 — 当前中断值不触发序列化问题，且相同编码器在 `values` 路径已有充分覆盖。

**建议**: 可留待 P2 真实三层联调阶段，在 try-agent 中自然覆盖中断路径。

## 4. 回答协调者的具体问题

| 问题 | 结论 |
|---|---|
| 是否充分证明真实 HTTP/SSE/GET state 一致？ | **基本充分**。SSE values 与 GET /state 的 decision 键已交叉验证。`TestClient` 走真实 Starlette 请求/响应管线（非 mock transport），等价于真实 HTTP 行为。完整结构等价性见 P2-1。 |
| 已执行与仅 ALLOW 不混淆？ | **不混淆**。ALLOW 测试断言 `execution.status == "EXECUTED"`（dict 形式），REQUIRE_APPROVAL 测试断言 `approval_id is not None`。旧 repr 格式下 `execution` 会是 `ExecutionResult(...)` 字符串，无法提取 `.status`。 |
| 整数分/Decimal 不 float？ | **不会**。`StrictInt` 经 `model_dump(mode="json")` 保持为 Python `int` → JSON number。`Decimal` 显式 `str()` → JSON string。JSON 中 `100` vs `100.0` 可区分。 |
| 无 token 泄露？ | **无**。`model_dump(mode="json")` 只输出模型字段值，不输出类名/路径。新编码器比旧 `default=str`（会输出 `Decision(` 类名）更安全。 |
| 无不必要 helper 能力？ | **无不必要能力**。`_state_json_default` 只覆盖当前实际出现的类型 + 防御性 `set`/`frozenset`/`bytes`。无 `__reduce__`、`__repr__` 等宽泛回退。 |
| 原 interrupt 扁平格式保持？ | **保持**。`_serialize_interrupts` 未修改，interrupt 事件的 `{"value": ..., "id": ...}` 结构不变，仅 `json.dumps` 的 default 参数改变。 |

## 5. 总体结论

**APPROVE** — 无 P0/P1 缺陷。

修复精准：两个承载 Pydantic 模型的 SSE 事件（`updates` 的 `__interrupt__` 和 `values`）从 `default=str` 改为 `_state_json_default`，其余三个纯 dict 事件不受影响。`_state_json_default` 的类型覆盖完整且 fail-closed（不支持类型抛 TypeError）。

5 条测试覆盖核心路径（ALLOW/REQUIRE_APPROVAL/EXECUTED/全量 repr 扫描/GET state 交叉验证），变异核验逻辑自洽。4 条 P2 均为低风险改进建议，不阻塞合并。

**未亲跑命令**（评审者未执行 Docker/真实 PG，仅静态分析 + ruff）：`ruff check` exit 0，其余依赖协调者已报告的 `AGENT_DB_TESTS=1 pytest tests/agent/test_sse_wire.py -q` 5 passed / AGENT_DB_TESTS=1 pytest tests/agent/test_service.py -q 39 passed。

协调者证据口径更正：39 passed来自整个make test-agent-db（含新增5条），不是test_service.py单文件39条；新增测试的旧实现变异为4 failed/1 passed。评审者未亲跑PG，实际命令以验收文档为准。
