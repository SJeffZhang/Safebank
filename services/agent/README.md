# agent（L1 Banking Agent，C1a 图骨架）

L1 使用 LangGraph；本组件只交付**图骨架**（`services/agent/app/graph/`）：`AgentState`、
`AgentContext`（运行时上下文）、类型化端口 `Ports` 与 `build_graph`。生产 HTTP 入口仍是
P0 探针（`scripts/health_app.py`），本组件**未**开放业务入口、未接 LLM、未接 L2/L3。

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
- `Decision` / `Understanding` / `ReadRecord` / `UtteranceRef` 是 C1a 本地最小模型，**不代表 L2
  授权**；C1b/C1c 换成真实 contracts / L2 线上类型。

## 运行方式

- 单元测试：`make test`（`tests/agent/test_graph.py`，内存 saver，零 skip）。
- 本组件无 PostgreSQL；**不声称** Postgres saver 已验收（留 C1d）。
- 启动：根目录 `make setup && make keys && make up`；端口 8100 只在容器网络内（仍为探针）。
