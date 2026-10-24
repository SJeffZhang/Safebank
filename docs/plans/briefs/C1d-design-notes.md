# C1d Design Notes: Facts, Gaps, and Decisions

> 设计起草 worker 只读代码后整理的发现与待裁定问题。协调者审核后逐条裁定，再写入任务书。

---

## 1. 代码事实与缺口

### 1.1 Agent 服务入口不存在

- `services/agent/app/main.py` 不存在（`Glob` 空结果）
- 当前 agent 容器启动的是 `scripts.agent_entrypoint`（见 compose `agent.command`：无 command 行 → 用 Dockerfile 默认 CMD）
- `scripts/agent_entrypoint.py` 当前可能只有一个健康探针（P0 验证），需要替换为 FastAPI 三接口
- **建议**：确认当前 `scripts.agent_entrypoint` 的内容，C1d 新建 `services/agent/app/main.py` 并作为新入口

### 1.2 Docker Compose agent 服务定义

- `docker-compose.yml` 第 33–62 行：agent 服务端口 8100，agent-net IP 172.29.10.11
- 挂载 `.secrets/jwt-public.pem:/run/secrets/jwt-public.pem:ro`
- 环境文件 `.secrets/agent.env`
- `read_only: true`、`tmpfs: /tmp`
- 不挂载 agent 源码目录（代码在镜像内）
- **注意**：C1d 测试用临时 PG（不连接 compose 的 postgres-agent），全栈验证由协调者跑

### 1.3 密钥生成

- `scripts/keys.py` 第 50–52 行：agent 库密码为 `passwords["agent_app"]`（`secrets.token_hex(24)`）
- 第 56 行：agent server 的角色为 `agent_app`
- 第 73 行：`postgres-agent-password` 文件（postgres 超级用户密码，`secrets.token_hex(24)`）
- 第 97–103 行：`agent.env` 内容：
  - `AGENT_SERVICE_TOKEN`（调 L2 的服务令牌）
  - `AGENT_DATABASE_URL` = `postgresql://agent_app:{password}@postgres-agent/agent`
- **JWT 公钥**：第 114–120 行生成 `jwt-public.pem`（Ed25519 公钥，0644），已挂载到 agent 容器

### 1.4 依赖状态

- `pyproject.toml` 的 poc deps 第 23–27 行：
  - `langgraph==1.2.16`（MIT）
  - `langgraph-checkpoint-postgres==3.2.0`（MIT）
  - `psycopg[binary,pool]==3.3.3`（LGPL-3.0）
- FastAPI 不在 poc deps 中显式列出——需核实是否在 uv.lock 中（如果 gateway 已依赖 FastAPI，Dockerfile 多阶段构建会包含）
- starlette（SSE 支持）应是 FastAPI 的传递依赖

### 1.5 现有图构造接口

- `services/agent/app/graph/graph.py` 第 108–249 行：`build_graph(ports, checkpointer)` 和 `build_gateway_graph(ports, checkpointer, client, *, gateway_read_client=None)`
- 参数 `checkpointer: BaseCheckpointSaver`——接受 InMemorySaver 或 AsyncPostgresSaver
- 第 249 行：`builder.compile(checkpointer=checkpointer)`——checkpointer 注入到编译时
- 当前所有测试使用 `InMemorySaver()`
- **不变量**：graph 编译后 checkpointer 不可换——要用新 checkpointer 必须重新 `build_gateway_graph` + `compile`

### 1.6 新输入边界

- `services/agent/app/graph/invoke.py` 第 25–35 行：`invoke_new_input(graph, utterances, *, config, context)`——同步 wrapper
- 第 38–70 行：`invoke_gateway_input(graph, utterance_ids, *, config, context, client)`——同步 wrapper，先调 G20 再 invoke
- **都是同步**——LangGraph `graph.invoke()` 是同步方法。在 async FastAPI handler 中需要用 `asyncio.to_thread` 或 LangGraph 的 `graph.ainvoke()`（LangGraph 1.x 应该有 async 版本）
- **待核实**：`CompiledStateGraph` 是否有 `ainvoke` 方法。如果 LangGraph 1.2.16 提供 `ainvoke`，C1d 直接用它，不需要线程池。如果只用 `invoke`，需要 `run_in_executor`。

### 1.7 GatherAdapter.with_read_executor 模式

- `services/agent/app/llm/gather.py` 第 55–66 行：`with_read_executor` 返回 shallow copy，共享 `_model` / `_observer`，独立 `_read_executor`
- C1c2 的 graph 已使用此模式（`graph.py` 第 652–664 行：`adapter = ports.gather.with_read_executor(executor_for_call)`）
- C1d 不需要改 gather——HTTP handler 每次调用创建新的 `executor_for_call`，与 checkpoint 无关

### 1.8 AgentState 字段

- `services/agent/app/graph/state.py` 第 113–136 行：19 个字段
- 第 104–111 行：`AgentContext` dataclass（`assistant_token`、`customer_id`、`session_id`）
- **关键**：`assistant_token` 是完整的 JWT 字符串，用于调 L2。必须不进 checkpoint。
- PRD 第 768 行要求全表搜索 0 条，本组件需要按此验证

