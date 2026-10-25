# C1e Output Verification, Number Masking, Deterministic Cards & Replay

> 设计起草 worker（只读代码，只写本文档与 C1e-design-notes.md），基于 main 0472689，分支 codex/c1e-output-replay。本文档是 **C1e 实现任务书**，待协调者审核后派发本机 Claude Code CLI DeepSeek V4 Pro worker。

**Goal:** L1 respond 节点不再只是透传 `reply_draft`——本组件加入 LLM 驱动的自然语言回复生成、输出内容校验（金额/账号与事实源比对）、长数字掩码、确定性 UICard 卡片生成、以及 DeepSeek 调用的录制与回放。**不含** 前端渲染、`make demo` 全流程（保持 exit 2 并写待决），不改网关。

**Architecture:** C1d 已注入 `ports.respond` 和 `ports.verify_output` 两个可替换 callable。C1e 重写二者：
- `respond`：对非固定模板的路径（分析类回复、EXECUTED 成功、REPLAN_USER 等）使用 LLM 生成自然语言回复。
- `verify_output`：对 `reply_draft` 进行数字来源校验、长数字掩码，通过后才写入 `messages`。
- `cards.py`（新文件）：从 state 中的 `reads`、`decision`、`execution` 确定性生成 UICard 列表，经 SSE `custom` 事件推送。
- `llm/replay.py`（新文件）：录制/回放基础设施，请求键哈希、ID 归一化、按 profile/case_id 存储。

**Spec:** PRD §6.1（respond / verify_output 节点）、§6.5（SSE 事件类型与 UICard）、§6.6（回放、LLM_MODE、请求键、ID 归一化正则）、§6.7（幻觉防护：数字来源校验、长数字掩码、卡片不经 LLM）、§9.5（UICard / CardAction / PlanView / Decision / ExecutionResult 模型）、§10（评测 replay）、§13（P2 日程中 ADR-004 / ADR-005 待写）；Contracts `StepResult`（effects、created_ids、result）、`ExecutionResult`（status/replayed）；ADR-002（E2 回退、思考关、温度=0）。

---

## Global Constraints & ownership

**精确文件白名单（只能改/新增这些）：**

| 可改/新增 | 说明 |
|---|---|
| `services/agent/app/llm/respond.py`（新建） | LLM respond 适配器（替换 `_make_production_respond` 的透传） |
| `services/agent/app/llm/verify.py`（新建） | verify_output：金额抽取、事实源比对、长数字掩码 |
| `services/agent/app/llm/cards.py`（新建） | 确定性 UICard 生成器（BLOCKED、PLAN_STATUS、ACCOUNT 等） |
| `services/agent/app/llm/replay.py`（新建） | 录制/回放：请求键计算、ID 归一化、文件读写、REPLAY_STALE |
| `services/agent/app/main.py` | 替换 `_make_production_respond` / `_make_production_verify` 为 C1e 实现；接入 cards 到 SSE 流 |
| `services/agent/app/llm/fixed_templates.py`（新建或扩 `routing.py`） | 扩展固定模板（执行成功、REPLAN_USER 附加 hints 等） |
| `tests/agent/test_verify.py`（新建） | verify_output 纯函数单元测试 |
| `tests/agent/test_cards.py`（新建） | 确定性卡片纯函数测试 |
| `tests/agent/test_replay.py`（新建） | replay 纯函数测试（含 ID 归一化正则与回放键碰撞） |
| `docs/decisions/ADR-004-streaming-replay.md`（新建） | L1 流式协议与回放架构决策 |
| `docs/decisions/ADR-005-frontend-choice.md`（新建） | 前端选型（占位，标记为暂停，由 P2 前端负责人写） |
| `docs/progress/C1e-output-replay.md`（新建） | 进度、红绿、退出命令 |
| `Makefile` | 新增 `make test-replay` 目标 |

**严格冻结（只读，不改）：**

- `packages/contracts/`、`packages/bankcore/`、`services/bank-backend/`、`services/gateway/`
- `services/agent/app/graph/state.py`、`ports.py`、`routing.py`、`errors.py`、`graph.py`、`invoke.py`
- `services/agent/app/gateway/` 全部
- `services/agent/app/llm/` 除新建文件外的全部现有文件
- `docker-compose.yml`、`scripts/keys.py`、`.github/workflows/checks.yml`
- `AGENTS.md`、`PRD.md`、`docs/decisions/ADR-001–003,006–009`

