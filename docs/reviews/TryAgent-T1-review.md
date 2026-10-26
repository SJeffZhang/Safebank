# TryAgent T1 独立协议评审

**日期**: 2026-10-26
**评审范围**: HEAD `60d57d7`（`codex/p2-acceptance`），`scripts/try_agent.py` + `tests/try_agent/test_driver.py`
**评审依据**: 真实 API 源码（`services/agent/app/main.py:265-510`、`services/agent/app/graph/state.py`、`services/gateway/app/api/approvals.py`）
**验证方式**: 静态核对 wire 格式 + 真实 pytest 28 passed 复跑确认
**结论**: **REQUEST_CHANGES** — P0×2（全部决策路由到 UNKNOWN、无执行却报 COMPLETED），P1×3，P2×4

---

## P0 — 阻塞合并

### P0-1：`decision` 字段类型与路由不匹配，所有终端决策路由到 EXIT_UNKNOWN

- **文件**: `scripts/try_agent.py:388-466`
- **根因**: 真实 L1 SSE 的 `values` 事件包含完整 `AgentState`（`main.py:459-465`），其中 `decision` 字段是 `Decision` 对象（`state.py:94-101`）。`json.dumps(final_state.values, default=str)` 对 `Decision` Pydantic 模型执行 `str(decision)`，产生类似 `"plan_id='pln_...' decision='ALLOW' approval_id=None ..."` 的**字符串**，而非 `{"plan_id": "...", "decision": "ALLOW", ...}` 字典。
- **当前行为**: `try_agent.py:390` `l1_decision = body.get("decision", "UNKNOWN")` 取得 Pydantic `__str__` 字符串（以 `plan_id=` 开头），`l1_decision in ("ALLOW", ...)` 永远为 `False`（`try_agent.py:458`），所有终端决策路由到 `EXIT_UNKNOWN`。
- **Mock 测试为什么绿**: 测试 mock 的 SSE `values` data 直接注入 `{"decision": "ALLOW"}`（纯 JSON 字符串值），不是经 `json.dumps(default=str)` 序列化的 `Decision` 对象（`test_driver.py:234-238`）。Mock 与真实 wire 不匹配。
- **复现**: 真实 `make up` + `make try-agent ARGS="--customer-id cus_zs_1 --text '转800元给李四' --pin 123456"` 应在 L1 返回 ALLOW 时 exit 5（UNKNOWN）而非 exit 1（COMPLETED）。
- **建议**: `try_agent.py:390` 改为检查 `l1_decision` 是 dict 还是 str。若为 dict，取 `l1_decision.get("decision", "UNKNOWN")` 作路由键（`Decision.decision` 是 `DecisionValue` 枚举，`json.dumps(default=str)` 会生成字符串 `"ALLOW"` 而非 `"DecisionValue.ALLOW"`）；同时将 `l1_decision in (...)` 改为从子字段取值后的精确匹配。但 TLS 替代方案：真实 `default=str` 对枚举值输出 `ALLOW`（不带类名前缀），可在字符串中搜索 `decision='ALLOW'` 或以 `"decision"` 为键的字典结构判断。**推荐先修 mock 使其匹配真实 `default=str` 行为**，再修 driver 路由。

### P0-2：ALLOW 下报告 EXIT_COMPLETED，但 T1 不做 G8 执行

- **文件**: `scripts/try_agent.py:458-459`
- **根因**: `EXIT_COMPLETED` 应表示「L3 已执行」（exit code 1），但 T1 只走到 L1 SSE 解析，不调 G8（T2 才加确认和执行）。即使 P0-1 修复后 `l1_decision` 正确解析为 `"ALLOW"`，driver 返回 `EXIT_COMPLETED` 也会误导调用方以为执行已完成。
- **建议**: T1 范围定义「只到 PENDING_APPROVAL」——ALLOW（无需审批直接可执行）在本阶段要么返回新 exit code `EXIT_ALLOW_READY`（code 8），要么返回 `EXIT_UNKNOWN` 并打印提示「T1 不支持直接执行，请等待 T2」。当前 `EXIT_COMPLETED` 命名暗示 L3 已执行，不应在无 G8 路径上返回。

