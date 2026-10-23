# C1b DeepSeek strict 理解/收集/计划适配（L1）进度

实现 worker：本机 DeepSeek V4 Pro（Claude Code CLI）。范围：`services/agent/app/llm/**`、
`services/agent/README.md`、`tests/agent/**` 与本文件；未改 graph 路由/身份 guard/invoke 边界、
contracts/schema、L2/L3/bankcore、Makefile/CI/compose/依赖锁/AGENTS/PRD。生产仍只有骨架+fake 端口，
**不声称**真实 L2/PG/live 通过。

## 交付内容

- `services/agent/app/llm/client.py`：`make_llm`（固定 `deepseek-v4-pro`、显式
  `https://api.deepseek.com/beta`、`thinking.type=disabled`、`temperature=0`、`max_retries=0`）、
  `load_api_key`（env→.env，绝不打印）。
- `services/agent/app/llm/schema.py`：`understanding_tool()`（`report_understanding` strict 工具，
  由本地 `Understanding` 生成并去掉 L2 派生的 `unclear`）、`read_tools()`（18 个 READ_ARGS 非 strict
  只读工具，schema 由生产 contracts 生成，参数本地契约校验）。
- `services/agent/app/llm/_common.py`：`new_ulid`（26 位 Crockford）、`single_tool_call`（拒绝
  未知/多/坏 tool calls）、`LlmToolError`。
- `services/agent/app/llm/redact.py`：`strip_wall`（递归剔除 `*_wall`）、
  `escape_untrusted`/`wrap_untrusted`/`apply_untrusted_wrapping`（`<untrusted_data source=…>` 包裹并
  转义开/闭标签边界）。
- `services/agent/app/llm/understand.py`：`UnderstandAdapter`——单 strict 工具强制 tool_choice，每次
  新建 system+human（无历史 assistant），仅验证失败重试一次，第二次明确失败。
- `services/agent/app/llm/plan.py`：`PlanAdapter`——复用 `plan_tool`/`from_strict_plan`+`PlanBody`，
  `request_id/session_id/utterance_ids` 由代码填（非 context 原话），模型自填 identity 直接 422 语义
  （`PlanBody` extra=forbid 拒绝）；同样新请求+一次重试。
- `services/agent/app/llm/gather.py`：`GatherAdapter`——只绑 18 个 read 工具，最多 3 轮，经显式
  `read_executor` 执行、`READ_ARGS[name]` 本地契约校验参数；拒绝未知/写工具；tool 消息只由合法结果
  生成，`*_wall` 剔除、untrusted 按 `untrusted_paths` 包裹。`ReadResult`/`ReadExecutor` 本地占位。
- `services/agent/app/llm/live.py`：手动 live 入口（理解2+计划3+gather1，预算8请求含重试），脱敏证据
  （工具 schema 哈希/校验/延迟，绝不存 key/headers/真实数据）。**本轮未执行 live**。
- `tests/agent/llm_mock.py`：`ScriptedLLM`（`httpx.MockTransport` 记录最终 HTTP payload 并回放）。
- `tests/agent/test_llm_schema.py`、`test_llm_adapters.py`、`test_llm_graph.py`：13+1 项新测，含一例
  真实 LangGraph + C1a Ports 贯穿。

## 红 → 绿记录

先落测试再实现；实际首轮失败逐项记录（不删断言/不宽松捕错）：

1. **`thinking` 参数被 openai SDK 拒收（红）**：`make_llm` 初用 `model_kwargs={"thinking":…}`，
   首跑 `test_understand_payload_and_result` 等 8 项因
   `TypeError: Completions.create() got an unexpected keyword argument 'thinking'` 失败（openai 类型化
   `create()` 拒绝未知顶层 kwarg）。读 `langchain_openai` 源码（`_default_params` 含 `extra_body`）后改
   `extra_body={"thinking":{"type":"disabled"}}`，经 mock 断言 `body["thinking"]` 顶层出现 → 绿。
2. **测试样例/断言错误（红）**：`needs_write:"yes"`/`needs_write:1` 被 pydantic v2 bool 宽松转成 True，
   未触发重试（`len==1` 断言红）；gather tool 消息索引应为 `messages[3]`（sys/user/assistant/tool）非
   `[2]`；untrusted 断言写错斜杠、network 断言应为 langchain 包装的 `OpenAIConnectionError`。改为真正
   非法值（`intents:"transfer"` 字符串）、解码 JSON 断言、`OpenAIConnectionError` 后全绿。untrusted
   转义本身（`<untrusted_data`→`<\untrusted_data`、`</untrusted_data`→`<\/untrusted_data`）实现正确。
3. **绿测**：`pytest tests/agent -q` = `36 passed`（C1a 22 + C1b 14）零 skip/xfail。

## 退出命令与结果（本组件无 PG）

| 命令 | 结果 |
|---|---|
| `make lint` | ruff check + format --check 通过（256 files formatted） |
| `.venv/bin/pytest tests/agent -q` | `36 passed`（零 skip/xfail；live 单独 `python -m` 入口不混入） |
| `make test` | `2145 passed, 529 skipped`（skip 为无 PG 配置项，非本组件范围） |
| `make contracts-check` | `PASS: contracts v1 match frozen schemas` |

