# C1d Agent Service: Real Postgres Checkpoint, Thread Lock, HTTP/SSE Entrypoint

> 设计起草 worker（只读代码，只写本文档与 C1d-design-notes.md），基于 main 1ce15f0，分支 codex/c1d-agent-service。本文档是 **C1d 实现任务书**，待协调者审核后派发本机 Claude Code CLI DeepSeek V4 Pro worker。

**Goal:** L1 Banking Agent 从纯内存 fake 升级为真实持久化服务——Postgres checkpoint saver、线程级运行锁、旧 interrupt 替代、PRD §6.5 三 HTTP 入口、进程重启恢复，存储中不含任何 token/key。**不含** C1e（输出校验、卡片、回放）、前端/BFF。

**Architecture:** `AsyncPostgresSaver`（psycopg3）管理 LangGraph checkpoint 到临时 agent PG（测试）或 postgres-agent 容器（全栈）；FastAPI + SSE 提供三 HTTP 入口；应用层 `pg_try_advisory_lock` 保证每 thread_id 最多一个运行实例；`invoke_new_input` / `invoke_gateway_input` 为唯一新输入边界（禁止裸 `graph.invoke({})`）。

**Spec:** PRD §6.1（AgentState、AgentContext、interrupt/resume）、§6.5（流式接口）、§9.5（A 服务令牌权限）、§11.1（部署、agent 网络、postgres-agent）、§13 P2、§15 DoD；ADR-002（E7 checkpoint 实验结论）；C1 任务书中 C1d 行；本 worktree `services/agent/`、`tests/agent/`、`docker-compose.yml` agent / postgres-agent、`scripts/keys.py` agent 凭据、`pyproject.toml` poc 依赖。

---

## Global Constraints & ownership

**精确文件白名单（只能改/新增这些）：**

| 可改/新增 | 说明 |
|---|---|
| `services/agent/app/main.py`（新建） | FastAPI 应用、三 HTTP 入口、SSE、pg_try_advisory_lock、checkpoint saver 构造 |
| `services/agent/app/graph/invoke.py` | 必要时加 async 变体 `ainvoke_new_input` |
| `services/agent/app/graph/graph.py` | **只读参考**，不改（checkpointer 注入由 main.py 完成，已有接口不变） |
| `services/agent/README.md` | 更新启动方式、真实 PG 说明、未覆盖项 |
| `tests/agent/conftest.py`（新建或扩） | 临时 agent PG fixture（仿 `tests/gateway/db/conftest.py`） |
| `tests/agent/test_service.py`（新建） | HTTP/SSE/重启/锁/checkpoint 不含 token 测试 |
| `tests/agent/test_checkpoint_isolation.py`（新建） | checkpoint 全表搜索 token 为零、thread 隔离 |
| `tests/agent/test_lock.py`（新建） | 线程锁并发测试 |
| `docs/progress/C1d-agent-service.md`（新建） | 进度、红绿、退出命令 |
| `docs/acceptance/L1-C1d-agent-service.md`（新建，协调者写） | 验收报告 |

**严格冻结（只读，不改）：**

- `packages/contracts/`、`packages/bankcore/`、`services/bank-backend/`、`services/gateway/`
- `services/agent/app/graph/state.py`、`ports.py`、`routing.py`、`errors.py`
- `services/agent/app/gateway/` 全部
- `services/agent/app/llm/` 全部
- `docker-compose.yml`、`scripts/keys.py`、`Makefile`、`pyproject.toml`、`.github/workflows/checks.yml`
- `AGENTS.md`、`PRD.md`、`docs/decisions/ADR-*`

**边界与禁止项：**

- 不读真实 `.env` / `.secrets` / `~/.claude/settings.json`。合成 token/key 测试。
- 不做全栈 make up/down/test-p1*/test-integration（只能协调者跑全栈）。
- 不删除/重建长期卷 `safebank_pg-agent` / `safebank_pg-bank`。
- 不 push、不 PR、不 merge。
- 不改 C1e 范畴（输出校验、卡片、回放）；不实现前端/BFF。
- 不改 CI 文件（`checks.yml`）。如需新 CI 目标 `make test-agent-db`，写入 Makefile 但不改 CI——协调者后续补 CI 行。
- 不调用真实 L2/L3（fake 边界标注清楚）。
- 不使用 `;` 或 `&&` 串联 shell 命令。

---

## Interfaces & fixed choices

