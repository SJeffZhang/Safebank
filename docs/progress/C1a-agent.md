# C1a 图骨架与 AgentState（L1）进度

实现 worker：本机 DeepSeek V4 Pro（Claude Code CLI）。范围：仅 `services/agent/**`、
`tests/agent/**` 与本文件；未改 contracts/gateway/bankcore/L3/Makefile/compose/依赖锁/AGENTS/PRD。

## 交付内容

- `services/agent/app/graph/state.py`：`AgentState`（TypedDict，`messages` 用 `add_messages`）、
  `AgentContext`（助手令牌/customer/session，运行时 context）与 C1a 本地模型 `UtteranceRef`、
  `Slot`、`Understanding`、`ReadRecord`、`Decision`、`Finding`、`DecisionValue`。
- `services/agent/app/graph/ports.py`：`Ports`（7 个显式注入的类型化端口）。
- `services/agent/app/graph/errors.py`：`AgentGraphError` / `CrossCustomerError` /
  `SessionMismatchError` / `MissingPortError` / `PortFailure`。
- `services/agent/app/graph/graph.py`：`build_graph(ports, checkpointer)`，节点
  `ingest/understand/gather/plan/evaluate/await_approval/execute/respond/verify_output` 与
  条件边（understand/gather/evaluate/await_approval 四处分叉）。
- `tests/agent/test_graph.py`：15 项行为测试（内存 `InMemorySaver`）。
- `services/agent/README.md`：骨架可用范围 / 运行方式 / 生产入口仍为探针。

## 红 → 绿记录

1. **先写行为测试（红）**：先落测试文件再实现，首跑 `pytest tests/agent -q` 因模块不存在全错；
   实现后首轮红 `5 failed, 10 passed`，失败为真实语义问题，逐项定位：
   - `test_second_message_resets_round_fields` / `test_replan_agent_reroutes_and_bounds`：测试的
     `execute` 端口硬编码 `plan_id="pln_a"`，未回显 state 的 plan_id（测试缺陷，非骨架缺陷）。
   - `test_ports_called_once_before_interrupt`：`evaluate` 覆盖 lambda 未 bump 计数（测试缺陷）。
   - `test_wrong_resume_does_not_execute`、`test_new_input_replaces_pending_interrupt_and_old_resume_cannot_execute`：
     先经最小探针确认两件事实：① 新 input 在 pending interrupt 时**原生从 START 重新开始**（旧
     interrupt 被新 interrupt 取代，无需 ingest 额外清理）；② 节点返回 `Command(goto=...)` **不会**
     覆盖静态边（同一 super-step 里 execute 与 respond 都会跑，触发 `InvalidUpdateError`）。
     因此 `await_approval` 改用「返回值写 `approval_id` + 条件边 `_route_await_approval`」：正确
     resume 才 `approval_id` 非空 → execute，错 resume → None → respond。
2. **实现后绿测**：`pytest tests/agent -q` = `15 passed`（零 skip）。

## 退出命令与结果（本组件无 PG）

| 命令 | 结果 |
|---|---|
| `make lint` | ruff check 通过；ruff format --check 通过（184 files already formatted） |
| `.venv/bin/pytest tests/agent -q` | `15 passed`（零 skip） |
| `make test` | `423 passed, 302 skipped`（skip 为无 PG 配置的集成/持久化项，非本组件范围） |
| `make contracts-check` | `PASS: contracts v1 match frozen schemas` |

## 未覆盖 / 待决（交协调者或后续组件）

- 未接 live LLM、未接 L2/L3：`understand/gather/plan/evaluate/execute/respond/verify_output`
  端口是注入的假实现，仅验证控制流；**不声称**真实 G3/G4/G8 联调。
- 本组件无 PostgreSQL；`AsyncPostgresSaver`、thread 运行锁、`pg_try_advisory_lock` 留 C1d。
- 完整返回码表（401/403/404、409、429、503、STATE_CHANGED/UNKNOWN 等）留 C1c；`evaluate` 的
  REPLAN 定向补读（`evidence_refs`）与 execute 结果路由留 C1c。
- `Decision`/`Understanding`/`ReadRecord` 等是 C1a 本地最小模型，未对齐真实 L2 wire / contracts；
  `messages` 的 user 文本由 `ingest` 拼接原话 text 写入（C1b/C1c 若调整入口需同步）。
- 序列化提示：InMemorySaver 对 L1 本地 pydantic 模型与 `PlanDraft`/`ExecutionResult` 反序列化会打
  `Deserializing unregistered type ... will be blocked in a future version` 告警（当前可重建）；若
  后续启用 `LANGGRAPH_STRICT_MSGPACK=true` 需显式 `allowed_msgpack_modules`，留待 C1d/协调者定夺。