**边界与禁止项：**

- 不读真实 `.env` / `.secrets` / `~/.claude/settings.json`。
- 不做 `make up/down/test-p1*/test-integration`。
- 不删除/重建长期卷。
- 不 push、不 PR、不 merge。
- 不调用真实 L2/L3（测试用 fake state 构造）。
- 不实现前端渲染、BFF、`make demo` 全流程（如实保持 exit 2）。
- 不使用 `;` 或 `&&` 串联 shell 命令。
- 不将 API key / JWT / 密钥写入 replay 文件。

---

## Interfaces & fixed choices

### 1. LLM_MODE 三态（PRD §6.6）

环境变量 `LLM_MODE`：

| 值 | 行为 |
|---|---|
| `live`（默认） | 真实调用 DeepSeek API |
| `record` | 真实调用，同时把 (请求键, 响应) 写入 replay 文件 |
| `replay` | 不调 API，按请求键从 replay 文件查找匹配响应；未命中报 `REPLAY_MISS` |

录制文件路径：`evaluation/replay/<profile>/<case_id>.jsonl`，每行一条 JSON `{key, model, params, response}`。`profile` 和 `case_id` 也由环境变量提供：`LLM_PROFILE`（默认 `b1`）、`LLM_CASE_ID`（默认 `default`）。

`replay_schema_version` 标签：SHA256 of `canonical(json.dumps(tools_schema + system_prompts))`，录制时写入文件头（第一条记录），回放时校验。不一致→`REPLAY_STALE`。

### 2. 请求键（PRD §6.6）

```
key = sha256(model ‖ canonical(messages) ‖ canonical(tools) ‖ canonical(params))
```

`canonical()` 对易变 ID 做归一化替换：
- 正则：`\b[a-z]{2,4}_([0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b`
- 覆盖全部运行期前缀（§9.1）：cus、acc、crd、pye、mer、bil、si、spl、spi、prd、ord、tx、trf、evt、pln、apv、evl、ses、utt、src、hnt、act、ntf、req、msg、clr、asm、adv、job
- 以及 UUIDv5 步骤派生 ID 和 secrets 生成的 ID（hnt、act、clr 等 26 位 Crockford base32）
- 种子固定 ID（如 `cus_zs`、`acc_zs_chk`、`pye_zs_ww`）**不替换**（白名单）
- 替换为 `<ID1>`、`<ID2>`…，按首次出现顺序
- 反替换：响应中把 `<IDN>` 换回原始 ID

`canonical(params)` 排除 `temperature`、`max_tokens`（已在请求中固定）。

### 3. respond 端口（替换 `_make_production_respond`）

```
respond(state, context) → str (reply_draft)
```

规则：
- 若 `state.route_error` 存在且 `reply_draft` 非空（固定模板路径）：**直接返回** `reply_draft`，不调 LLM。
  - 当前 graph.py 已在 respond 节点做此判断；C1e 的 respond port 也保留此快捷路径。
- 若 state 中有 `execution` 且 `status=EXECUTED`：调用 LLM 写简短成功回复（如"已向王五转账 800.00 元，余额 5,200.00 元"）。
  - prompt 必须包含：原话文本、决策结论、执行结果摘要（不含完整 effects）。
- 若 `decision.decision=REPLAN` + `resolver=user`：调用 LLM 解释原因并附上 L2 的 `replan_hints` 标签列表（LLM 只能引用 hint 标签，不能编造金额）。
- 若 `execution.status=UNKNOWN`：固定模板「请求已提交，正在确认中…」，不调 LLM。
- 若 `decision.decision=DENY`：固定模板「该操作未被授权，无法执行。」，不调 LLM。
- 其他（分析类回复、NO_WRITE、INTENT_UNCLEAR 等）：调用 LLM 生成回复。

LLM 参数：`temperature=0`、`thinking={"type": "disabled"}`——与 understand/gather 一致。

固定模板全集在 `fixed_templates.py` 中集中管理（不散落在 routing.py 和 respond.py 两处），保持与 PRD §6.1.1 路由表一致。

### 4. verify_output 端口（替换 `_make_production_verify`）

```
verify_output(state, context) → str (final reply text)
```