### 1. HTTP 入口（三接口，PRD §6.5 锁定）

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/threads` | body `{thread_id}`；图按 `thread_id = session_id` 创建；已存在返回 200（幂等）。验证 caller JWT 中的 session_id 与 thread_id 一致。 |
| GET | `/threads/{thread_id}/state` | 返回 `{values: {messages}, next: [node_names], tasks: [{id, interrupts: [{value, id}]}]}`。从 checkpoint 读取 state。 |
| POST | `/threads/{thread_id}/runs/stream` | body `{input: {utterances: [{id,kind,text}]} | null, command: {resume: approval_id} | null}`。两者互斥（缺 input 或有 command）。SSE 流式返回。thread 不存在时自动创建。 |

**鉴权：** 所有三接口要求 `Authorization: Bearer <assistant_jwt>`。JWT 验证方式：用 Ed25519 公钥（`scripts/keys.py` 生成的 `jwt-public.pem`），验 `exp`、`nbf`（如果声明）。claim 中取 `sub`=customer_id、`sid`=session_id。代码中**不复制网关的 JWT 校验库**——只做 Agent 所需的最小验证：Ed25519 签名 + exp/sid。**A 服务令牌**用于调 L2，不用于 Agent 自己的 HTTP 鉴权。

### 2. Checkpoint Saver

- `AsyncPostgresSaver.from_conn_string(AGENT_DATABASE_URL)`（psycopg3 连接串）
- 启动时调用 `await checkpointer.setup()` 建表
- 连接参数：`autocommit=True`、`row_factory=dict_row`（按 langgraph-checkpoint-postgres 3.2.0 文档）
- graph 编译：`build_gateway_graph(ports, checkpointer, client, gateway_read_client=...)`——接口不变，只换 checkpointer 类型

### 3. Thread 运行锁

- `pg_try_advisory_lock(hashtext(thread_id))`——在 agent 库上执行
- 进入 `/threads/{thread_id}/runs/stream` 时获取，请求结束释放
- 拿不到锁返回 HTTP 409 `{"error": "RUN_IN_PROGRESS", "message": "..."}`
- 锁粒度为整个 HTTP 请求（SSE 连接）的生命周期

### 4. 新 input 与 resume 的区分

- 请求 body 含 `input` → `invoke_new_input(graph, utterances, config=..., context=...)`
- 请求 body 含 `command.resume` → `graph.invoke(Command(resume=approval_id), config=..., context=...)`
- 两者互斥，同时提供 → 400
- 调用前获取 advisory lock；调用后释放
- 旧 interrupt 替代验证（P0 实验 E7）：在有待处理 interrupt 的 thread 上发新 input，LangGraph 内建行为是从 START 重新开始、旧 interrupt 被丢弃。**实现时必须用真实 PG checkpoint 验证**这个行为，如不成立则由 ingest 显式清空。

### 5. Checkpoint 不含 Token 验证

- 完成一轮带 interrupt 的对话后，在 agent 库的 `checkpoints`、`checkpoint_writes`、`checkpoint_blobs` 三表中全文搜索 assistant JWT 与 A 服务令牌字符串 → 必须 0 条
- 测试在临时 PG 上运行，清理前执行搜索

### 6. 临时 PG 测试 Fixture

- 仿 `tests/gateway/db/conftest.py` 模式：
  - 随机端口、随机容器名（`sbg-agent-db-{pid}-{hex}`）
  - tmpfs `/var/lib/postgresql/data`（不持久化）
  - 测试结束 `docker rm -f`
  - 环境变量开关 `AGENT_DB_TESTS=1`
  - 使用 langgraph-checkpoint-postgres 自己的 `checkpointer.setup()` 建表，**不手写 DDL**
- 新增 `make test-agent-db` 目标：`AGENT_DB_TESTS=1 .venv/bin/pytest tests/agent/ -m "db" -q`
- 绝对不连接 long-term `safebank_pg-agent` 卷

### 7. 进程重启恢复

- 验证方式：同一进程内重建 graph（用相同 AGENT_DATABASE_URL 的 AsyncPostgresSaver）→ 调 `/threads/{thread_id}/state` 能看到之前的 interrupt
- 不做真实子进程重启（Docker kill + restart），那需要全栈，留给协调者

### 8. SSE 格式

```
event: updates
data: {"__interrupt__": [{"value": {"approval_id": "apv_..."}, "id": "..."}]}

event: messages
data: [{"type": "ai", "content": "..."}]

