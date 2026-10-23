# C1a 图骨架与 AgentState 独立评审

- 范围：`origin/main..HEAD`（业务 `afc8048`，head `3a6745a`）。只读；未改任何业务/测试/文档。
- 方法：读 `services/agent/app/graph/*`、`tests/agent/test_graph.py`、任务书 `C1-agent.md`、PRD §6.1/§6.5；定点复跑 `tests/agent/test_graph.py`（15 passed 零 skip）。协调者已亲跑 lint211/test1952/contracts/actionlint，本评审不复跑全量。
- 边界：不要求本组件提前做 C1b/c/d/e 的 LLM、真实 L2 客户端、PG/HTTP。范围只判 C1a 自身控制流、状态隔离与中断安全。

## 结论：REQUEST_CHANGES（1 条 P0 阻塞）

## 发现

### P0-1 中断恢复绕过身份/会话绑定，可跨客户调用 execute 端口

- 证据：身份校验只存在于 `ingest`：`services/agent/app/graph/graph.py:89-100`（`thread_id != context.session_id` 于 91-95；跨客户 `existing != context.customer_id` 于 96-100）。而 resume 时 LangGraph 从 interrupt 点重入，只重跑 `await_approval`（`graph.py:146-154`），该节点**不接收 `config`、不复核任何身份**，仅比较 `answer != approval_id`（152）后返回；`_route_await_approval`（71-73）见 `approval_id` 非空即路由到 `execute`（156-160），`execute` 直接用 `runtime.context` 调 `ports.execute`。`ingest` 被完全绕过。
- 失败场景（协调者真实 LangGraph 探针，本评审按代码确认其路径，未自行重跑 live 探针）：先 `cus_a/ses_a` 输入触发 `apv_a` interrupt；再用同 thread config、但 runtime context 换成 `cus_b/ses_a`（或 `ses_wrong`）执行 `Command(resume="apv_a")` → `ports.execute` 被调用 1 次、`state.customer_id` 仍为 `cus_a`。即 execute 端口被以「cus_a 的 plan + cus_b 的 assistant_token/session」的错配身份调用。C1c 接线后即变成用错客户令牌调 L2 G8 的副作用；不能依赖「未来 HTTP 入口兜底」，C1a 自身隔离必须闭合（任务书 §8.2、协调者要求）。
- 佐证：`tests/agent/test_graph.py:272-294`（resume 只重入 `await_approval`，ingest 不重跑）证明 ingest 校验不会在 resume 上生效。
- 修复建议：给 `await_approval` 增加 `config: RunnableConfig` 形参，在 `interrupt()` 之前（即任何端口副作用/路由前）复核三处绑定：`thread_id == context.session_id == state.session_id` 且 `context.customer_id == state.customer_id`，不一致抛 `SessionMismatchError`/`CrossCustomerError`。可提取一个共享 guard 供 resume 入口复用；`execute`（156-160）也可作第二道 fail-closed 复核，但最小闭合点在 `await_approval`。

### P2-1 ingest 不校验本轮原话非空，空输入可走完全链路

- 证据：`graph.py:101-115`，`texts` 为空时 `messages` 为空，`ingest` 不拒绝，图继续 understand→gather→plan→evaluate→execute。
- 失败场景：`graph.invoke({}, config, context)` 无任何用户输入，若注入的 `evaluate` 返回 ALLOW（C1a 测试假端口即如此）会直达 execute。这不是「自动审批」——决策权在 evaluate（L2 边界），但骨架缺最小输入守卫。
- 修复建议：`ingest` 对本轮 `utterances` 为空时 fail-closed（如抛 `AgentGraphError`/专用 code），与「无输入不得执行」一致。若视为 C1c 契约责任，至少在本组件留待决说明。

## 已核对的通过项

