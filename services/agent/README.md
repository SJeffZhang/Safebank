# agent（L1 Banking Agent，C1a 图骨架 + C1b DeepSeek 适配 + C1c1 gateway 协议客户端）

L1 使用 LangGraph；已交付**图骨架**（`services/agent/app/graph/`）、**C1b DeepSeek 适配**
（`services/agent/app/llm/`）与 **C1c1 gateway 协议客户端**（`services/agent/app/gateway/`）：
`AgentState`、`AgentContext`、`Ports`、`build_graph`，understand/gather/plan 三个端口实现，以及
G20/G21/G2/G4/G8 的严格 HTTP/wire 边界。生产 HTTP 入口仍是 P0 探针（`scripts/health_app.py`），
本组件**未**开放业务入口、未接 L2/L3 真实联调，未跑真实 live。

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
  **尚未执行**；需 `DEEPSEEK_API_KEY`（由协调者安全注入环境，不要把 key 复制进 worktree）。
- 本地模型：`needs_write`/`unclear` 用 `StrictBool`（拒字符串/数字宽松转型），`Slot` 非负
  `intent_index` + 合法 `span`。

## C1c1 gateway 协议客户端（`services/agent/app/gateway/`）

- `GatewayClient(base_url, service_token, *, http_client=None, timeout=10.0)`：A 服务令牌只进客户端
  配置，assistant JWT 每次调用从 `AgentContext` 取；每次调用经 `send(request, follow_redirects=False)`
  + `request.extensions["timeout"]` 强制有限正超时与禁重定向（不信任注入 client 默认，构造器拒
  NaN/Inf/0/负数 timeout）；写/评估不自动重试、不轮询 `UNKNOWN`、不新建 plan 代替 UNKNOWN。
- 只读：`get_utterances`（G20，ids 精确匹配无重复/漏项/跨请求）、`get_clarification`（G21，null→None）、
  `read_tool`（G2，18 个 `READ_ARGS` 白名单，必填字段不补猜，结果经 `READ_RESULTS` 校验、原始 data 原样）。
- 写：`evaluate`（G4，PlanDraft 原样 body，无 extra origin/auth，`session_id` 与 context 不符零 HTTP）、
  `execute`（G8，body 仅 `{request_id}`，200 复用冻结 `ExecutionResult`、202 `UnknownExecution`，均须
  `plan_id` 等于目标；409 `STATE_CHANGED`/`POLICY_CHANGED` 的 `details.decision` 完整保留且绑定原 plan）。
- wire 严格：`decision`/`risk`/`parse_status`/clarification `kind`/finding `code` 闭集；`scope`/
  `partial_scope`/replan-hint `scope` 为结构化 `NormalizedScope` mirror（transfer 金额严格 `Cents`，
  `fields` free map）；`shadow_b1` 递归 `GatewayDecision | None`；`kind` 保持开放 `str`。
- 错误三类 `GatewayResponseError`/`GatewayProtocolError`/`GatewayTransportError`（`category` 供 C1c2
  路由；`__str__` 只含固定类别/状态/code，不回显凭证、原始 httpx 异常或服务端 message；`details` 只保留
  wire 校验后的 `decision` 模型 dump，其余键丢弃；`trace_id` 只接受受限安全形状否则省略）。
- 全部自动测试走 `httpx.MockTransport`（fake），**不构成真实 L1→L2→L3 联调**；G4/G8 当前仍是 L2 stub。
  错误路由、REPLAN 定向补读、typed/hint/action 语义与 `GatherAdapter.ReadExecutor` 桥接留 C1c2。

## 运行方式

- 单元测试：`make test`（`tests/agent/`，内存 saver，零 skip；live 用 `python -m services.agent.app.llm.live` 手动跑）。
- 本组件无 PostgreSQL；**不声称** Postgres saver 已验收（留 C1d）。
- 启动：根目录 `make setup && make keys && make up`；端口 8100 只在容器网络内（仍为探针）。
