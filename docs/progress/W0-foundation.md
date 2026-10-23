# W0 进度（第二轮）：按架构评审修订后的 L2 Gateway 基础骨架

分支 `feat/l2-foundation`。执行者：Claude Code（DeepSeek V4 Pro）。第一轮提交 378ba67，第二轮在评审报告 `docs/reviews/L2-W0-architecture.md` 基础上逐条修订 P0-1–P0-7、P1-8–P1-14 与指挥决定 1–7。正式 PRD 仍为 v2.1，本文只记录 W0 骨架；PRD 修订由指挥做。

## 退出命令实测结果

| 命令 | 结果 |
|---|---|
| `make lint` | 通过（ruff check + format --check，133 文件） |
| `make test-gateway` | 52 passed |
| `make test-gateway-db` | 14 passed（临时 Postgres 16 → 迁移 upgrade head 两次 → 断言 → 销毁） |
| `make test` | 170 passed, 45 skipped（P1 116 单测未破坏；DB 项按无环境 skip） |
| `make up` | 全部 6 服务 healthy（含 gateway 新入口与 gateway 迁移） |
| `make test-p1` | 33 passed（真实 PG 全绿） |

## 评审问题逐条处理

### P0（阻塞项，全部完成）

- **P0-1 EvalContext 阶段间传数据**：`pipeline/context.py` 重写为每阶段一个类型化结果槽——`identity`(Caller)、`authorization`(AuthorizationInputs)、`provenance`、`intent`、`snapshot`、`preflight`、`workflow`、`decision`，加 `mode`/`profile`/`wall_now`/`conn`/`l3`/`stage_status`。规则：每阶段只写自己的槽、只读前面的槽；改 `context.py` 只能由指挥。
- **P0-2 短路与 profile**：`Stage` 增加 `always_run`（⑧⑨=True）与 `enabled(profile)`；DENY 只短路 ⑤⑥⑦；E 类错误 `raise GatewayError`（已从 `FindingEffect` 删掉 ERROR）；`evaluate(ctx, mode=...)` 支持 EVALUATE/EXECUTE_REPLAY/SHADOW_B1。测试断言 DENY 后顺序为 contract→entity→provenance→intent→decision→audit，B0 关闭 provenance/intent/preflight/workflow。
- **P0-3 对外数据模型与策略注册表**：新建 `app/schemas/`（Decision、FindingView、ReplanHint、PlanView、UtteranceRef、NormalizedScope、AuthorizationInputs、ReadEnvelope、AppFormPlanRequest、ExecutionAccepted，以及 PlanStatus/ApprovalStatus/ParseStatus/ExecutionJobStatus/WorkflowEventStatus/FindingEffect/ActionKind 等 StrEnum）。`models.py` 改名 `tables.py`，CHECK 约束由 `schemas.enums` 生成。Finding 的 effect 枚举改名 `FindingEffect`。`policies/tools.yaml` 与 `app/policy/registry.py` 留待 W2 按结构补规则（P2，见下）。
- **P0-4 路由与授权矩阵**：新建 `app/api/{auth,sessions,tools,plans,execution,approvals,notify_audit,demo}.py` 与 `app/internal/scheduled.py`（占位 503），`main.py` 只 include 一次。矩阵改为 `(method, path_format)` 键 + 多 CallerSpec 元组，一次性登记 G1–G22a + S1 + D0–D12；`lookup` 用 `{param}` 正则匹配；`resolve_caller` 解析 `Caller{kind,customer_id,sid,utts,jti,scopes}` 并推导 origin（A→agent、F→app_form）；`coverage_errors` 双向断言（路由⊆矩阵、矩阵有路由、public 无 `/internal/*`、internal 只有 `/internal/*`、D 路由按模式），`build_*_app` 内联启动断言。
- **P0-5 交叉接口占位**：`app/state.py` 为 plans/approvals/utterances 提供合法迁移表 + `transition(conn, table, id, from_set, to)`（0 行即 409），是改状态唯一入口。G4 的「创建审批/算 view_hash」与 W4 的「执行前复演」按指挥决定 #1 留接口占位（见「给 W1–W4」），不实现业务；W3/W4 可并行。
- **P0-6 波次 1 表与字段**：全部补进 `0001`——`request_dedup`（持久幂等）、`pin_failures`/`ip_ratelimit`/`auth_sessions`、`sessions.authorization_epoch`、`utterances.parse_status/clarification_id`、`form_actions.kind/action_kind/normalized_fields/parent_utterance_id/created_at_wall`、`hints.label`、`evaluations.equivalent_to/clock_version` + `UNIQUE(plan_id,revision)`、`otp_challenges.view_hash/last_sent_at_wall`、`execution_jobs.created_at_wall/updated_at_wall/deadline_wall`、`audit_events.plan_id` + 索引，以及 P2-16 的索引/外键。新增防漂移测试：`test_ddl_drift.py`（冻结 `schema_v1.json` == 从 `tables.py` 渲染的 DDL，表拓扑序 + 索引排序）与 `db/test_drift.py`（alembic autogenerate compare_metadata 为空）。
- **P0-7 运行镜像依赖**：`uv export` 重导 `requirements.txt`/`requirements-test.txt`，`make wheels` 下载 PyJWT/argon2-cffi/pypinyin 等（44 个锁定 wheel），gateway 容器可起（`make up` 实测 healthy）。