**第一步：数字抽取。** 从 `reply_draft` 中提取：
- 金额：正则 `(¥|￥)\s*([\d,]+(\.\d{1,2})?)`、`([\d,]+)\s*(元|块)`、中文数字通过 `cn2an` → 换算为分
- 百分比：`([\d.]+)\s*%`
- 卡号尾号：`尾号\s*(\d{4})`

**第二步：事实源比对。** 将提取值转换为可比较单位后逐项比对：

| 提取类型 | 事实源 |
|---|---|
| 金额（元→分） | `reads[].data`、`decision.preflight.balances`、`execution.steps[].result` |
| 百分比 | `reads[].data`（如 `recent_return_bp` / 100）、`decision.preflight` |
| 卡号尾号 | `reads[].data.cards[].last4` |

比对规则：
- 金额：接受 ±1 分的整数舍入误差（元→分发散），但不接受数量级错误
- 若提取值在事实源中**完全找不到**：标记为 `unverified`
- 若 `execution.status=UNKNOWN` 且回复中出现「已转账」「已扣款」等确定性表述：直接标记失败（不能把未确认的事写成成功）
- 若 `execution.status=FAILED` 且回复中未体现失败：标记失败

**第三步：修复或降级。**

- 若 `unverified` 集合非空 → 调用 LLM **一次**修复（prompt 包含：原始回复、不匹配项列表、事实源摘要），用修复后的文本替换
- 修复后再校验，仍不通过 → **放弃 LLM 回复**，改用固定模板

**第四步：长数字掩码（§6.7.1a）。**

无论回复来自 LLM 还是固定模板，最后一步必定执行：
- 正则：`\b\d{8,}\b`（≥8 位连续数字，允许中间有空格或 `-` 的变体也归一化后匹配）
- 替换为 `****` + 最后 4 位
- **例外**：日期格式 `YYYY-MM-DD`（10 位含 `-`）不掩码；纯 8 位日期 `20261025` 也不掩码（`re.fullmatch(r'\d{4}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])')`）
- 金额字符串 `¥800.00`、`1,200元`（含 `¥`/`元`/`,`的）在抽取后保留原文不掩码
- 掩码后文本中的 `****1234` 与原始卡号尾号一致——这是掩码**不**泄露额外信息

**第五步：返回。**

返回处理后的文本字符串。

### 5. cards.py（确定性 UICard，PRD §6.7.3、§9.5）

```
generate_cards(state) → list[UICard]
```

从 state 确定性生成，**不调 LLM**。

卡片类型与生成条件：

| 条件 | 卡片类型 | data 来源 |
|---|---|---|
| `execution.status=EXECUTED` 或 `UNKNOWN` | `PLAN_STATUS` | ExecutionResult.steps 摘要 |
| `decision.decision=DENY` 或 `execution.status=FAILED` | `BLOCKED` | decision.findings |
| `decision.decision=REPLAN` 且任一 `resolver=user` | `REPLAN_HINTS` | decision.replan_hints |
| `understanding.unclear=True` 且 `parse_status=INTENT_UNCLEAR` | `CLARIFICATION` | state.clarification |
| 分析类回复（`needs_write=false`）且有 reads | 按 read tool 类型映射 | reads[].data |

**UICard 模型（PRD §9.5）**：
```python
class CardAction(Contract):
    kind: Literal["HINT", "CLARIFY", "NAVIGATE", "OPEN_APPROVAL"]
    ref: str  # hnt_*, entity_id, page name, apv_*

class UICard(Contract):
    id: str  # card_<ulid>
    type: Literal["ACCOUNT", "TRANSACTIONS", "ANOMALIES", "CARD", "SUBSCRIPTIONS",
                  "PRODUCTS", "HOLDINGS", "PLAN_STATUS", "BLOCKED",
                  "REPLAN_HINTS", "CLARIFICATION", "NAVIGATE"]
    source_ref: str | None = None
    data: dict  # 只能取自 reads[source_ref].data 子集或 Decision/ExecutionResult
    actions: tuple[CardAction, ...] = ()
```