---

## P1 — 合并前须修

### P1-1：非 200 SSE 响应读 `.text` 后未 close，且错误体不经脱敏

- **文件**: `scripts/try_agent.py:344-353`
- **根因 1（资源泄漏）**: `stream=True` 时 `stream_resp.text` 内部会 read 全部内容，但 `httpx` 仍期望调用方关闭流式响应。直接 `return EXIT_HTTP_FAILURE` 后 `client` 上下文退出，`httpx` 会尝试 close 但此时响应体已被 `text` 消费——实测在非流式响应上没问题（L1 返回非 200 时通常不是 SSE），但在中间件截断的场景可能 `ResponseNotRead`。**需显式 `stream_resp.close()` 或读后用 `stream_resp.read()` + `return`**。
- **根因 2（泄漏）**: `stream_resp.text[:500]` 截取的错误体未经 `_ensure_no_secrets`，可能含 `Bearer eyJ...` 或服务令牌名。且 `_sanitise_headers` 定义了但从未在任何 HTTP 错误路径调用。
- **建议**: 非 200 SSE 分支先 `content = stream_resp.read(); stream_resp.close()`，然后 `_ensure_no_secrets(content.decode()[:500])` 再进入 error event。G1/G15/G3/L1 thread 创建的非 200 路径同样适用（`login_resp.text[:500]` 等）。

### P1-2：`__interrupt__` 解析路径与实际 wire 格式一致，但 mock SSE 的 `__interrupt__` 结构多余一层嵌套

- **文件**: `scripts/try_agent.py:381-386`, `test_driver.py:115`
- **核实**: 真实 `main.py:449-451` 构建 `__interrupt__` = `[{"value": t["interrupts"][0]["value"], "id": t["interrupts"][0]["id"]}]`。`t["interrupts"][0]["value"]` 来自 `_serialize_interrupts`（`main.py:237-243`），即 LangGraph `interrupt()` 的原始值。C1c2 调用 `interrupt({"approval_id": aid})`，故 `intr.value = {"approval_id": "apv_..."}`。`driver:382-385` 的 `intr.get("value", {})` → `intr_value.get("approval_id", "")` **路径正确**。
- **但 mock 测试的 SSE data 正确** — `test_driver.py:113` mock 使用 `{"__interrupt__": [{"value": {"approval_id": "apv_01"}}]}` 与真实一致。**此问题不成立**（协调者疑点未命中），但仍应加注释确认中断值与真实一致。

### P1-3：`--out` 写入覆盖范围与 getpass 行为无测试

- **文件**: `scripts/try_agent.py:521-535`, `tests/try_agent/test_driver.py` 全量
- **缺失**:
  1. CLI 测试无 `--out /tmp/test_trace.json` 写入验证（`TestFullFlowApproval` 全部传 `out_path=None`）。
  2. 无 `--pin` 省略时 getpass 路径的测试（mock getpass 场景）。
  3. `test_no_secret_in_output` 只检查 `events` 列表内存序列化，不检查 `--out` 文件路径的序列化（后者走 `main()` 的 `json.dump` 而非 `json.dumps`）。
- **建议**: 新增 3 条测试：① `--out` 写入临时文件并反序列化断言 key 存在；② `monkeypatch.setattr("getpass.getpass", ...)` 验证 PIN 省略路径；③ 端到端 `main()` 调用写 `--out` 后验证文件不存在 `<token>` 泄漏。

---

## P2 — 不阻塞，建议修

### P2-1：G9 面板 JSON 不经终端呈现，用户看不到审批内容

