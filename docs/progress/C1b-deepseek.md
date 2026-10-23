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
  协调者复跑后以实际结果为准，本 worker 不把未跑 live 写通过。

## 未覆盖 / 待决（交协调者或后续组件）

- `Understanding`/`Slot`/`ReadRecord` 仍为 C1a 本地占位：`Slot.name` 用 `str` 非槽位枚举、`ReadRecord`
  只存 `source_ref`/`untrusted_paths`（不含读结果 data）。C1c 接真实 L2 读结果/Decision 时对齐真实
  wire 类型，plan 的「reads 摘要」届时补读数据（当前只用 source_ref 指针）。
- `gather` 的 `read_executor` 是显式端口（C1b 用 fake；C1c 接 L2 G2）。未接 gateway HTTP/路由/持久化。
- `ReadResult`（data/source_ref/untrusted_paths）为本地占位形状，C1c 对齐 PRD §7.5 完整读取结果
  （versions/observed_at）。
- live 入口尚未跑；`usage` 未回传（adapter 只暴露校验后的 pydantic 模型，不暴露 AIMessage usage，
  留 C1e 回放/观测时补）。模型未含多模态 Flash、未开启思考。