- 新 input 替代旧 interrupt、旧 approval 不能执行旧 plan：`test_new_input_replaces_pending_interrupt_and_old_resume_cannot_execute`（220-238）通过。
- 错 resume 不调 execute：`test_wrong_resume_does_not_execute`（206-217）通过；resume 值 != approval_id 返回 None → respond。
- 节点重跑前无副作用：`await_approval` 仅读 state、`interrupt()` 为第一动作，无端口调用；resume 不重跑前序端口（272-294）。
- 本轮字段重置：`ingest` 清 understanding/reads/plan_draft/plan_id/decision/replan_count/approval_id/execution/reply_draft（102-113），`messages` 按 add_messages 累积。
- 两 sid/两 customer 隔离、同 thread 跨 customer 拒绝、thread_id 必须匹配 session：`test_threads_isolated_between_customers`（241-252）、`test_same_thread_different_customer_rejected`（255-259）、`test_thread_id_must_match_session_id`（262-269）通过。
- 缺端口 fail-closed：`build_graph` 校验 7 端口 + checkpointer（83-87）；`test_missing_port_fails_closed`、`test_port_returning_none_fails_closed_without_execute_side_effect` 通过。
- secrets 不进 checkpoint/输出：token 只走 `AgentContext`（运行时 context），`test_runtime_secret_not_in_checkpoint_or_output`（297-303）通过。
- 边界模型占位不假称真实 wire：`state.py` docstring 与 README/进度均声明 Decision/Understanding/ReadRecord/UtteranceRef 为 C1a 本地占位，不代表 L2 授权。
- 无未经用户输入的自动审批：REQUIRE_APPROVAL 需 `answer == approval_id`（152），无任何路径自行置 approval_id；ALLOW 由注入的 evaluate 决定（骨架不发明决策）。
- 单元/内存/真实 PG/live 边界清晰：测试全部 InMemorySaver；README/进度明确「无 PG、未接 LLM/L2/L3、不宣称 Postgres saver 已验收」。

## 未确认（无证据，不列为发现）

- resume 送达时若线程已无 pending interrupt（前一轮 ALLOW/DENY 已完结）的 LangGraph 原生行为未在测试覆盖；属 C1d HTTP 层处理，不在本组件控制流内。
- `LANGGRAPH_STRICT_MSGPACK=true` 下 L1 本地 pydantic 模型的 msgpack 序列化告警（进度文档已自记待决），未复现。

---

## 复审（修复 `ba02aa0`，父 `3a6745a`）— 结论：APPROVE（P0/P1 已闭合，仅 P2 文案/覆盖）

- 复核方式：读 `graph.py`/`invoke.py`/`state.py`/`errors.py` 全量 diff 与测试 diff；亲跑 `tests/agent/test_graph.py` = 22 passed 零 skip；另用最小 StateGraph + `input_schema`(必填键) 探针验证 LangGraph 1.2.16 对 `graph.invoke({})` 的真实行为（见 P2-1）。协调者 lint/全量 test/contracts/actionlint 由其在跑，本复审不替其宣称。

### P0-1 复核：已闭合

- `await_approval` 增 `config` 形参（graph.py:177-190），`interrupt()` 前调用 `_require_resume_binding`（graph.py:82-101）复核 `thread_id==context.session_id`、`context.customer_id==state.customer_id`、`context.session_id==state.session_id`，不一致抛 `SessionMismatchError`/`CrossCustomerError`，任何 execute/respond/verify 端口副作用之前拒绝。错误在 await_approval 内抛出、条件边不执行，故 respond/verify 结构上不可达。
- 测试：错 customer（test_graph.py:376）、错 session（385）、错 thread（394）均 assert execute==0；坏 resume 后正确 context 可安全 continue 且 execute==1（405）。正确 resume 仍不重跑 ingest/plan/evaluate（既有 272-294 用例仍绿）。

### P2-1 复核：已闭合（边界 + ingest 兜底）

- `invoke_new_input`（invoke.py:19-27）在触碰 checkpoint 前对空/缺原话抛 `EmptyUtterancesError`；`ingest` 空原话兜底（graph.py:129-131）；直接 `invoke({"utterances": []})` 被拒（438），已完成 round 不被空输入消费（427）。新 input 替代旧 interrupt 后旧 approval 不执行旧计划（既有用例仍绿）。