event: values
data: {"messages": [...], ...}

event: error
data: {"message": "..."}
```

用 `graph.astream()` 或 `graph.astream_events()`——以 PRD §6.5 的事件类型为准。`custom` 卡片事件留 C1e。

### 9. 新增 Makefile 目标

```makefile
test-agent-db:
	AGENT_DB_TESTS=1 .venv/bin/pytest tests/agent -m db -q
```

不改 CI（`checks.yml`），仅 Makefile。

---

## 依赖核实清单（协调者在派发前核实）

| 包 | 版本 | License | 用途 | 当前状态 |
|---|---|---|---|---|
| `langgraph-checkpoint-postgres` | 3.2.0 | MIT | AsyncPostgresSaver | 已在 poc deps |
| `psycopg[binary,pool]` | 3.3.3 | LGPL-3.0 | AsyncPostgresSaver 驱动 | 已在 poc deps |
| `langgraph` | 1.2.16 | MIT | StateGraph, interrupt, Command | 已在 poc deps |
| `httpx` | 0.28.1 | BSD | HTTP client（已有） | 已在 main deps |
| FastAPI / `starlette` | 间接依赖 | — | SSE 支持 | 需核实是否已在锁文件中，如果是 gateway 的传递依赖则无需新加；否则需 coordinator 批准并锁定 |

**仅当 starlette 不在锁文件中时需新加：** `starlette`（BSD，FastAPI 的依赖）。核实方法：`grep -r 'starlette' uv.lock`。如果已存在则不需要任何新依赖。

---

## Tasks（逐项，先红后绿）

### Task 0: 依赖与环境核实

- [ ] 读 `uv.lock` 确认 `langgraph-checkpoint-postgres==3.2.0`、`psycopg[binary,pool]==3.3.3` 存在
- [ ] 读 `uv.lock` 确认 starlette 存在（FastAPI 传递依赖）或记录为缺失
- [ ] 如果 starlette 缺失：写进 `docs/progress/C1d-agent-service.md` 待协调者裁定，不自行加依赖

### Task 1: 临时 PG fixture + checkpoint 建表

文件：`tests/agent/conftest.py`（新建）

- [ ] 红测：写 `test_checkpointer_setup_creates_tables`——启动临时 PG 容器 → 用 AsyncPostgresSaver.from_conn_string + setup() → 验证 `checkpoints`、`checkpoint_writes`、`checkpoint_blobs` 三表存在
- [ ] 红测：写 `test_repeat_setup_is_idempotent`——连调两次 setup()，第二次不报错
- [ ] 红测：写 `test_checkpoint_round_trip`——`graph.invoke(...)` 后 state 能从新 graph 实例恢复
- [ ] `docker run --rm -d --name sbg-agent-db-... --tmpfs /var/lib/postgresql/data:...` 临时 PG，session scope，`docker rm -f` 清理
- [ ] 环境变量 `AGENT_DB_TESTS=1` 控制 skip
- [ ] 跑 `AGENT_DB_TESTS=1 .venv/bin/pytest tests/agent/test_checkpoint_isolation.py -q`，红。
- [ ] 实现 fixture。
- [ ] 跑绿。小提交。

### Task 2: AsyncPostgresSaver 集成 + checkpoint 不含 token

文件：`services/agent/app/main.py`（新建）、`tests/agent/test_checkpoint_isolation.py`

- [ ] 红测：写 `test_checkpoint_has_no_token_after_full_round`——在临时 PG 上跑完整一轮 ALLOW → execute（fake gateway），之后直接 psycopg3 查 `checkpoints`/`checkpoint_writes`/`checkpoint_blobs` 三表，全文搜索 `"tok_` / assistant JWT 值 / A 服务令牌值 → 确认 0 行
- [ ] 红测：写 `test_thread_isolation`——两个不同 thread_id 的 state 互不可见
- [ ] 构建带 AsyncPostgresSaver 的 graph（在 main.py 的工厂函数中）
- [ ] 跑绿。小提交。

### Task 3: Thread 运行锁

文件：`services/agent/app/main.py`、`tests/agent/test_lock.py`（新建）