### 1.9 现有测试结构

- `tests/agent/` 下已有 20 个测试文件
- `tests/agent/gateway/transport.py` 有 `ScriptedGateway` 和 `json_response`/`error_response` helper——可复用于 fake L2 响应
- `tests/gateway/db/conftest.py` 第 1–139 行：临时 PG fixture 模板（随机端口、tmpfs、alembic migration、`docker rm -f`）
- C1d 的 fixture 比 gateway 简单——不需要 alembic，只需要 `checkpointer.setup()` 建三张表

---

## 2. PRD 不闭合点

### 2.1 P0 实验 E7 的状态

- PRD 第 769 行：【P0 实验】验证在有待处理 interrupt 的线程上用新 input 调用时图会从 START 重新开始、旧 interrupt 被丢弃；不成立则由 ingest 显式清理
- P0 验收（`docs/acceptance/P0.md`）中 LangGraph 内存 checkpoint 实验结论只覆盖「runtime context 不入 checkpoint」，**未覆盖 E7**
- ADR-002 第 29 行：「内存 checkpoint 中断恢复与 runtime context 脱敏验证见 tests/test_langgraph.py；Postgres saver 持久化与恢复进入相应组件验收」
- **意味着**：E7 需要用真实 PG checkpoint 验证，不应假设内存 saver 上的行为与 PG saver 一致
- **建议**：C1d Task 4 的红测必须在 PG checkpoint 上运行，如果行为与内存 saver 不同，在 ingest 中补显式清空

### 2.2 Thread lock 的实现细节

- PRD 第 769 行说用 `pg_try_advisory_lock(hashtext(thread_id))`
- `hashtext()` 是 PostgreSQL 内置函数，需要一条 SQL 语句：`SELECT pg_try_advisory_lock(hashtext($1))`
- Lock 的持有者是一个数据库连接——如果 handler 用 async，多个协程共享同一连接池，需要确保 acquire 和 release 在**同一连接**上
- **建议**：用独立的 psycopg3 连接（非池化），专门拿锁。`psycopg.connect(AGENT_DATABASE_URL)` + `conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (thread_id,))` + `finally: conn.close()`（close 自动释放锁）

### 2.3 AsyncPostgresSaver 与 graph.astream 的兼容性

- `AsyncPostgresSaver` 名中有 "Async"，但 LangGraph 1.x 的 `graph.astream()` 可能需要 `async` checkpointer
- `langgraph-checkpoint-postgres` 3.2.0 提供的 `AsyncPostgresSaver` 实现了 `BaseCheckpointSaver` 的 async 接口
- `graph.astream()` 应能接受它——**待协调者确认**或 P0 实验快速验证
- 如果不能，回退为 `graph.invoke()` + `asyncio.to_thread`（完全串行、无流式），但 SSE 只能推送最终结果而非中间事件
- **建议**：在 Task 0 先验证 `graph.astream(checkpointer=AsyncPostgresSaver(...))` 能正常工作

### 2.4 JWT 验证的边界

- PRD 第 995 行：BFF 拿浏览器 cookie，与助手 JWT 是不同东西
- 助手 JWT 由 L2 G3 签发（`services/gateway`），L1 只需要用公钥验签
- C1d 范围：实现 Ed25519 签名验证 + `exp` 时效检查。JWT 的 `sub`(customer_id) / `sid`(session_id) 校验。
- `sub` 和 `sid` 的 claim 命名由 L2 G3 的 JWT 签发逻辑决定——**需要确认实际 claim 名称**，或从 L2 代码 (`services/gateway`) 中读取
- **建议**：协调者先确认 L2 G3 签发的 JWT claim 结构，或赋权 C1d 只读查看 gateway 的 JWT 签发代码

### 2.5 HTTP 鉴权中间件

- Agent service 不持有服务令牌来验证调用方——它接收客户端发来的 assistant JWT，验证签名后就把 `sub`/`sid`/原始 token 放入 AgentContext
- 调用方身份验证依赖 JWT 签名——不需要额外的 API key 或 service token 鉴权
- `/threads` 和 `/threads/{tid}/state` 也验证 JWT（不仅是 `/runs/stream`）
- **安全问题**：如果 agent service 的 PoC 环境暴露在 agent-net 外，任何人都可以发伪造 JWT（如果公钥泄露）。但公钥不是秘密，属于已知风险——真正的防线是 agent 不能写 L3。

### 2.6 SSE 中间事件

- PRD 第 847 行：`updates` 事件中的 `__interrupt__` 必须是该 run 的**最后一条** `updates`
- LangGraph `astream` 的 `updates` 按节点迭代产出。interrupt 节点 `await_approval` 是图中一个节点，其后还有 `verify_output` 和 `END`——所以 `__interrupt__` 不会出现在最后
- **建议**：C1d 的 SSE 处理逻辑需要识别 `__interrupt__` 并特别处理（不等待后续节点），或如实按 `astream` 产出的顺序推送所有事件