### P1（全部完成）

- **P1-8 fake L3 有状态**：重写 `app/l3/fake.py`——自定义 `httpx.AsyncBaseTransport` 包住校验 app，`timeout`/`drop`/`commit_then_drop`/`prepare_drop` 抛真实 `ReadTimeout`/`RemoteProtocolError`；按 `(customer_id, plan_id)` 维护 intent 状态机（PREPARED/COMMITTED/CANCELLED 墓碑）；行为支持按调用次数设定；`set_snapshot`/`set_result`；校验 `X-Service-Token` 与 `GET executions` 的 `customer_id`；`calls` 记录供断言。端到端测试：fake + 真实 `L3Client` 得到 `L3OutcomeUnknown`、commit_then_drop 后查询回 COMMITTED。
- **P1-9 L3 客户端异常分类**：`L3NotDelivered`（ConnectTimeout/PoolTimeout/ConnectError）、`L3OutcomeUnknown`（ReadTimeout/WriteTimeout/RemoteProtocolError）、`L3BusinessError(code,status,details)`；非 2xx 解析错误体；超时从构造参数注入（默认 3/10/5）。
- **P1-10 统一错误覆盖框架错误**：404/405 → `NOT_FOUND` 404；未捕获异常经 FastAPI `Exception` 处理器 → 503 `DEPENDENCY_UNAVAILABLE`（FastAPI 将其注册为 ServerErrorMiddleware handler，生产 503 并记录日志；测试用原始 ASGI 捕获响应）。
- **P1-11 JWT 严格校验**：`aud` 必须是字符串且等于期望值（数组 aud 拒绝，PyJWT 传 `audience` 后手动再校验）；`scope`/`utts` 必须是 `list[str]`；`sid` 必须是字符串。反例测试齐全（数组 aud、字符串 scope/utts、app/assistant 互不通用）。
- **P1-12 库所有权与迁移接线**：新增 `scripts/gateway_database.py`（`ALTER DATABASE gateway OWNER TO postgres`、建 `gateway_admin`、写 `gateway-migration.env`），`docker-compose.yml` 加 `gateway-maintenance` profile，`Makefile` 加 `gateway-provision`/`migrate-gateway` 并接进 `up`。迁移增加 `ALTER DEFAULT PRIVILEGES`（gateway_app 默认 DML、gateway_admin 默认 SELECT/TRUNCATE）。DB 测试补 `gateway_app` 不能 `DROP TABLE audit_events`。
- **P1-13 审计哈希覆盖时间戳 + 小事务**：hash 覆盖 `created_at_wall`（RFC3339，注入时钟）；新增 `append_tx` 自开小事务；README 写明不得在含 HTTP 调用的事务内 append。测试覆盖时间戳篡改检出。
- **P1-14 阶段归属**：按指挥决定 #3 统一——①②③④→W2，⑤⑥⑦⑧⑨+G4→W3，W1 原话/scope，W4 审批/执行；② 用 `ctx.bank_state()`（惰性缓存）核对所有权，⑤ 只钉版本。

