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

---

## 第三轮（复审阻塞项修复）

第二轮复审结论：P0-3 工具注册表、N1（公共路由鉴权）、N2/N2b（状态迁移与交叉接口占位）、N3–N6（bank_state / 审计时区 / 幂等进行中行 / 结果类型所有权）必须补。本轮全部修复，探针 A–D 转成正式测试。

| 项 | 处理 | 证据 |
|---|---|---|
| P0-3 工具注册表 | 新建 `services/gateway/policies/tools.yaml` 与 `app/policy/registry.py`：`ToolPolicy`（base_risk/sensitive_params/allowed_origins/outflow/irreversible/otp_required/title_template）+ `action_kind`（transfer 本人/对外、卡类 LOOSEN/TIGHTEN）+ `load_policies`（校验工具集 == 14 个 WRITE_ARGS）。README 写明 W2 只改值不改结构 | `tests/gateway/test_policy.py`（14 工具、风险/敏感参数/OTP、action_kind 转移与卡方向） |
| N1 公共路由鉴权 | `build_public_app` 加 `FastAPI(dependencies=[Depends(caller_dependency(env, public_key))])`，`caller_dependency` 写 `request.state.caller`，public_key 透传 | 探针 A 转 `test_routing.py::test_public_routes_require_auth_probe_a`（无凭证 401） |
| N2 状态迁移 | `transition` 先按 `TRANSITIONS[table.name]` 校验每条边，非法边抛 `ValueError`；补 CONFIRMED→PENDING_APPROVAL/DENIED/REPLAN；新增 `execution_jobs` 迁移表；注释写明 EXECUTING 在复演之后写入 | `db/test_state.py::test_illegal_transition_rejected` |
| N2b 交叉接口占位 | 新增 `approval/service.py`（create_for_evaluation，W4）、`approval/planview.py`（build_view，W4）、`evaluation/repo.py`（append_evaluation，W3）、`locks.py`（customer_lock，W4）、`execution/replay.py`（replay，W3 实现 W4 调用），签名+NotImplementedError+owner docstring | `test_probes.py::test_cross_interface_placeholders_exist_probe_d` |
| N3 bank_state | `BankState.model_validate(response)`（client 返回 dict），快照带 `plan_draft.steps` | 探针 B 转 `test_probes.py::test_bank_state_returns_and_caches_probe_b`（返回 BankState + 只调一次 snapshot） |
| N4 审计时区 | `_event` 统一 `astimezone(UTC).isoformat(timespec="microseconds")`，拒绝 naive | 探针 C 转 `db/test_audit_chain.py::test_verify_accepts_non_utc_clock_probe_c`（+08:00 不误报） |
| N5 幂等进行中行 | `EXECUTION_IN_PROGRESS` 替代 `RUN_IN_PROGRESS`；produce 抛异常时删除进行中行；docstring 规定同事务、不放 HTTP 调用 | `db/test_idempotency.py::test_produce_exception_leaves_id_retryable` |
| N6 结果类型所有权 | `ResolvedStep` + `ctx.steps`（② 写）；各结果 dataclass 标注 owner 可加字段不可删改；README 记录 | `context.py` 注释 + README |
| P1-13 时区 | 见 N4 | 同上 |
| N7 fake L3（P2 顺手修） | execute 需先 prepare（否则 409）；business_fail 保持 intent PREPARED；同 plan 不同 plan_hash 重复 prepare → 409 IDEMPOTENCY_CONFLICT；`_current_behavior/_pending_drop` 的并发串扰留给 W4 | `test_l3.py` 更新 prepare-first |
| N8 schema | `AppFormPlanRequest.form_action_ids` 1..5；`ExecutionAccepted.status` Literal["UNKNOWN"] | `schemas/requests.py` / `execution.py` |
| N9 序列默认权限 | 0001 加 `ALTER DEFAULT PRIVILEGES … ON SEQUENCES`；gateway_admin LOGIN 留给 W5（见 README 待决） | `0001_gateway.py` |

## 第三轮退出命令实测

| 命令 | 结果 |
|---|---|
| `make lint` | 通过（145 文件） |
| `make test-gateway` | 59 passed |
| `make test-gateway-db` | 17 passed |
| `make test` | 177 passed, 48 skipped |
| `make up`（现有卷，未删卷） | 全部 6 服务 healthy（gateway 重建为第三轮镜像，`Existing dataset preserved`） |
| `make test-p1` | 33 passed |

本轮未删除、重建或 `docker volume rm` 任何数据卷；`make up` 复用第二轮的 `safebank_pg-bank`/`safebank_pg-agent` 卷与 `.secrets`。

---

## 第四轮（开工前最后修补）

第三轮定点复核结论「有条件通过」，条件为 R1/R2/R3 三处；本轮按任务书只修 R1–R4，另完成 R4 的 outflow 口径修正。

| 项 | 处理 | 证据 |
|---|---|---|
| R1 JWT 公钥加载 | `Settings` 加 `jwt_public_key_path`（默认 `/run/secrets/jwt-public.pem`）；`main.load_public_key()` 读不到/为空拒绝启动；`serve()` 加载后传给两个 app | `test_routing.py::test_load_public_key_missing_refuses_r1`（缺公钥 raise）、`test_valid_jwt_with_public_key_passes_auth_r1`（合法 JWT 通过鉴权进入 503 占位） |
| R2 pyyaml 进主依赖 | `pyyaml==6.0.3` 从 dev 移到 `[project] dependencies`，`uv lock` + `uv export` 重导 `requirements*.txt`，`make wheels` 更新；third-party.md 加注 | `requirements.txt` 含 pyyaml（`# via safebank-gateway`）；`docker run safebank-p0:local python -c "import yaml"` → `6.0.3` |
| R3 action_kind fail-closed | 卡类工具在影子状态找不到卡时抛 `LookupError`（不再默认 TIGHTEN） | `test_policy.py::test_action_kind_card_absent_fails_closed_r3`（卡不存在两种工具都抛错）、`test_action_kind_huge_limit_is_loosen_when_card_present_r3`（卡存在 + 极大限额 → LOOSEN） |
| R4 outflow 口径 | 新增 `registry.outflow(tool, args, shadow_state)`：仅 TRANSFER_EXTERNAL 与目标为他人的 schedule_transfer 计对外转出，本人互转/申购/赎回不计；`tools.yaml` 中 transfer/schedule_transfer/subscribe_product 的静态 `outflow` 改 false | `test_policy.py::test_outflow_matches_external_transfer_and_schedule_r4`（三类六断言） |

## 第四轮退出命令实测

| 命令 | 结果 |
|---|---|
| `make lint` | 通过（145 文件） |
| `make test-gateway` | 64 passed |
| `make test-gateway-db` | 17 passed |
| `make test` | 182 passed, 48 skipped |
| `make up`（现有卷，未删卷） | 全部 6 服务 healthy |
| 镜像内 `import yaml` | `6.0.3` |
| `make test-p1` | 33 passed |

本轮未删除、重建或 `docker volume rm` 任何数据卷；`make up` 复用既有 `safebank_pg-bank`/`safebank_pg-agent` 卷与 `.secrets`。