### 2.7 `autocommit=True` 与 psycopg3

- `langgraph-checkpoint-postgres` 文档可能要求 `autocommit=True`，但 psycopg3 的连接参数中 `autocommit` 是 `Connection` 属性而非连接串参数
- **建议**：C1d 在 `AsyncPostgresSaver.from_conn_string()` 之后，或直接在连接串中，按 langgraph-checkpoint-postgres 3.2.0 的实际文档/源码设置

---

## 3. 协调者裁定问题清单

### Q1: scripts/agent_entrypoint 如何处理？

当前容器入口是 `scripts/agent_entrypoint.py`（P0 健康探针）。
- **选项 A**：C1d 替换为 `services/agent/app/main.py`（改 compose command）
- **选项 B**：C1d 只建 `main.py`，不改 compose，协调者在全栈验证时改 compose
- **建议**：B（C1d 不改 compose，只在任务书中写清楚新入口路径）

### Q2: FastAPI / starlette 是否需要新增依赖？

- FastAPI 可能已通过 gateway 的依赖在锁文件中
- **建议**：Task 0 先核实。如果不在锁文件中，请协调者添加（`fastapi` + `uvicorn[standard]`，都是 BSD/MIT），C1d 不自行改 `pyproject.toml`

### Q3: `graph.ainvoke()` 还是 `asyncio.to_thread(graph.invoke)`？

- LangGraph 1.2.16 的 `CompiledStateGraph` 应该有 `ainvoke` 方法（异步版本）
- **建议**：Task 0 先验证。如果有 `ainvoke`，SSE 中直接用 `async for event in graph.astream(...)`；如果没有，回退到 `asyncio.to_thread`

### Q4: JWT claim 结构确认

- L2 G3 签发的 assistant JWT 中 `sub` 和 `sid` 的 claim 名是什么？
- L2 gateway 的 JWT 签发代码在当前 worktree 不可见（agent worktree 只能读 agent 代码）
- **建议**：协调者确认真实 claim 名称，或授权 C1d worker 只读查看 `services/gateway` 中的 JWT 签发代码

### Q5: `make test-agent-db` 是否需要接入 CI？

- PRD §15 DoD 要求 P2 有 L1 真实验收
- **建议**：C1d 只加 Makefile 目标，不改 `checks.yml`。协调者在 C1d 合入后补 CI 行

### Q6: Agent 健康探针更新？

- 当前 compose 第 56–62 行：healthcheck 用 `urllib.request.Request('http://127.0.0.1:8100/health')`
- C1d 的 FastAPI app 是否需要 `/health` 端点？
- **建议**：C1d 加一个简单的 `/health` 返回 200（不做 DB readiness），保持与现有 compose 兼容。全栈 DB readiness 由协调者后续处理

### Q7: C1d 是否需要改 `scripts/agent_entrypoint.py`？

- 如果 C1d 新建 `services/agent/app/main.py`，建议不改 `scripts/agent_entrypoint.py`（冻结区外）
- 协调者在全栈验证时改 compose 的 `command` 指向新入口
- **建议**：C1d 不改 `scripts/agent_entrypoint.py`

---

## 4. 已读文件清单

- `AGENTS.md`（§2、§8、§5 下一步）
- `PRD.md`（§6.1、§6.1.1、§6.5、§9.5、§11.1、§13、§15）
- `services/agent/app/graph/state.py`（AgentState、AgentContext、AgentInput）
- `services/agent/app/graph/graph.py`（build_graph、build_gateway_graph）
- `services/agent/app/graph/invoke.py`（invoke_new_input、invoke_gateway_input）
- `services/agent/app/graph/routing.py`（RouteResult、route_evaluate、route_execute_error）
- `services/agent/app/graph/ports.py`（Ports dataclass）
- `services/agent/app/graph/errors.py`（异常类）
- `services/agent/app/llm/gather.py`（GatherAdapter.with_read_executor）
- `services/agent/app/gateway/client.py`（GatewayClient 前 30 行）
- `tests/agent/routing/test_graph_compiled.py`（编译图测试模式）
- `tests/agent/gateway/transport.py`（ScriptedGateway）
- `tests/gateway/db/conftest.py`（临时 PG fixture 模板）
- `docker-compose.yml`（agent / postgres-agent 定义）
- `scripts/keys.py`（agent 凭据生成）
- `pyproject.toml`（poc 依赖）
- `Makefile`（test-agent-db 目标模式）
- `docs/acceptance/C1a-agent.md`、`L1-C1b-deepseek-adapters.md`、`L1-C1c1-gateway-client.md`、`L1-C1c2-gateway-routing.md`、`P0.md`
- `docs/plans/briefs/W4b2a-runtime-keys.md`（任务书格式参考）
- `docs/plans/briefs/C1-agent.md`（C1 总体计划）
- `docs/decisions/ADR-002-llm.md`（P0 实验结论）