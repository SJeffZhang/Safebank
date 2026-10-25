# C1e Design Notes — Output Verification, Number Masking, Deterministic Cards & Replay

> 设计起草 worker（只读代码，本文档记录代码事实、PRD 缺口、待裁定问题）。
> 基于 main 0472689，分支 `codex/c1e-output-replay`。

---

## 1. 代码事实

### 1.1 respond / verify_output 当前实现

`services/agent/app/main.py:201–221` — 两个生产端口都是透传：

```python
# _make_production_respond (line 201–209)
def respond(state, _context) -> str:
    reply = state.get("reply_draft", "") if hasattr(state, "get") else ""
    if reply:
        return reply
    return "您的请求已收到。"

# _make_production_verify (line 213–220)
def verify_output(state, _context) -> str:
    if hasattr(state, "get"):
        return state.get("reply_draft", "") or "Done"
    return "Done"
```

C1e 必须替换这两个 callable。

### 1.2 Graph 中 respond 节点的逻辑

`services/agent/app/graph/graph.py` 的 `build_gateway_graph` 中 respond 节点（约 L100–130）：

- 若 `state.get("reply_draft")` 已存在（路由错误路径已填好），直接返回，不调用 `ports.respond`
- 否则调用 `ports.respond(state, runtime.context)`
- 把返回值写入 `state["reply_draft"]`
- 然后进入 `verify_output`

### 1.3 固定模板当前状态

`services/agent/app/graph/routing.py` 第 ~10–80 行定义了 23 个模板 key（DENY、REPLAN_AGENT_EXHAUSTED、REPLAN_USER、AUTHORIZATION_EXPIRED、AUTHORIZATION_SUPERSEDED 等），`respond_template(key)` 返回对应字符串。模板不包含占位符——它们是纯文本。

C1e 的 `fixed_templates.py` 应从中提取这些模板并新增带占位符的模板：EXECUTED（`{operation}`、`{amount_yuan}`、`{counterparty}`）、REPLAN_USER_HINTS（`{hints}`）。

### 1.4 State 字段（respond / verify_output 需要的）

`services/agent/app/graph/state.py` AgentState（TypedDict）：

| 字段 | 类型 | respond 用 | verify_output 用 |
|---|---|---|---|
| `messages` | `list` | 否 | 否（写入端） |
| `utterances` | `list[UtteranceRef]` | 是（原话文本） | 否 |
| `understanding` | `Understanding` | 是（intents/needs_write） | 否 |
| `reads` | `list[ReadRecord]` | 是（分析类） | 是（事实源） |
| `plan_draft` | `PlanDraft` | 是（操作摘要） | 是（事实源） |
| `decision` | `GatewayDecision` | 是（决策、风险、hints） | 是（事实源、执行状态） |
| `execution` | `ExecutionResult` | 是（执行结果） | 是（事实源） |
| `reply_draft` | `str` | 输入/输出 | 输入 |
| `route_error` | `str` | 是（决定是否走固定模板） | 否 |

### 1.5 GatewayDecision 结构

`services/agent/app/gateway/wire.py` — 本地 wire 模型：

- `plan_id`、`evaluation_id`、`revision`
- `decision: Literal["ALLOW", "REQUIRE_APPROVAL", "REPLAN", "DENY"]`
- `risk: Literal["GREEN", "YELLOW", "RED"]`
- `approval_id: str | None`
- `findings: list[dict]` — 每条含 `code`、`rule`、`level`（绿/黄/红）、`detail`
- `replan_hints: list[dict]` — 每条含 `label`、`ref`（hnt_*）、`detail`
- `preflight: GatewayPreflight | None` — 含 `balances`（`list[GatewayBalance]`）、`holdings`、`steps`
- `shadow_b1: GatewayDecision | None`

### 1.6 ExecutionResult 结构

`packages/contracts/src/safebank_contracts/plan.py`：

- `plan_id`、`status`（`EXECUTED | FAILED | UNKNOWN`）
- `steps: list[StepResult]` — 每条 `tool`、`status`、`result`（dict）、`effects`、`created_ids`、`code`
- `versions`、`committed_at`、`error_code`
- `replayed: bool`

### 1.7 ReadRecord 结构

`services/agent/app/graph/state.py`：

- `source_ref`、`tool`、`args`（dict）
- `data: dict` — **已 wrap untrusted 文本**（`<untrusted_data source="...">...</untrusted_data>`）
- `untrusted_paths: tuple[str, ...]`
- `versions: dict[str, int]`
- `observed_at: str`

**关键**：verify_output 做数值比对时，`data` 中的 untrusted 字段已被 wrap。因此数值字段（如 `balance_cents`、`amount_cents`）不会被 wrap，可以直接读。但 `name`、`memo`、`merchant_name` 等会被 `<untrusted_data>` 包裹。