### 新发现（均 P2，不阻塞）

- **P2-1（代码注释过度声明）**：`state.py:111-114` `AgentInput` docstring 称「`utterances` required 使 empty/omitted input 永远不能静默复用旧原话」——**不成立**。独立探针实测：LangGraph 1.2.16 对 `input_schema` 必填键**不在 invoke 时强制**，`graph.invoke({})` 在同 thread 上返回 `{'utterances': ['a']}`（复用旧值）。真正拒空靠 `invoke_new_input` 边界 + `ingest` 兜底，`input_schema` 仅作声明。建议改写该 docstring 指向真正的 guard，避免误导后续读者绕过 `invoke_new_input`。
- **P2-2（测试覆盖）**：错 customer/session/thread 三条用例只 assert `execute==0`，未显式 assert `respond`/`verify` 未被调（虽结构上成立），与要求「execute/respond/verify 无副作用」不完全对齐；建议补 `calls.get("respond",0)==0`、`verify==0`。
- **P2-3（用例命名 nuance）**：`test_resume_thread_id_mismatch_rejected_without_execute`（394）实际落在 `ses_other` 无 checkpoint 的 fresh-run 路径，由 `ingest` 的 thread_id 校验兜底，并非真正 resume 重入路径上的 `_require_resume_binding` thread_id 分支；真 resume 重入时 thread_id 必等于持 interrupt 的 thread，该分支与 session 校验冗余。实现者已在进度文档自记，非缺陷。

### 残留（已披露，属 C1d 义务，C1a 无 HTTP 下可接受）

- 裸 `graph.invoke({})`（缺 utterances key、身份正确）仍会静默复用旧原话并重跑整条链（重新 execute 旧计划）；这是 LangGraph 输入合并语义所致，`input_schema` 不拦截。当前仅由「C1d 只经 `invoke_new_input` 发起新输入」这一约定防止，进度文档「未覆盖/待决」已明确写出、未掩盖。C1a 无 HTTP 入口、消费者仅测试，故不阻塞；但 **C1d 不得把 `graph.invoke({})` 暴露为新输入路径**，且届时应在 HTTP 层做缺 utterances 的显式拒绝（非口头约定）。

### 已核对的通过项（复验）

- 错身份 resume 无 execute 副作用、错误恢复后正确续跑、正确 resume 不重跑前序节点、新 input 替代旧 interrupt 后旧 approval 不执行、重置本轮字段、两 customer/两 sid 隔离、secret 不进 checkpoint、缺端口 fail-closed 无 execute 副作用——全部保持绿；端口 fake/内存边界如实，未声称银行资金越权已测。

---

## 终审（`ba02aa0..8ed1f7c`）— 结论：APPROVE（无未闭项）

- 复核方式：读全量 diff；亲跑 `tests/agent/test_graph.py` = 22 passed 零 skip；`git diff ba02aa0..8ed1f7c -- graph.py invoke.py errors.py packages contracts` 输出为空，确认无生产行为/冻结区漂移。
- P2-1（docstring 过度声明）已闭合：`state.py` `AgentInput` docstring 现准确写明「type declaration 不能使 `graph.invoke` 拒绝缺 `utterances` 键、`invoke({})` 仍复用旧原话；新输入必须经 `invoke_new_input`，`ingest` 空原话检查为第二道图内兜底」，与实测 LangGraph 1.2.16 行为一致。
- P2-2（计数断言）已闭合：错 customer（test_graph.py:376-384）、错 session（385-393）、错 thread（404-412 更名后）三处均补 `execute==0` + `respond==0` + `verify_output==0`，无遗漏。
- P2-3（命名 nuance）已闭合：错 thread 用例改名 `test_thread_id_session_mismatch_rejected_via_ingest_guard`，注释明确「无 checkpoint 走 fresh ingest guard、不冒充 await_approval resume guard」，与代码路径一致。
- 残留项维持原判：裸 `graph.invoke({})` 复用旧原话仍属 C1d 义务（进度文档已披露），C1a 无 HTTP 下不阻塞。