## fake / 真实 LLM 边界

- 全部自动测试用 `httpx.MockTransport` 记录**最终 HTTP payload** 并断言：beta endpoint、thinking/
  temperature、strict/tool_choice、生产工具 schema、身份字段（customer/session/token）不入 body、
  `*_wall` 剔除、无密钥泄漏、重试次数与「每次新 system+human」、gather 只读上限/拒绝写工具、网络错误
  有界不吞。
- **未执行 live**：`live.py` 只作为手动入口交付，未跑真实 DeepSeek（本机未用 key/网络，避免误报）；
  协调者复跑后以实际结果为准，本 worker 不把未跑 live 写通过。协调者亲跑的旧失败证据
  `docs/acceptance/c1b-deepseek-live/results.jsonl` **保留未覆写/未洗绿**。

## 修复轮（独审 REQUEST_CHANGES + 协调者 live 失败后，2026-10-24）

按 `docs/reviews/C1b-deepseek-review.md`（P1-1 阻塞 + P2-1~P2-6）与协调者 live 证据逐项修复，先红测再绿：

1. **P1 live 失败 exit 非 0**：`main()` 统计样本失败/预算耗尽/语义断言失败，>0 时 `sys.exit(1)`；证据
   在退出前写入。`run_scenarios` 拆为可测函数，`test_llm_live.py` 4 项实际模拟：成功 exit0（7 请求）、
   plan 校验失败 exit1（记录 `validation_errors`，只存 loc/msg/type，无 input/key/header/原始响应）、
   语义断言失败 exit1（`problems`）、预算耗尽 exit1。红：旧 `main()` 无 `sys.exit`，失败仍 exit0。
2. **P2-1 StrictBool**：`Understanding.needs_write`/`unclear` 改 `StrictBool`，拒 `"yes"/"off"/0/1`；
   `Slot.intent_index` 加 `ge=0`、`span` 加 `0<=a<b` 校验。红：`needs_write:"yes"` 被宽松转 True 不报错。
   `Slot.name` 枚举仍留 C1c（冻结 contracts 无权威槽位枚举，不另造冲突数据）。
3. **P2-3/P2-5 gather**：`invalid_tool_calls` 立即抛 `LlmToolError`（混合合法/非法也先抛、不先执行部分）；
   `max_rounds` 严格 1..3；模型在最后一轮仍要读时抛「exceeded max rounds」明确截断，不无信号返回。
   红：`test_gather_stops_at_round_limit` 原静默返回 3 reads，改为断言抛错。
4. **P2-4 读数据入 plan**：协调者批准最小扩展 `graph/state.py` `ReadRecord`（`tool/args/data/
   untrusted_paths/versions/observed_at`，`data` 为 wall 剔除+untrusted 包裹后的安全载荷，不含
   JWT/服务 token/wall/不可信升级）。`GatherAdapter` 输出完整 `ReadRecord`，`PlanAdapter` 在全新
   system+human 中给模型可引用的原话 id/kind/text 与读数据 source_ref/tool/data，模型能生成
   arg_sources 的 utterance span 与 read pointer；代码填 request/session/utts 身份与 LLM 可引用 ID 分开。
   红：`test_plan_prompt_includes_reads_utterances_and_derived` 先断言 human 无 utt/src/entity。
5. **P2-2/P2-6 live 证据与预算**：adapter 加 `observer`（AIMessage）回传 usage；live 记实际
   token/延迟/请求计数，plan 成功证据含 tool/amount/entity/source_kinds + 语义断言（transfer/freeze/
   limits 三场景用可见实体），不再只有结果类名；live 用 `max_retries=0` + 硬预算 8（6 基础请求预留余量），
   超限/失败立即非 0。README 记录 live 入口与 `DEEPSEEK_API_KEY` 由协调者安全注入，不复制 key 到 worktree。
6. **P2 plan 系统提示漏 derived**：`source.kind` 补 `derived`（template 仅
   `cancelled_monthly_fees`/`available_less_7d_charges`），按冻结 schema 描述，未自造 App-only 来源。

## 退出命令与结果（本组件无 PG）

| 命令 | 结果 |
|---|---|
| `make lint` | ruff check + format --check 通过（258 files formatted） |
| `.venv/bin/pytest tests/agent -q` | `49 passed`（零 skip/xfail；live 单独 `python -m` 入口不混入） |
| `make test` | `2158 passed, 529 skipped`（skip 为无 PG 配置项，非本组件范围） |
| `make contracts-check` | `PASS: contracts v1 match frozen schemas` |

## 未覆盖 / 待决（交协调者或后续组件）

- `Slot.name` 仍为 `str`（冻结 contracts 无权威槽位枚举，C1c 对齐 L2 实际枚举）；`ReadRecord`/`ReadResult`
  为本地形状，C1c 接真实 L2 读结果时对齐 PRD §7.5 完整字段。
- `gather` 的 `read_executor` 是显式端口（C1b 用 fake；C1c 接 L2 G2）。未接 gateway HTTP/路由/持久化。
- live 仍未由本 worker 执行（不读 key/不跑 API）；协调者复跑时以 `python -m services.agent.app.llm.live`
  为准，失败会 exit1。模型未含多模态 Flash、未开启思考。