### 1.8 SSE 流当前实现

`services/agent/app/main.py:409–497` `_stream()`：

1. 获取线程锁
2. `graph.ainvoke()` — 执行图直到完成或中断
3. `graph.aget_state(config)` — 读最终状态
4. 若中断 → 推送 `event: updates` 含 `__interrupt__`
5. 推送 `event: values` 含 `final_state.values`
6. 推送 `event: messages` 含 `[{"type": "ai", "content": reply_text}]`
7. 异常 → `event: error`

**当前没有 `custom` 事件**。C1e 在第 5 步前插入卡片推送。

### 1.9 ChatDeepSeek 配置

`services/agent/app/llm/client.py`：

- `model="deepseek-v4-pro"`
- `api_base="https://api.deepseek.com/beta"`（strict 模式需要 /beta）
- `temperature=0`
- `max_retries=0`
- `extra_body={"thinking": {"type": "disabled"}}`

### 1.10 依赖

- `cn2an` 不在 `pyproject.toml` — C1e 需要新增（中文数字→阿拉伯数字）
- `langchain-deepseek` 已在主依赖
- `httpx` 已在主依赖（gateway client 用的）
- `psycopg` 已在主依赖
- `pyjwt` + `cryptography` 已在主依赖

### 1.11 种子 ID 白名单

PRD §6.6 要求种子固定 ID 在 `canonical()` 中不被替换。从 PRD §2.2 种子数据：
- `cus_zs`（张三）、`cus_ww`（王五）、`cus_zl`（赵六）、`cus_ls`（李四）、`cus_sb`（孙八）
- `acc_zs_chk`（张三活期）、`acc_ww_chk`（王五活期）等
- `crd_zs_1`（张三储蓄卡）
- `pye_zs_ww`（张三→王五收款人）

协调者需提供完整白名单（从 seed 数据提取），或授权 C1e 实现 hardcode 常见前缀。

---

## 2. PRD 缺口

### 2.1 verify_output 的修复 LLM 调用边界不明确

PRD §6.7.1 说「不匹配→LLM 修复一次→仍不匹配→固定模板」，但：
- 修复 LLM 的 prompt 应包含什么？是否需要 fact_set 的完整列表？
- 修复后「仍不匹配」的判定标准是与原始事实源比对还是放宽？
- 任务书中已裁定：事实源不变、不接受放宽，与第一次比对用同一标准。

### 2.2 长数字掩码与金额表示的交互

PRD §6.7.1a 说「≥8 位数字 → `****` + 后四位」，但：
- `¥800.00` 中的 `800.00` 是 6 位（不含小数点），不会被掩码
- `¥100000.00` 中 `100000` 是 6 位不会被掩码；但 `¥1000000.00`（100 万分）中的 `1000000` 是 7 位仍不被掩
- 只有 ≥8 位连续数字才触发——常见的 6–7 位金额（如 10 万分 = 1000.00 元）不会误掩
- 任务书中已裁定：日期例外（`\d{4}-\d{2}-\d{2}` 和 `\d{8}` 日期格式不掩码）、金额带 `¥`/`元`/`,` 的不掩码

### 2.3 UICard 的 `data` 字段内容未在 PRD 中列具体字段

PRD §9.5 只给了 UICard 的 type 枚举和 actions 结构，没有列每种卡片类型的 `data` 具体字段。这需要实现时从 `ReadRecord.data`、`Decision`、`ExecutionResult` 中**可选地**提取，不在 PRD 中定义新的结构——直接用现有 contract 的字段子集。

### 2.4 make demo 的 exit 2

PRD §12 的演示脚本依赖 L2 G4/G8（完整审批与执行），当前 `make demo` exit 2。C1e 不改变这个状态——如实记录 exit 2。

### 2.5 ADR-004 和 ADR-005 缺失

没有 `docs/decisions/ADR-004-*.md`、`docs/decisions/ADR-005-*.md`。C1e 需要新写两份 ADR。

---

## 3. 待协调者裁定

### Q1: 种子 ID 白名单

`canonical()` 需要区分「种子固定 ID」（如 `cus_zs`、`acc_zs_chk`、`pye_zs_ww`）和「运行期 ID」。完整白名单应由协调者从 PRD §2.2 种子数据提取，还是由 C1e 实现者按前缀 hardcode？

**建议**：C1e 实现者从 PRD §2.2 提取全部种子前缀并 hardcode 到 `canonical()` 函数中（白名单随种子数据变更同步更新）。不在 PRD 中新增种子 ID 清单。