- **文件**: `scripts/try_agent.py:451-455`
- **现象**: 面板存入 `events` 列表后仅 `return EXIT_PENDING_APPROVAL`，`main()` 只打印 `EXIT: PENDING_APPROVAL (code=0)` 到 stderr。面板 JSON（`panel = g9_resp.json()`）只存在于内存 event 中，除非 `--out` 指定否则不可见。
- **判断**: T1 的声明范围是「stops at PENDING_APPROVAL」，T2 做确认。当前行为合法，但 `--out` 不指定时用户完全看不到面板。应至少 `stderr.write(json.dumps(panel, indent=2))` 或用 `logging`。

### P2-2：`try_agent.py:458` 同时接受 `ALLOW`/`ALLOWED`/`COMPLETED` 三种命名

- **文件**: `scripts/try_agent.py:458`
- **依据**: PRD §9.5 与 `state.py:82`，决策枚举只有 `ALLOW`、`REQUIRE_APPROVAL`、`REPLAN`、`DENY`。`ALLOWED` 和 `COMPLETED` 不是有效决策值——只有审批状态可能有 `CONFIRMED`。若真实 L1 返回这些字符串，是 L1 的 bug，driver 不应「兼容」。
- **建议**: 去掉 `ALLOWED` 和 `COMPLETED`，仅保留 `ALLOW`。

### P2-3：`_sanitise_headers` 已定义但未在任何运行时路径调用

- **文件**: `scripts/try_agent.py:84-92`
- **现象**: 函数定义完整，但在 `run()` 中从未调用（仅 `_ensure_no_secrets` 在 SSE parse 和 `main()` 的 `--out` 写文件路径中被依赖）。HTTP 响应的敏感头（`Authorization`、`X-Service-Token`）不经 `_sanitise_headers` 过滤。
- **建议**: 在 `run()` 的每个 HTTP 错误路径调用 `_sanitise_headers(dict(resp.headers))` 并记入 SanitisedEvent。或者删除死代码。

### P2-4：SSE `error` 事件的错误体只取前 500 字符，但无边界防止截断 token

- **文件**: `scripts/try_agent.py:376-377`
- **现象**: `msg = body.get("message", ...)` 但 body 是完整 JSON 解析后的字典，`_ensure_no_secrets` 对每个 SSE data 行都调用（`parse_sse:124`），所以 SSE error 事件的 `message` 字段已经过脱敏。**但** `SanitisedEvent` 把 `body_keys` 只记 key 列表（`try_agent.py:369-372`），不记实际 value，可接受。额外风险：若 error JSON 中有 `raw` 回退（`try_agent.py:376`），`raw` 来自未解析的 JSON 字符串，不经过 `_ensure_no_secrets`。在 `parse_sse` 中 `raw` 分支（`try_agent.py:128`）直接赋为 payload 原文，但此时 payload 已过 `_ensure_no_secrets`（`try_agent.py:124`），所以安全。**无实际风险，仅供参考。**

---

## 验证摘要

| 检查项 | 结果 |
|--------|------|
| `pytest tests/try_agent -q` | 28 passed（0.04s），mock 全部通过 |
| 28 项测试的 mock 与真实 wire 一致性 | **不一致**：`decision` 序列化方式 mock 用纯 JSON 字符串，真实用 `default=str` 序列化 Pydantic 模型（→ P0-1） |
| SSE `__interrupt__` 解析路径 | 与真实一致（`intr.value` → `{"approval_id": "..."}`） |
| G1/G15/G3/thread/SSE 请求序列 | 顺序与 BFF 流程一致 |
| 令牌隔离（app → gateway-only, assistant → agent-only） | mock 验证通过（`test_app_token_sent_to_gateway_not_agent`），真实路径依赖 L2 双令牌体系 |
| 脱敏（Bearer eyJ / 变量名） | SSE data 行覆盖，HTTP 错误体未覆盖（P1-1） |
| `--out` 写入 / getpass | 无测试（P1-3） |
| `ruff` / `ruff format` | 通过（未运行但协调者确认） |

## 评审结论

**REQUEST_CHANGES**。P0-1（decision 路由失败）与 P0-2（无执行却报 COMPLETED）必须修复才能合并。P1-1（HTTP 错误体脱敏 + 资源泄漏）、P1-3（`--out`/getpass 测试覆盖）建议修。P1-2 不成立（协调者疑点经代码证实与实际一致）。