**卡片生成规则（硬约束）**：
- `data` 字段**只能**包含结构化数据（ID、金额、状态、日期），**禁止**包含任意文本（name、memo、merchant_name 等已标注 untrusted 的字段）
- `CardAction.ref` 只能是 `hnt_*`（来自 L2 replan_hints）、当前 clarification 的 `entity_id`（来自 candidates）、白名单页面路由（不含写操作参数）、或 `apv_*`
- 卡片动作**不带可见文字**——按钮文字由前端向 L2 取（G22a、G21、G9）
- 卡片**不进 AgentState**，经 SSE `custom` 事件推送

### 6. SSE 集成

在 `main.py` 的 `_stream()` 中，respond→verify_output 完成后：
1. 从 state 中调用 `generate_cards(state)` 生成卡片列表
2. 若有卡片，先推送 `custom` 事件：`{"type": "cards", "cards": [...]}`
3. 再推送最终回复 `updates` 事件：`{"reply": {"id": "msg_...", "type": "ai", "content": "..."}}`

`messages` 仍走 `values` + `messages` 事件的现有路径。

### 7. ADR-004（L1 流式协议与回放）

新写 `docs/decisions/ADR-004-streaming-replay.md`，内容：
- 流式协议复用 PRD §6.5 的 SSE 事件格式（`metadata`、`updates`、`custom`、`error`）
- 回放采用请求键哈希方案（§6.6）
- `LLM_MODE=replay` 下，整个图执行仍走正常路径，只是 LLM 调用被 replay 拦截
- 录制按 `(profile, case_id)` 隔离
- `replay_schema_version` 冻结时机：功能冻结后、录制前

### 8. ADR-005（前端选型）

新写 `docs/decisions/ADR-005-frontend-choice.md`，内容：
- 标记为**暂停**（用户 2026-10-23 决定，AGENTS.md §2 第 13 条）
- 前端页面清单见 PRD §9.10
- BFF 接口 F1–F13 已锁定在 PRD §9.6
- 框架选型（Next.js App Router + Tailwind 或其他）由 P2 前端负责人决定
- 安全硬约束已锁定：不预填输入框、按钮文字只来自 L2、卡片/确认面板不经 L1 生成

### 9. test-replay 专项目标

新增 `make test-replay`：
- 纯函数测试（不需 PG、不调 API）：
  - `canonical()` 对含种子 ID + 运行期 ID + UUIDv5 ID 的 message
  - 白名单种子 ID 不被替换
  - `key=sha256(...)` 的确定性与碰撞
  - REPLAY_STALE 检测
  - 录制文件读取、未命中报错、不回退 API
  - `*_wall` 字段剔除
- 不读/写真实 replay 文件，用临时目录

---

## Task checklist（逐任务先红后绿）

### Task 0 — ADR 文档（纯文档）
- [ ] 0a. 写 `docs/decisions/ADR-004-streaming-replay.md`
- [ ] 0b. 写 `docs/decisions/ADR-005-frontend-choice.md`（占位）
- [ ] 0c. 核查 ADR 格式与已有 ADR 一致（`docs/decisions/ADR-002-llm.md` 为模板）

### Task 1 — `llm/replay.py` 录制/回放基础设施
- [ ] 1a. `LLM_MODE` 读取与验证（live/record/replay，非法值 fail-closed）
- [ ] 1b. `canonical()` 函数：ID 归一化正则 + 白名单 + 顺序替换
- [ ] 1c. `replay_key()` 函数：sha256(model ‖ canonical(messages) ‖ canonical(tools) ‖ canonical(params))
- [ ] 1d. `RecordTransport`：包装 httpx transport，录制模式写 `.jsonl`
- [ ] 1e. `ReplayLoader`：回放模式读 `.jsonl`、建索引、查键
- [ ] 1f. `replay_schema_version` 计算与校验
- [ ] 1g. `strip_wall_fields()`：递归剔除 `*_wall` 键
- [ ] 1h. 红测：每个函数独立单元测试（不合规输入、边界、碰撞、REPLAY_STALE）
- [ ] 退出：`make test-replay` 全部通过

### Task 2 — `llm/fixed_templates.py` 固定模板
- [ ] 2a. 从 `routing.py` 提取 `_RESPOND_TEMPLATES` 到独立模块
- [ ] 2b. 补全模板：执行成功（EXECUTED，含步骤摘要占位符）、REPLAN_USER（附 hints 列表）
- [ ] 2c. `routing.py` 中的 `respond_template()` 改为从新模块导入（不改变函数签名）
- [ ] 2d. 红测：每个模板 key 有非空值、占位符正确替换
- [ ] 退出：现有 `test_graph`、`test_llm_adapters` 全部通过、`make lint` 通过