- [ ] 红测：写 `test_concurrent_same_thread_rejected`——同一 thread_id 并发两个 POST `/threads/{tid}/runs/stream`，第二个收到 409 RUN_IN_PROGRESS
- [ ] 红测：写 `test_different_threads_ok`——不同 thread_id 可以并发
- [ ] 红测：写 `test_lock_released_after_completion`——一轮完成后，同一 thread_id 可以再发起新请求
- [ ] 实现：在 runs/stream handler 获取 `pg_try_advisory_lock(hashtext(thread_id))`，用 FastAPI 的 `BackgroundTasks` 或 `try/finally` 确保释放
- [ ] 注意：`pg_try_advisory_lock` 必须用 psycopg3 的原生连接（不是 SQLAlchemy），因为 agent 库没有 SQLAlchemy engine。直接用 `psycopg.connect(AGENT_DATABASE_URL)` 获取锁后释放连接
- [ ] 跑绿。小提交。

### Task 4: P0 实验 E7——旧 interrupt 替代

文件：`tests/agent/test_lock.py` 或新增 `tests/agent/test_interrupt_replacement.py`

- [ ] 红测：写 `test_new_input_on_pending_interrupt_starts_from_beginning`——在临时 PG 上跑一轮到 REQUIRE_APPROVAL → interrupt，确认 state 有 `__interrupt__`。然后对同一 thread 发新 input（不同 utterance）→ 确认在新 state 中没有旧 interrupt，且旧 `approval_id` 不在 state 中
- [ ] 红测：写 `test_old_resume_does_not_execute_after_new_input`——在 interrupt 后发新 input，然后用旧 approval_id resume → 确认不触发 execute（approval_id 为空）
- [ ] 如果 LangGraph 原生行为已满足（新 input → START），红测直接绿——**必须用真实 PG checkpoint 验证**，不能只在内存 saver 上跑
- [ ] 如果原生行为不满足，由 ingest 显式清空——在 ingest 检测到 state 有 `approval_id` 且当前是 new input 路径时，设 `approval_id: None`
- [ ] 跑绿。小提交。

### Task 5: HTTP/SSE 入口

文件：`services/agent/app/main.py`、`tests/agent/test_service.py`（新建）

- [ ] 红测：写三接口的基本 HTTP 测试（FastAPI TestClient）：
  - `test_create_thread_returns_200`
  - `test_get_state_returns_messages_and_tasks`
  - `test_stream_runs_returns_sse`——用 `httpx.stream` 消费 SSE，验证收到 `updates`、`messages` 等事件
  - `test_stream_with_command_resume`
  - `test_input_and_command_mutually_exclusive`
  - `test_missing_auth_returns_401`
- [ ] 实现 FastAPI app：三路由 + JWT 鉴权依赖
- [ ] JWT 校验：加载 `JWT_PUBLIC_KEY_PATH`（默认 `.secrets/jwt-public.pem`，可配环境变量）→ Ed25519 验签 → 解析 `sub`、`sid`、`exp`
- [ ] AgentContext 构造：`customer_id = sub`、`session_id = sid`、`assistant_token = raw_jwt_string`（传给 gateway client）
- [ ] 跑绿。小提交。

### Task 6: 进程重启恢复

文件：`tests/agent/test_service.py`

- [ ] 红测：写 `test_interrupt_survives_graph_rebuild`——在临时 PG 上跑一轮到 interrupt → 销毁 FastAPI app（graph 对象） → 重建新 FastAPI app（新的 AsyncPostgresSaver，同一连接串）→ GET `/threads/{tid}/state` 能看到 interrupt tasks
- [ ] 红测：写 `test_resume_works_after_rebuild`——重建后 POST `/threads/{tid}/runs/stream` with `command: {resume: approval_id}` → 正常 continue
- [ ] 实现已在 factory 函数中——只需确保 graph 不是模块级全局、每次 app 启动时重新构造。测试验证恢复即可。
- [ ] 跑绿。小提交。

### Task 7: 最终门禁 + 文档

- [ ] `make setup && make lint` 通过
- [ ] `.venv/bin/pytest tests/agent -q`（所有 agent 测试，含旧 + 新增）零 skip/xfail
- [ ] `make test-agent-db` 通过
- [ ] `make test` 通过
- [ ] `make contracts-check` 通过
- [ ] 更新 `services/agent/README.md`：启动方式、环境变量、真实的 PG 使用说明、fake 边界
- [ ] 写 `docs/progress/C1d-agent-service.md`：红绿记录、每 task 的实际命令与结果、未覆盖项
- [ ] 本地提交（含 `Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>`），冻结 SHA
- [ ] 停止，待协调者亲验 + 独立审

---

## Review Focus（独立评审时重点核对）