### P2（只做小而减冲突的）

- P2-15：启动校验 F/A/S/D 令牌互不相同（config）。路由遍历断言已由 P0-4 的 `coverage_errors` 覆盖。
- P2-16：索引/外键已并入 0001。
- P2-17：契约错误 details 返回字段路径与错误类型（不回显值）。
- P2-18（Settings 收拢密钥）、P0-3 的 `policies/tools.yaml`+`app/policy/registry.py`：留待 W2，结构由 W2 起，写进各自进度「待决」若需改。

## 待决（给指挥）

1. `BANK_BACKEND_URL`、`ExecutionJobStatus`（EXECUTING/UNKNOWN/EXECUTED/FAILED/CANCELLED）、`WorkflowEventStatus`（PENDING/COMMITTED/BLOCKED）需补记 PRD §11.1 / §7.2。
2. `make up` 在本 worktree 生成了新 `.secrets` 并重建了 `safebank_pg-bank`/`safebank_pg-agent` 卷（旧 P1 卷按任务书删卷重建）；主 checkout 的旧 `.secrets` 与新卷不匹配，集成分支重新 `make keys && make up` 即可。
3. `policies/`（tools.yaml 等）与 `app/policy/registry.py` 尚未建——W2 起结构并只补规则值；W1/W3/W4 若需要 ActionKind/基础等级，先用 `schemas.enums.ActionKind` 与 W2 约定，避免重复造轮子。

## 给 W1–W4 的最终接口约定

（路径见 README「目录结构 / 分工与共享规则」，此处列关键接口与所有权）

- 所有任务：不改 `pipeline/__init__.py`、`pipeline/context.py`、`schemas/**`、`auth/matrix.py`、`state.py`、`tables.py`、`migrations/**`、`main.py`、`pyproject.toml`/`uv.lock`、compose。需要改就写「待决」。状态只能 `state.transition`；request_id 只能 `idempotency.request_idempotent`；错误只能 `GatewayError(Code.X)`；L3 只能 `L3Client`（测试用 `FakeL3().transport()`）；审计只能 `audit.append_tx`。测试放 `tests/gateway/<任务名>/`，Makefile 只末尾追加 `test-*`。
- W1：可改 `auth/tokens.py`（签发/续签/撤销）、`auth/pin.py`、`api/auth.py`、`api/sessions.py`、`utterance/**`、`policies/intent_lexicon.yaml`。产出 `schemas.NormalizedScope`/`UtteranceRef`，写 `utterances`/`pending_clarifications`；提供 `utterance.loader.load_for_plan(conn, sid, ids, caller) -> AuthorizationInputs` 供编排器填 `ctx.authorization`；矩阵已登记全表，W1 只补 `test_auth_matrix.py`。
- W2：可改 `api/tools.py`、`pipeline/{entity,provenance,intent}.py`、`taint/**`、`policies/{trust,injection_patterns,tools}.yaml`（tools.yaml 只补值不改结构）、`tests/gateway/w2/`。阶段只写 `ctx.provenance`/`ctx.intent` 与 findings；W1 合入前用 `AuthorizationInputs` fixture。
- W3：可改 `api/plans.py`、`pipeline/{contract,snapshot,preflight,workflow,decision,audit}.py`、`evaluation/repo.py`、`workflow/**`、`policies/{thresholds,workflow}.yaml`。预演只调 bankcore；REQUIRE_APPROVAL 时调 W4 的 `approval.service.create_for_evaluation`（W4 未合入前是占位）；实现 profile 开关与 SHADOW_B1。
- W4：可改 `api/{approvals,execution}.py`、`approval/**`（PlanView/view_hash/binding_mac/OTP）、`execution/**`、`locks.py`。复演调 `pipeline.evaluate(ctx, mode=EXECUTE_REPLAY)`，结果用 W3 的 `append_evaluation` 落库；`locks.customer_lock` 必须能被 W5 复用。
- W5（波次 2）：S1/通知/G11/G16/D 接口在已有占位路由模块上实现，复用 `locks`、`state`、`audit`。