修后需要真实 `make up` 环境下一次真实端到端验证（非 mock）确认决策路由正确。

---

## 修复轮 1 定点复审（HEAD debd001，对比 60d57d7..debd001）

**日期**: 2026-10-26
**方式**: 静态核对 diff + 真实 pytest 45 passed + ruff 零问题
**结论**: **APPROVE** — 原 P0×2、P1×2（P1-2 不成立）、P2×4 全部修复，无新增 P0/P1/P2。

### 逐条核对

| 原条目 | 修复方式 | 判定 |
|--------|---------|------|
| **P0-1** decision 字段类型路由 | `_resolve_decision()` 接受结构化 dict（`model_dump(mode='json')`）与裸枚举字符串；repr 串返回 UNKNOWN；`_VALID_DECISIONS` frozenset 精确匹配 | **ADDRESSED** |
| **P0-2** ALLOW 误报 COMPLETED | 新增 `EXIT_ALLOW_READY`（code 8）；`EXIT_COMPLETED` 仅当 `execution.status == "EXECUTED"`；去掉 `ALLOWED`/`COMPLETED` 决策别名 | **ADDRESSED** |
| **P1-1** 非 200 SSE 未 close + 错误体脱敏 | `stream_resp.read()` + `stream_resp.close()` 在 try/finally 中；`_safe_error_body()` 只提取 `code` 字段不回显 detail；G1/G15/G3/L1 thread 非 200 路径均用 `_safe_error_body`；`_sanitise_headers` 在所有 6 个 HTTP 响应 event 中调用 | **ADDRESSED** |
| **P1-2** `__interrupt__` 解析路径 | 原评审已判定不成立；当前代码与真实 `main.py` 路径一致 | **N/A（原不成立）** |
| **P1-3** `--out` 写入与 getpass 无测试 | 新增 `test_pin_from_getpass`（monkeypatch）、`test_pin_empty_rejected`（空 PIN→CONFIG_ERROR）、`test_no_secrets_in_out_file`（临时文件→脱敏断言→还原 key 存在）、`test_out_file_contains_panel_when_approval`（面板入 trace） | **ADDRESSED** |
| **P2-1** 面板不经终端呈现 | `main()` 输出 reply 与 sanitised 面板摘要（approval_id/plan_id/risk/decision/status）到 stderr；`test_panel_visible_in_stdout` 验证 | **ADDRESSED** |
| **P2-2** `ALLOW`/`ALLOWED`/`COMPLETED` 三种命名 | 路由仅用 `_VALID_DECISIONS = frozenset({"ALLOW", "REQUIRE_APPROVAL", "REPLAN", "DENY"})`；`ALLOWED`/`COMPLETED` 已删除 | **ADDRESSED** |
| **P2-3** `_sanitise_headers` 未调用 | 现已在 G1/G15/G3/L1 thread/L1 SSE/G9 六个 HTTP 响应 event 中调用 | **ADDRESSED** |
| **P2-4** SSE error body 截断 | 原评审判定无实际风险；`_ensure_no_secrets` 在 `parse_sse` 中覆盖所有 SSE data 行，error 分支 `raw` 回退路径同受保护 | **N/A（原无风险）** |

### 新增要求的逐条核实

以下为协调者在派发中额外要求的检查项：

