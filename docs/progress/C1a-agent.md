# C1a 图骨架与 AgentState（L1）进度

实现 worker：本机 DeepSeek V4 Pro（Claude Code CLI）。范围：仅 `services/agent/**`、
`tests/agent/**` 与本文件；未改 contracts/gateway/bankcore/L3/Makefile/compose/依赖锁/AGENTS/PRD。

## 交付内容

- `services/agent/app/graph/state.py`：`AgentState`（TypedDict，`messages` 用 `add_messages`）、
  `AgentContext`（助手令牌/customer/session，运行时 context）、`AgentInput`（新输入边界 Schema，
  `utterances` 必填）与 C1a 本地模型 `UtteranceRef`、`Slot`、`Understanding`、`ReadRecord`、
  `Decision`、`Finding`、`DecisionValue`。
- `services/agent/app/graph/ports.py`：`Ports`（7 个显式注入的类型化端口）。
- `services/agent/app/graph/errors.py`：`AgentGraphError` / `CrossCustomerError` /
  `SessionMismatchError` / `MissingPortError` / `EmptyUtterancesError` / `PortFailure`。
- `services/agent/app/graph/graph.py`：`build_graph(ports, checkpointer)`，节点
  `ingest/understand/gather/plan/evaluate/await_approval/execute/respond/verify_output` 与
  条件边；`await_approval` 在 `interrupt()` 之前复核身份/会话绑定（P0）。
- `services/agent/app/graph/invoke.py`：`invoke_new_input`，唯一的新输入边界，空原话 fail-closed（P2）。
- `tests/agent/test_graph.py`：22 项行为测试（内存 `InMemorySaver`）。
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

### 独立评审 REQUEST_CHANGES 后的 P0/P2 修复（2026-10-24）

评审 `docs/reviews/C1a-agent-review.md`：P0-1 resume 身份绑定绕过（`await_approval` 不复核
customer/session，`ingest` 被 resume 跳过，跨客户 execute 端口被调用）；P2-1 `ingest` 不校验本轮
原话非空，空输入可走完整写链。

**P0 红→绿**：先落 4 项红测（同 thread pending `apv_a` 分别以 `cus_b/ses_a`、`cus_a/ses_wrong`、
不一致 thread_id resume 均不得调 execute；坏 resume 后再以正确身份 resume 必须安全）。临时注释掉
`await_approval` 里的绑定 guard 复跑，实际红测 `3 failed, 1 passed`：`test_resume_wrong_customer_*`、
`test_resume_wrong_session_*`、`test_correct_resume_after_bad_resume_is_safe` 均 `DID NOT RAISE`
（execute 端口被以错配身份调用）；`test_resume_thread_id_mismatch_*` 由 ingest 的 thread_id 校验兜底
（fresh thread 路径）故红测通过。恢复 guard 后 4 项转绿。修复：`await_approval` 增加
`config: RunnableConfig` 形参，`interrupt()` 之前调用共享 guard `_require_resume_binding` 复核
`thread_id == context.session_id`、`context.customer_id == state.customer_id`、
`context.session_id == state.session_id`，不一致抛 `SessionMismatchError`/`CrossCustomerError`，
任何 execute/respond/verify 端口副作用之前拒绝；正确 resume 仍不重跑 ingest/plan/evaluate，不重复消费。

**P2 红→绿**：先探针复现两事实：① `graph.invoke({})` 在已完成 round 的同 thread 上**会默默复用旧
utterances**（`SAME_THREAD_MISSING_UTT: NO ERROR (old utterances reused!)`，此时 input_schema +
ingest 空校验都无法区分缺 key）；② `graph.invoke({"utterances": []})` 走 ingest 空校验可拒。修复：
新增 `AgentInput` 输入 Schema（`utterances` 必填，声明契约）+ 唯一新输入边界 `invoke_new_input`
（`utterances` 非空才 `graph.invoke({"utterances": [...]})`，空/缺一律 `EmptyUtterancesError`，
不触碰旧 checkpoint 的旧计划）+ `ingest` 空原话 fail-closed 兜底（直接 `invoke({"utterances": []})`
也被拒）。新 input 与 resume 保持清楚区分：新 input 只经 `invoke_new_input`，resume 只经
`Command(resume=...)`。仅改 services/agent 与 tests/agent，未自制平行状态机、未越界改 API/共享 schema。

**协调者亲验后收尾（小改，不更动执行逻辑）**：① `state.py` 的 `AgentInput` docstring 更正为真实行为
——输入类型声明**不能**让 `graph.invoke` 拒绝缺键（`invoke({})` 仍走 state 合并），服务新输入必须经
`invoke_new_input` 验证、`ingest` 兜底；② 错 customer/sid 两个 resume 用例补 `respond==0`、
`verify_output==0` 断言；错 thread 用例改名 `test_thread_id_session_mismatch_rejected_via_ingest_guard`
并注明走 fresh ingest guard（无 checkpoint 于 `ses_other`），不冒充 await_approval resume guard 覆盖。


## 退出命令与结果（本组件无 PG）

| 命令 | 结果 |
|---|---|
| `make lint` | ruff check 通过；ruff format --check 通过（212 files already formatted） |
| `.venv/bin/pytest tests/agent -q` | `22 passed`（零 skip） |
| `make test` | `1959 passed, 461 skipped`（skip 为无 PG 配置的集成/持久化项，非本组件范围） |
| `make contracts-check` | `PASS: contracts v1 match frozen schemas` |

## 未覆盖 / 待决（交协调者或后续组件）

- 未接 live LLM、未接 L2/L3：`understand/gather/plan/evaluate/execute/respond/verify_output`
  端口是注入的假实现，仅验证控制流；**不声称**真实 G3/G4/G8 联调。
- 本组件无 PostgreSQL；`AsyncPostgresSaver`、thread 运行锁、`pg_try_advisory_lock` 留 C1d。
- 完整返回码表（401/403/404、409、429、503、STATE_CHANGED/UNKNOWN 等）留 C1c；`evaluate` 的
  REPLAN 定向补读（`evidence_refs`）与 execute 结果路由留 C1c。
- `Decision`/`Understanding`/`ReadRecord` 等是 C1a 本地最小模型，未对齐真实 L2 wire / contracts；
  `messages` 的 user 文本由 `ingest` 拼接原话 text 写入（C1b/C1c 若调整入口需同步）。
- `AgentInput` 的 `input_schema` 在当前库版本不强制执行（`invoke({})` 仍走 state 合并），真正拒空
  依赖 `invoke_new_input` 边界 + `ingest` 兜底；C1d HTTP 层须只经 `invoke_new_input` 发起新输入。
- 序列化提示：InMemorySaver 对 L1 本地 pydantic 模型与 `PlanDraft`/`ExecutionResult` 反序列化会打
  `Deserializing unregistered type ... will be blocked in a future version` 告警（当前可重建）；若
  后续启用 `LANGGRAPH_STRICT_MSGPACK=true` 需显式 `allowed_msgpack_modules`，留待 C1d/协调者定夺。
- resume 送达时若线程已无 pending interrupt（前一 round 已完结）的 LangGraph 原生行为未在本组件
  测试（评审「未确认」节）；属 C1d HTTP 层处理，不在本组件控制流内。