### Q2: verify_output 中修复 LLM 调用的温度

verify_output 中一次修复调用 LLM，temperature 应设为 0（与 understand/gather 一致）还是允许小幅变化？

**建议**：temperature=0（已写入任务书）。修复是确定性对比的补救，不需要创造性。

### Q3: cards.py 中「分析类卡片」的类型映射

分析类回复（`needs_write=false`）且有 reads 时，需要按 read tool → UICard type 映射。当前建议映射：

| Read Tool | UICard Type |
|---|---|
| `get_accounts` | `ACCOUNT` |
| `get_account_history` | `TRANSACTIONS` |
| `get_cards` | `CARD` |
| `get_subscriptions` | `SUBSCRIPTIONS` |
| `get_products` | `PRODUCTS` |
| `get_holdings` | `HOLDINGS` |
| `detect_anomalies` | `ANOMALIES` |

若一条请求触发了多种 read，生成多张卡片。是否需要限制卡片数量上限？

**建议**：上限 5 张卡片，优先 PLAN_STATUS/BLOCKED/REPLAN_HINTS（这些是结论性卡片），其次按 read 调用顺序取。

### Q4: 重放文件路径

`evaluation/replay/<profile>/<case_id>.jsonl` 中 `<profile>` 的值：`LLM_PROFILE` 环境变量默认 `b1`，`LLM_CASE_ID` 默认 `default`。是否需要在实际写入前确保目录存在？（`os.makedirs` 或等效）

**建议**：录制模式自动创建目录（已是通用做法），不需要额外环境变量。

### Q5: `canonical()` 中 params 的精确定义

PRD §6.6 说「排除 temperature、max_tokens」，但 ChatDeepSeek 还有 `extra_body`（含 `thinking`）。这些也应该排除。

**建议**：排除 `temperature`、`max_tokens`、`extra_body`、`model`（已在 key 前缀）。只保留 `messages` 和 `tools` 的 canonical form。

### Q6: `replay_schema_version` 的 components

`replay_schema_version = sha256(canonical(tools_schema + system_prompts))`。这里的 `tools_schema` 是全部 `bind_tools()` 的工具定义还是仅本次调用的子集？`system_prompts` 是所有 adapter 的 system prompt 拼接？

**建议**：录制时存为文件头的元数据 `{"schema_version": "<sha256>", "tools": [...], "systems": {...}}`。回放时先读文件头，不匹配则 REPLAY_STALE。具体哪些 tools/systems 进 hash 由 C1e 实现者按实际 `make_llm` + adapter 的绑定决定——录制时刻是确定的。

### Q7: 是否需要写 `llm/cards.py` 的 UICard model 还是复用契约？

UICard / CardAction 只在 L1 使用（前端从 SSE 读取），不是三层共享契约。因此定义在 `llm/cards.py` 中作为 dataclass/Pydantic model，不放进 `packages/contracts/`。PRD §9.5 作为规格参考。

**建议**：在 `llm/cards.py` 中定义，不碰冻结契约。

---

## 4. 实现顺序依赖

```
Task 0 (ADR) ──独立，无依赖
Task 1 (replay.py) ──独立纯函数，无依赖
Task 2 (fixed_templates.py) ──依赖 routing.py 现有模板
Task 3 (respond.py) ──依赖 Task 2（固定模板）
Task 4 (verify.py) ──独立纯函数部分（抽取、比对、掩码），依赖 Task 3 的 respond 生成回复
Task 5 (cards.py) ──独立，无依赖
Task 6 (SSE 集成) ──依赖 Task 3+4+5
Task 7 (final gates) ──全部前序
```

建议实现顺序：0 → 1 → 2 → 3 → 4 → 5 → 6 → 7，但 Task 5 可与 Task 3/4 并行（不同文件、不同测试文件）。

---

## 5. 风险与注意

1. **verify_output 修复 LLM 可能引入新幻觉**：修复 prompt 可能让 LLM 看到 facts 并向其靠拢而非诚实报告不匹配。对策：修复后仍过同一 `check_against_facts()` 函数，无误报空间。
2. **长数字掩码的日期例外可能漏掩**：存在 `20261025abc` 这种 8 位数字后紧跟字母的模式。日期正则以 `\b` 为界，不会误判。
3. **卡片数据中的 untrusted 字段**：ReadRecord.data 中 untrusted 文本已被 `<untrusted_data>` 包裹。cards.py 必须读取 `strip_wall()` 后的 data（与 gather 发送给 LLM 的一致），但对被包裹的字段只取 key 存在性、不取具体值。
4. **SSE streaming 与 cards 的顺序**：cards 必须在 `values` 事件前推送，这样前端可以先渲染卡片再渲染回复文本。