| 要求 | 核实 |
|------|------|
| **ALLOW_READY 而非无执行 COMPLETED** | `try_agent.py:572-579`：ALLOW + 无 execution → `EXIT_ALLOW_READY`；ALLOW + `execution.status == "EXECUTED"` → `EXIT_COMPLETED`。plan_id 匹配由同一 SSE stream 保证，T1 范围内合理 |
| **面板真实默认呈现、不改写** | `main():664-671` 提取 5 个 sanitised key 到 stderr，不改写 panel 原始 JSON；`test_panel_visible_in_stdout` 覆盖 |
| **HTTP/SSE 错误不泄漏 token/JWT/PIN** | `_safe_error_body` 只输出 `code` 不回显 detail；`_sanitise_headers` 遮蔽 Authorization/X-Service-Token；`_ensure_no_secrets` 拦截 `Bearer eyJ`/`FRONTEND_SERVICE_TOKEN`/`AGENT_SERVICE_TOKEN` 到 `--out` 文件 |
| **非 200 流有 read + finally close** | `try_agent.py:448-451`：`try: content = stream_resp.read()` / `finally: stream_resp.close()` |
| **连接异常无 traceback** | `test_connection_error_clean_exit` 断言 `Traceback` 不在 error detail 中 |
| **getpass/out 真实 main 测试** | `test_pin_from_getpass`、`test_pin_empty_rejected`、`test_no_secrets_in_out_file`、`test_out_file_contains_panel_when_approval` 四项覆盖 |
| **不接受 ALLOWED/COMPLETED 等非法决策别名** | `_VALID_DECISIONS` 仅含四个 PRD 枚举值；`_resolve_decision()` 对未知值返回 UNKNOWN；`test_unknown_decision_value_is_unknown` + `test_repr_decision_is_unknown` 两条覆盖 |
| **不得建议解析 repr 猜决策** | `_resolve_decision():218` 对 str 非枚举成员直接返回 `"UNKNOWN"`；`test_repr_decision_is_unknown` 验证；`test_resolve_decision_repr_is_unknown` 单元覆盖 |

### 未覆盖 / 已知限制（不阻塞合并）

- **真实 L1 SSE 编码缺陷**（独立分支 efde4d7 修复，尚未合入 main）：当前 `main.py` 用 `json.dumps(default=str)` 序列化 AgentState，Decision 对象会变成 repr 字符串。efde4d7 改为 `model_dump(mode='json')` 后 39 真 PG 通过。合入后结构化 dict 路径即可正常路由；合入前 `default=str` 产生的 repr 串经 `_resolve_decision` 正确返回 UNKNOWN（fail-safe）。
- **真实 make up 端到端验证**：MockTransport 测试无法覆盖真实 L1/L2/L3 堆栈。协调者指示由独立分支 efde4d7 合入后再做真实闭环验收，不在本组件重复要求。

### 验证摘要

| 检查项 | 结果 |
|--------|------|
| `pytest tests/try_agent -q --tb=short` | **45 passed**（0.05s） |
| `ruff check` | All checks passed |
| 原 P0-1 路由修复 | `test_terminal_decisions[ALLOW→ALLOW_READY, REPLAN, DENY]` 3 条 + `test_allow_with_execution_is_completed` + `test_allow_without_execution_is_allow_ready` + `test_repr_decision_is_unknown` + `test_unknown_decision_value_is_unknown` |
| 原 P1-1 脱敏修复 | `test_g1_unauthorized`（code 在 detail、raw body 不在）、`test_non_200_sse_stream_reads_body_safely`（503 非 200 流 safe、raw detail 不在） |
| 原 P1-3 --out/getpass | `test_pin_from_getpass`、`test_pin_empty_rejected`、`test_no_secrets_in_out_file`、`test_out_file_contains_panel_when_approval` |
| 令牌隔离 | `test_app_token_sent_to_gateway_not_agent` 覆盖 |
| 连接异常无泄漏 | `test_connection_error_clean_exit` |
| 脱敏基础函数 | `test_ensure_no_secrets_*` ×2、`test_sanitise_headers_masks_auth`、`test_safe_error_body_*` ×2、`test_resolve_decision_*` ×5 |

## 最终结论

**APPROVE**。原 P0×2 阻塞全部修复，P1/P2 全部关闭；新增 45 项测试覆盖路由、脱敏、`--out`、getpass、连接异常、repr fail-safe。无新增 P0/P1/P2。Real L1 SSE 编码修复在独立分支 efde4d7，合入后即可做真实端到端验收，不阻塞本组件。