1. **checkpoint 不含 token**：三表全文搜索为 0，无论哪种决策路径（ALLOW/DENY/REQUIRE_APPROVAL/REPLAN）都跑过
2. **线程锁**：同 thread_id 并发 409，不同 thread_id 并发 OK，锁在请求结束后释放
3. **旧 interrupt 替代**：PG checkpoint 上验证新 input 丢弃旧 interrupt
4. **重启恢复**：同一 PG 重建 graph 后 state 与 interrupt 完好
5. **HTTP 鉴权**：缺 token/错签名/过期/错 sid 均被拒绝
6. **input vs resume 互斥**：同时提供 → 400
7. **SSE 格式**：事件类型与 PRD §6.5 一致
8. **AGENT_DATABASE_URL 不泄露**：不打印/日志/响应中不含数据库凭据
9. **临时 PG 隔离**：使用 `AGENT_DB_TESTS=1` 开关，默认 skip；测试后容器被销毁
10. **不改冻结区**：graph/ports/routing/state/contracts/bankcore/L2/L3 零 diff

---

## 未覆盖（本组件不做）

- 全栈 make up/test-p1*/test-integration（协调者跑）
- 真实 postgres-agent 容器（只测临时 PG）
- C1e（输出校验、卡片、回放）
- 前端/BFF
- CI 文件 `.github/workflows/checks.yml`（协调者补 `make test-agent-db` 行）
- `make demo`（仍如实 exit 2）
- 真实 L2 G4/G8 联调（fake 标注）
- 健康探针替换（当前 agent 容器用探针脚本，HTTP 入口接上后由协调者更新 compose healthcheck）
## 协调者裁定（2026-10-24，与上文冲突时以本节为准）

- **D1 入口**（Q1/Q7）：`scripts/agent_entrypoint.py` 属于 agent 自有文件，允许修改，用来启动真实 agent 服务（uvicorn + FastAPI）。`docker-compose.yml` 保持冻结。如果服务确实需要新的环境变量或挂载，写进进度文件的「待决」，由协调者在全栈阶段串行添加，不得自行修改 compose。
- **D2 FastAPI / uvicorn**（Q2）：二者已在主依赖 `[project.dependencies]` 中（fastapi==0.161.1、uvicorn==0.54.0），无需新增。
- **D3 运行时依赖（新增裁定）**：当前 `requirements.txt`（运行镜像）**不含** langgraph、langchain、langgraph-checkpoint-postgres、psycopg，它们只在 `poc` 依赖组里，所以容器内的 agent 服务跑不起来。授权本组件把 agent 运行所需的包从 `poc` 组移到主依赖：**版本保持 pyproject/uv.lock 中现有的精确版本不变，不引入任何新包**。然后按仓库既有做法重新生成 `uv.lock`（只能是移组带来的变化）、`requirements.txt` 和 `.wheels`（`make wheels`），并在 `docs/third-party.md` 核对、补全这些包的 license。进度文件里要列出移动的包清单，以及 `requirements.txt` 的 diff。gateway / bank-backend 的行为不得改变。
- **D4 JWT**（Q4）：L1 **不验证、不解析** assistant JWT 来做任何授权判断，只把它从 runtime context 原样转发给 L2。claim 的定义见 PRD §7.3：aud=safebank-assistant，scope=[assistant]，sid，utts，10 分钟有效。测试里需要合成令牌时，只能用测试密钥，可以只读参考 `services/gateway/app/auth/`。checkpoint 与 AgentState 中不得出现任何令牌。
- **D5 CI**（Q5）：本组件新增的 `make test-agent-db`（tmpfs 临时 PG，零 skip）要在 `.github/workflows/checks.yml` 的 p1-postgres job 里追加一行运行它，并通过 actionlint。
- **D6 /health**（Q6）：保留 `/health`，现有 compose 健康检查要继续通过。
- **D7 async**（Q3）：先在 Task 0 核实 `graph.ainvoke`/`astream` 和 `AsyncPostgresSaver` 的真实 API（读已安装包的源码），能原生 async 就用原生，不要无谓地套 `asyncio.to_thread`。
- **D8 范围**：真实 PG 测试只能用 tmpfs 临时容器，**禁止**连接长期的 postgres-agent 卷。重启恢复必须至少有一项是「新进程或全新 saver/graph 实例，从同一个 PG checkpoint 恢复」，不能只在同一个对象上做 resume。`make up` 全栈验证由协调者执行。