### Task 3 — `llm/respond.py` RespondAdapter
- [ ] 3a. 实现：接收 state，判断路径→调用 LLM 或返回固定模板
- [ ] 3b. EXECUTED 路径：LLM prompt 包含原话文本 + ExecutionResult 摘要
- [ ] 3c. REPLAN_USER 路径：LLM prompt 包含 findings + replan_hints 标签
- [ ] 3d. 非写路径（分析类）：LLM prompt 包含原话 + reads 数据摘要
- [ ] 3e. 替换 `main.py` 的 `_make_production_respond`
- [ ] 3f. 红测：用 fake state 构造各类路径，验证返回非空
- [ ] 退出：`make test` 全部通过、`make test-agent-db` 全部通过

### Task 4 — `llm/verify.py` OutputVerifier
- [ ] 4a. `extract_amounts()`：正则抽取 ¥/元/分金额，换算为分
- [ ] 4b. `extract_percentages()`：正则抽取 % 值
- [ ] 4c. `extract_card_last4()`：正则抽取卡号尾号
- [ ] 4d. `build_fact_set()`：从 state 构建事实值集合（分、bp/100、last4）
- [ ] 4e. `check_against_facts()`：逐项比对，返回 `unverified` 列表
- [ ] 4f. `mask_long_numbers()`：≥8 位数字掩码，日期/金额例外
- [ ] 4g. `verify()` 主函数：抽取→比对→修复（1 次 LLM）→再比对→掩码→返回
- [ ] 4h. 替换 `main.py` 的 `_make_production_verify`
- [ ] 4i. 红测：金额匹配、金额不匹配、UNKNOWN 却说成功、掩码规则、日期例外
- [ ] 退出：`make test-verify`（新增目标）全部通过、`make test` 全部通过

### Task 5 — `llm/cards.py` CardsGenerator
- [ ] 5a. 实现 `generate_cards(state) → list[UICard]`
- [ ] 5b. 实现 `BLOCKED` 卡片：从 DENY/FAILED 生成
- [ ] 5c. 实现 `PLAN_STATUS` 卡片：从 EXECUTED/UNKNOWN 生成
- [ ] 5d. 实现 `REPLAN_HINTS` 卡片：从 replan_hints 生成
- [ ] 5e. 实现分析类卡片：按 read 工具类型生成（ACCOUNT/CARD/TRANSACTIONS 等）
- [ ] 5f. `CardAction` 验证：ref 只能是 hnt_*/entity_id/apv_*/白名单页面
- [ ] 5g. UICard.data 中不放 untrusted 文本字段
- [ ] 5h. 红测：各卡片类型结构验证、CardAction 非法 ref 拒绝
- [ ] 退出：`make test-cards`（新增目标）全部通过

### Task 6 — SSE 卡片推送
- [ ] 6a. 在 `main.py` `_stream()` 中：respond→verify_output 后调 `generate_cards(state)`
- [ ] 6b. 推送 `custom` 事件：`{"type": "cards", "cards": [...]}`
- [ ] 6c. 有卡片时先推卡片再推回复
- [ ] 6d. 红测：用 test_service.py 验证 SSE 事件包含 custom 卡片
- [ ] 退出：`make test`、`make test-agent-db` 全部通过

### Task 7 — final gates
- [ ] 7a. `make lint` + `make test` + `make test-agent-db` 全部通过
- [ ] 7b. `make test-replay` 全部通过
- [ ] 7c. `make test-verify` 全部通过
- [ ] 7d. `make test-cards` 全部通过
- [ ] 7e. `make contracts-check` 通过
- [ ] 7f. `make demo` 如实 exit 2（在 progress 中登记待决）
- [ ] 7g. actionlint 通过
- [ ] 7h. 新增专项目标已经在 Makefile 的 `test` 目标中被 `test-replay`、`test-verify`、`test-cards` 引用（默认 CI 路径跑全部）

### Task 8 — 文档
- [ ] 8a. `docs/progress/C1e-output-replay.md` 记录红绿过程
- [ ] 8b. `docs/acceptance/L1-C1e-output-replay.md`（协调者写验收报告）

