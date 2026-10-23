# agent（L1 Banking Agent，C1a 图骨架 + C1b DeepSeek 适配）

L1 使用 LangGraph；已交付**图骨架**（`services/agent/app/graph/`）与 **C1b DeepSeek 适配**
（`services/agent/app/llm/`）：`AgentState`、`AgentContext`、`Ports`、`build_graph`，以及
understand/gather/plan 三个端口实现。生产 HTTP 入口仍是 P0 探针（`scripts/health_app.py`），
本组件**未**开放业务入口、未接 L2/L3，未跑真实 live。

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

## 运行方式

- 单元测试：`make test`（`tests/agent/`，内存 saver，零 skip；live 用 `python -m services.agent.app.llm.live` 手动跑）。
- 本组件无 PostgreSQL；**不声称** Postgres saver 已验收（留 C1d）。
- 启动：根目录 `make setup && make keys && make up`；端口 8100 只在容器网络内（仍为探针）。
