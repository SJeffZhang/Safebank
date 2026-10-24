# agent（L1 Banking Agent，C1a 图骨架 + C1b DeepSeek 适配 + C1c1 gateway 协议客户端 + C1d 真实 PG checkpoint 服务）

L1 使用 LangGraph；已交付**图骨架**（`services/agent/app/graph/`）、**C1b DeepSeek 适配**
（`services/agent/app/llm/`）、**C1c1 gateway 协议客户端**（`services/agent/app/gateway/`）与
**C1d agent 服务**（`services/agent/app/main.py`，Postgres checkpoint + HTTP/SSE）：
`AgentState`、`AgentContext`、`Ports`、`build_graph`，understand/gather/plan/evaluate 端口，
G20/G21/G2/G4/G8 严格 HTTP/wire 边界，以及 FastAPI app、`AsyncPostgresSaver`、线程 advisory
lock、SSE 流式输出。

## 骨架可用范围（C1a）

- 节点链路：`ingest → understand → gather → plan → evaluate →（ALLOW→execute /
  REQUIRE_APPROVAL→await_approval→interrupt / DENY、REPLAN→respond）→ respond → verify_output → END`。
- 每个节点只调用注入的端口（`Ports`），端口与 checkpointer 缺失即 `MissingPortError`，端口返回
  `None` 即 `PortFailure`；骨架从不替未实现节点伪造「执行成功」。
- `ingest` 重置本轮字段（understanding/reads/plan_draft/plan_id/decision/replan_count/
  approval_id/execution/reply_draft），并在 `thread_id != session_id` 或同 thread 跨 customer 时
  fail-closed（`SessionMismatchError` / `CrossCustomerError`）。
- 中断语义：`await_approval` 用 `interrupt({"approval_id": ...})`；正确 resume 才进 `execute`，
  resume 值不等于 `approval_id` 时结束本轮（经条件边回到 `respond`）。`await_approval` 在
  `interrupt()` 之前复核身份/会话绑定（`thread_id == context.session_id`、
  `context.customer_id == state.customer_id`、`context.session_id == state.session_id`），不一致
  fail-closed（`SessionMismatchError` / `CrossCustomerError`），任何端口副作用之前拒绝。
- 新输入只经 `invoke_new_input(graph, utterances, ...)`（`services/agent/app/graph/invoke.py`），
  空/缺原话抛 `EmptyUtterancesError`；resume 只经 `graph.invoke(Command(resume=...))`。二者边界
  清楚，避免裸 `graph.invoke({})` 复用旧 checkpoint 的原话/计划。
- 助手令牌只放在 `AgentContext`（LangGraph 运行时 context），不进入 `AgentState` /
  `config["configurable"]`，因此不进 checkpoint。

## C1b DeepSeek 适配（`services/agent/app/llm/`）

- `make_llm` 固定 `deepseek-v4-pro`、显式 `https://api.deepseek.com/beta`、`thinking.type=disabled`、
  `temperature=0`；密钥只在客户端对象，不进 state/prompt/checkpoint/日志。
- `understand`：单个 `report_understanding` strict 工具（强制 tool_choice），仅验证失败重试一次；
  `plan`：复用 `plan_tool`/`from_strict_plan`+`PlanBody`，`request_id/session_id/utterance_ids` 由代码
  填，模型自填身份即失败；两者每次新建 system+human（无历史 assistant）。
- `gather`：只绑 18 个 read 工具（非 strict，参数本地契约校验），最多 3 轮，经显式 `read_executor`
  执行；拒绝未知/写工具与畸形 tool call、达到上限明确截断，`*_wall` 剔除、untrusted 用
  `<untrusted_data source=…>` 包裹并转义；输出完整 `ReadRecord`（tool/args/data/source_ref）供 plan 引用。
- 自动测试全部走 `httpx.MockTransport` 检查最终 HTTP payload；`live.py` 是手动 live 入口（理解2+
  计划3不同场景+gather1，硬预算8请求，含 usage/token/请求计数与语义断言），任一失败 exit 非 0，
  **已执行 7 次请求全部通过**（transfer 80000/target/span 正确、freeze/limits 来源语义成功、gather2轮成功）。
  需 `DEEPSEEK_API_KEY`（由协调者安全注入环境，不要把 key 复制进 worktree）。
- 本地模型：`needs_write`/`unclear` 用 `StrictBool`（拒字符串/数字宽松转型），`Slot` 非负
  `intent_index` + 合法 `span`。

## C1c1 gateway 协议客户端（`services/agent/app/gateway/`）

- `GatewayClient(base_url, service_token, *, http_client=None, timeout=10.0)`：A 服务令牌只进客户端
  配置，assistant JWT 每次调用从 `AgentContext` 取；每次调用经 `send(request, follow_redirects=False)`
  + `request.extensions["timeout"] = httpx.Timeout(timeout).as_dict()` 强制有限正超时与禁重定向；写/评估不
  自动重试、不轮询 `UNKNOWN`、不新建 plan 代替 UNKNOWN。真实 loopback 回归测试见
  `tests/agent/gateway/test_real_transport.py`（非 Mock）。
- 只读：`get_utterances`（G20）、`get_clarification`（G21）、`read_tool`（G2，18 个 `READ_ARGS` 白名单）。
- 写：`evaluate`（G4）、`execute`（G8）。
- 错误三类 `GatewayResponseError`/`GatewayProtocolError`/`GatewayTransportError`，不泄露凭证。

## C1d Agent 服务（`services/agent/app/main.py`）

- FastAPI app（`app_factory()`），三 HTTP 入口：`POST /threads`、`GET /threads/{id}/state`、
  `POST /threads/{id}/runs/stream`（SSE）。
- `AsyncPostgresSaver`：psycopg3 直接连接 postgres-agent / 临时 PG，启动时 `setup()` 建表。
- `pg_try_advisory_lock(hashtext(thread_id))`：每 thread 最多一个运行实例，冲突返回 409。
- JWT 鉴权：Ed25519/EdDSA，`verify_aud: False`，要求 `sub/sid/exp`；L1 只提取身份不做授权（D4）。
- SSE 事件：`updates`（含 `__interrupt__`）、`values`、`messages`、`error`。
- **端口当前为 fake**（`_make_fake_ports`）：understand/gather/plan/evaluate/execute/respond/verify_output
  均为返回固定结果的 lambda，不调真实 L2/L3；C1c2 图路由与 C1e 输出将替换为真实实现。

## 运行方式

- 单元测试：`make test`（`tests/agent/`，266 passed zero skip；含 26 真实 tmpfs PG 检查点测试）。
- Agent DB 测试：`make test-agent-db`（`AGENT_DB_TESTS=1`，零 skip，独立 tmpfs PG 容器）。
- 全栈启动：根目录 `make setup && make keys && make up`；agent 服务监听 8100（容器网络内）。
- live 测试：`python -m services.agent.app.llm.live`（需 `DEEPSEEK_API_KEY`，协调者手动跑）。

### 未覆盖 / 后续组件

- C1c2：完整图路由，真实 L2 G4/G8 适配与 gather→plan→evaluate→execute 控制流。
- C1e：输出校验、卡片适配、回放（replay）。
- 端口当前为 fake（`_make_fake_ports`），不调真实 L2/L3；真实联调待 C1c2/C1e。