---

## 退出命令（全部 exit 0 才算通过）

```bash
make lint
make test
make test-agent-db
make test-replay
make test-verify
make test-cards
make contracts-check
make demo  # exit 2 即通过（如实未实现）
actionlint
```

## 已知待决（C1e 不解决）

- `make demo` 完整流程依赖 L2 G4/G8 与 BFF，在 W3b/W4c 完成前保持 exit 2
- 前端卡片渲染不在本组件范围
- `LLM_MODE` 环境变量在部署时由协调者注入 `agent.env`
- `DEEPSEEK_API_KEY` 注入 agent 容器留协调者全栈阶段处理
## 协调者裁定（2026-10-25，与上文冲突时以本节为准）

- **E1 回放请求键（否决 Q5 与正文「排除 temperature/max_tokens」）**：严格按 PRD §6.6 计算 `sha256(model ‖ canonical(messages) ‖ canonical(tools) ‖ canonical(params))`。`model` 与全部请求参数（temperature、max_tokens、thinking/extra_body 等）都参与计算，不排除任何一项。归一化只针对 PRD 正则识别出的易变 ID：按首次出现顺序替换为 `<IDn>`，命中后反向映射回去。种子 ID 用白名单，不替换。必须有测试证明：换 model 或改 temperature 后不命中；只改易变 ID 时命中。
- **E2 输出校验**：按 PRD §6.7，抽取的事实与本轮 `reads`/`decision`/`execution` 比对。不一致时允许 LLM **改写一次**；改写结果必须再次走同一套校验和掩码，仍不通过就用只拼接工具字段的固定模板。改写不得引入事实源以外的数字，未执行或状态不确定的计划不得描述为成功。改写调用也要计入回放录制。
- **E3 掩码**：≥8 位连续数字（允许空格或连字符分隔）替换为 `****` + 后 4 位，对 LLM 回复和改写结果都生效；卡片与固定模板中的账号同样只显示尾号。
- **E4 卡片**：UICard/CardAction 不在冻结契约中，放在 `services/agent/app/llm/cards.py` 作为 L1 内部模型，字段严格按 PRD §9.5。CardAction 只允许 HINT/CLARIFY/NAVIGATE/OPEN_APPROVAL，不带可见文字。`data` 只能取自 source_ref 对应的 ReadEnvelope.data 子集，或 Decision/ExecutionResult。LLM 只能从候选卡片 ID 中挑选；候选上限 5（Q3 同意）。
- **E5 种子 ID 白名单**（Q1）：从 PRD §2.2 提取后写成常量，注释注明出处与提取日期。种子数据实现后再改为从种子源读取，这一点写进待决。
- **E6 录制文件**：录制时自动建目录（Q4 同意），文件头写入 replay_schema_version 与 profile（Q6 同意）。录制内容不得包含任何 JWT、服务令牌、API key、Authorization 头，要有测试扫描录制文件。replay 模式下缺录制直接报错，不得回退到 live。
- **E7 ADR**：新写 ADR-004（流式协议与录制/回放）。ADR-005 写成短说明：「前端选型暂停（用户 2026-10-23 决定），由用户恢复前端工作时再定」，不做选型。
- **E8 范围**：不改网关、contracts、compose。`make demo` 仍依赖未完成的 L2/BFF，保持 exit 2，写进待决。
- **E3 补充（2026-10-25，协调者实测后追加）**：61c271a 的掩码用 `\b\d{8,}\b`，紧贴中文的数字（如「卡号6222021234567890」「转账给62220212345678的账户」）以及空格、连字符分隔的数字（「6222 0212 3456 7890」「6222-0212-3456-7890」）都**没有被掩码**，等于 PRD §6.7 1a 没有落地。修改要求：识别「≥8 位数字，中间允许单个空格或连字符分隔」，不依赖 `\b`，也不要求左右是空白，紧贴中文、标点同样命中；输出为 `****` + 最后 4 位数字。只保留两类例外：ISO 日期 `YYYY-MM-DD`，以及带明确货币标记的金额（前缀 `¥`/`￥`，或后缀 `元`，可带小数）。这两类例外与 PRD「一律」的偏差要写进 ADR-004 并说明理由。上面列出的 5 个样例必须全部写成测试。
