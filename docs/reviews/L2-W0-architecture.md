# L2 W0 基础骨架架构评审

- 评审对象：`feat/l2-foundation`（378ba67）相对 `feat/l2-gateway` 的 diff，共 50 个文件，+2952 行
- 评审人：只读评审 subagent（Claude），2026-10-23
- 依据：AGENTS.md §2；docs/plans/L2-plan.md §0、§3、§4；docs/plans/briefs/W0-foundation.md；docs/progress/W0-foundation.md；PRD §3.3、§7.1–§7.3、§7.8 末段、§7.9、§7.10、§7.12、§9.1、§9.5、§9.5a、§9.8、§10.1、§11.1

## 实际运行的验证

| 命令 / 探针 | 结果 |
|---|---|
| `make test-gateway` | 40 passed |
| `make test-gateway-db` | 4 passed（临时 Postgres 16，迁移跑两次） |
| `make lint` | 通过 |
| 探针：fake L3 设为 `timeout`（delay 0.5s），经真实 `L3Client` 调用，并把 `TIMEOUT_DEFAULT` 调成 0.1s | **返回 200**，用时 0.58s，没有抛 `L3Timeout`（ASGITransport 不执行超时） |
| 探针：fake L3 设为 `drop`，经 `L3Client` 调用 | 抛出裸的 `DropConnection(BaseException)`，**不是** `L3Error` |
| 探针：不带 `X-Service-Token` 调 fake L3 `/internal/snapshot` | 200（fake 不校验令牌） |
| 探针：不带 `customer_id` 调 fake `GET /internal/executions/{id}` | 200 COMMITTED（真实 L3 要求 `customer_id`） |
| 探针：public_app `GET /nope`、`POST /health` | `{"detail":"Not Found"}`、`{"detail":"Method Not Allowed"}`，不是统一错误体 |
| 探针：签发 `aud=["gateway-app","safebank-assistant"]`、`scope="app"` 的 JWT，用 `verify_jwt(expected_aud="safebank-assistant")` 校验 | **通过**，得到 `scope=('a','p','p')` |
| `grep pyjwt\|argon2\|pypinyin requirements.txt` | 没有匹配：运行镜像里没有 PyJWT |

## 结论

**目前还不能作为 W1–W4 四路并行的稳定基础。** 已经做对的部分：双端口拆分、启动护栏、审计链的加锁方式、角色隔离、临时数据库测试设施，以及「每个阶段一个文件」的思路，这些都可以保留。但并行开发会碰到的「共享接触面」还没有定义：阶段之间怎么传数据、gateway 对外的数据模型、路由与授权矩阵怎么登记、跨任务共用的状态机与服务接口、波次 1 需要的表与字段。按现状开工，W1–W4 一定会同时修改 `context.py`、`main.py`、`matrix.py` 和迁移文件，也会各自发明 Decision、scope、幂等的格式。这正是 L2-plan §0 要避免的架构漂移。

**阻塞项是下面的 P0-1 到 P0-7。** 都修完之后，可以作为并行基础。如果 P0-5（W3 与 W4 的交叉接口）不想在 W0 里做，就把 W4 改为在 W3 的 G4 合入后再开工，也就是 W3、W4 串行，W1、W2 仍然并行。

---

## 问题列表

### P0：阻塞并行开工

**P0-1 `EvalContext` 没有阶段间传数据的位置，W2 和 W3 会同时修改 `context.py`**
- 位置：`services/gateway/app/pipeline/context.py:49-68`
- 问题：现在只有 request、caller、origin、customer_id、session_id、plan_draft、plan_id、snapshot、findings、audit 几个字段。§7.2 ①–⑨ 与 §7.9、§7.10 还需要以下数据，都没有位置：
  - 已加载的原话及其 `normalized_scope`、`scope_ids`、assistant 令牌的 `utts`；
  - 已解析的步骤（`resolved_steps`），以及每步的 ActionKind、基础等级、outflow 标记；
  - 每个（步骤，参数）的来源与污点标签，以及引用到的 read_records；
  - 每个阶段的通过状态。hint 只能在每一步都通过 ③④ 时生成，决策阶段需要知道这一点；
  - 预演结果（bankcore `Simulation`）、versions、clock_version、sim_now；
  - 工作流历史与影子事件；
  - 输出：Decision、replan_hints、risk、policy_version、view_hash、shadow_b1；
  - 运行模式：G4 评估、G8 执行前在新快照上复演 ①–⑧（§7.10.4）、shadow B1；
  - 运行环境：profile、可注入的时钟（wall_now）、db 连接（执行路径必须用 `lock_conn`）、L3 客户端。
- 修改建议：W0 把这些位置一次性定好，每个都用有类型的子对象，并注明哪个阶段写、谁拥有。例如 `ctx.identity: Caller`（W1 的数据，由编排器填）、`ctx.authorization: AuthorizationInputs`（W1 的原话与 scope）、`ctx.provenance: ProvenanceResult | None`（只有 ③ 写）、`ctx.intent: IntentResult | None`（④）、`ctx.snapshot`（⑤）、`ctx.preflight`（⑥）、`ctx.workflow`（⑦）、`ctx.decision: Decision | None`（⑧）、`ctx.stage_status: dict[str, StageStatus]`、`ctx.mode`、`ctx.profile`、`ctx.clock`、`ctx.conn`、`ctx.l3`。规则：每个阶段只写自己的位置，只读前面阶段的位置。之后要改 `context.py`，只能由指挥统一改。

**P0-2 短路规则会让 DENY 没有 Decision；E 类错误与 profile 开关都没有落点**
- 位置：`services/gateway/app/pipeline/__init__.py:41-48`；`tests/gateway/test_pipeline.py` 断言 DENY 后只剩 `["contract","audit"]`
- 问题：
  1. DENY 之后 ⑧ 决策阶段也被跳过，DENY 的 Decision（code、findings、`replan_hints=[]`）没人组装。可 §9.1 规定 DENY 是 200 的 Decision。
  2. `Effect.ERROR` 存在，但编排器不处理它。① 契约失败按 §9.8 应该是 E422，不是 finding；现在照样会进入快照阶段。
  3. GATEWAY_PROFILE（B0、B1、各种消融，§10.1）要按阶段开关，shadow B1 要用另一套配置纯内存跑一遍，这两件事都没有挂接点。
- 修改建议：
  - 阶段增加 `always_run: bool`，⑧ 与 ⑨ 设为 True；DENY 只跳过 ⑤⑥⑦。
  - E 类错误一律 `raise GatewayError`，不写成 finding，并从 Effect 中删掉 ERROR。
  - 阶段增加 `enabled(profile) -> bool`，由编排器统一判断。
  - `evaluate(ctx, mode=...)` 支持 EVALUATE、EXECUTE_REPLAY、SHADOW_B1；SHADOW_B1 不写库、不审计。
  - 测试改为断言 DENY 后的运行顺序是 `[contract, …, decision, audit]`，并且 `ctx.decision.decision == "DENY"`。

**P0-3 gateway 对外的数据模型与策略注册表没有定义，W1–W4 会各自发明**
- 位置：缺失。`safebank_contracts` 里没有 Decision、Finding 的线上格式、PlanView、UtteranceRef、AppFormPlanRequest、ReadEnvelope，也没有 PRD §7.2 所说的 `contracts.status`；`models.py:42-64` 把状态枚举写死成元组。
- 问题：
  - **scope 格式**：W1 产出 `normalized_scope`，W2 的 ④ 要消费它，W3 生成的 hint scope 要能证明是父 scope 的收缩，W1 的 G19 要写 form_action 的 `normalized_fields`。四方共用一个格式，却没有类型定义。
  - **ActionKind 与基础等级**：任务书把 `policies/tools.yaml` 交给 W2，但 W1（G19 校验 `action_kind`、意图词表）、W3（基础风险、outflow、序列规则）、W4（PlanView 标题、不可逆标记）都依赖它。
  - **命名冲突**：Finding 的 `effect` 取值（`RISK_YELLOW` 等）将成为线上格式；它和 contracts 里 `plan.Effect`（账本效果）同名，容易混淆。
- 修改建议：W0 新建 `services/gateway/app/schemas/`，放 Pydantic v2 模型，按 PRD §9.5 逐字段写：Decision、FindingView、ReplanHint、PlanView、UtteranceRef、NormalizedScope、AppFormPlanRequest、ReadEnvelope、ExecutionAccepted（202 UNKNOWN），以及 PlanStatus、ApprovalStatus、ParseStatus、ExecutionJobStatus 等 StrEnum。`models.py` 的 CHECK 约束从这些枚举生成。同时新建 `policies/tools.yaml` 与 `app/policy/registry.py`，内容包括：14 个 W 工具对应的 ActionKind、基础等级、敏感参数、allowed_origins、outflow，加上加载函数。W2 之后只能补规则，不能改结构。另外把 `models.py` 改名为 `tables.py`（或 `db/tables.py`），避免和 L2-plan §4 约定的 `app/models/`（gateway 内部模型目录）冲突。Finding 的 effect 枚举改名为 `FindingEffect`。

**P0-4 路由与授权矩阵的格式无法让 W1–W4 各自登记，「默认拒绝」也没有落实**
- 位置：`services/gateway/app/main.py:22-50`；`auth/matrix.py:17-32`；`auth/deps.py:27-52`、`55-87`
- 问题：
  1. 路由直接写在 `main.py` 的 `build_*_app` 里，W1、W3、W4、W5 都得改这一个文件。
  2. `lookup(method, request.url.path)` 按字面路径查表，`/plans/pln_x/execute` 这种带参数的路径永远查不到。
  3. `CredentialSpec` 只能写一种凭证，但 G2、G4、G5、G8、G20、G21 允许「F+app **或** A+assistant」两种调用方（§7.1、§9.5）。
  4. `matrix_guard` 没有接到任何应用上；也没有检查「已注册路由 ⊆ 矩阵」。`/internal/health` 现在就不在矩阵里。
  5. `jwt_dependency` 返回结果时丢掉了 `utts`，而 G4 需要它判断 `utterance_ids ⊆ token.utts`。
  6. `matrix_guard` 读 `os.environ`，`service_token_dependency` 却接受注入的 env，两者不一致。
  7. D 路由「只在 DEMO/EVAL 注册」没有挂接点。
- 修改建议：
  - W0 预先建好路由模块，每个都先放返回 503 或 501 的占位实现，`main.py` 统一 include，之后不再改 `main.py`。建议分组：`api/auth.py`、`api/sessions.py`（W1：G1、G3、G14、G15、G18–G22a）；`api/tools.py`（W2：G2）；`api/plans.py`（W3：G4）；`api/execution.py`、`api/approvals.py`（W4：G5–G10、G17）；`api/notify_audit.py`（W5：G11–G13、G16）；`internal/scheduled.py`（W5：S1）；`api/demo.py`（W5：D 接口，按 settings 决定是否挂载）。
  - 矩阵按 `(method, route.path_format)` 作键，值是允许的调用方元组，例如 `(Caller("F", aud=APP, scopes={"app"}), Caller("A", aud=ASSISTANT, require_sid=True))`。由 W0 按 §9.5、§9.5a、§9.7 **一次性登记全表**，之后这张表只有指挥能改。
  - 写一个全局依赖：解析出 `Caller{kind, customer_id, sid, utts, jti, scopes}`，并由它推出 origin（A→agent，F→app_form）。
  - 加一个启动断言和对应测试：两个应用里每条路由都必须在矩阵里，矩阵里每条记录都必须有对应路由（D 路由按模式区分）。

**P0-5 跨任务共写的状态与交叉调用没有接口，W3 与 W4 实际上做不到并行**
- 位置：缺失（`models.py` 的 plans、approvals、utterances、evaluations 表）
- 问题：
  - `plans.status` 有三方写：W3 写 EVALUATED、REPLAN、DENIED、PENDING_APPROVAL；W4 写 CONFIRMED、EXECUTING、UNKNOWN、EXECUTED、FAILED；W1 在取消 epoch 时写 SUPERSEDED（§7.4）。
  - `approvals.status` 由 W1（G14 登出、取消词）和 W4 写。
  - `utterances.status` 由 W1 写 SUPERSEDED、CANCELLED，由 W4 写 CONSUMED。
  - `evaluations` 由 W3（G4）和 W4（执行前复演，revision+1，§7.10.5）写。
  - G4 返回 REQUIRE_APPROVAL 时，要当场创建 approval，计算 view_hash 与 binding_mac，这些是 W4 的逻辑；而 W4 的复演又要调用 W3 的流水线和评估落库。
  - 客户锁 `lock_conn`（§7.8）由 W4 实现，W5 的 S1 和 demo 入口也必须用同一把锁。
- 修改建议：W0 增加两类东西。
  - (a) `app/state.py`：plan、approval、utterance 各自的合法迁移表，以及 `transition(conn, table, id, from_set, to)`，用 `UPDATE … WHERE status = ANY(:from) RETURNING`，返回 0 行就抛 409。所有任务只能通过它改状态。
  - (b) 交叉服务的接口签名与占位实现：`approval/service.py: create_for_evaluation(conn, plan, evaluation, view) -> approval_id | None`、`approval/planview.py: build_view(ctx) -> (PlanView, view_hash)`，这两个归 W4；`evaluation/repo.py: append_evaluation(conn, ctx) -> EvaluationRow`，归 W3；`locks.py: customer_lock(customer_ids) -> lock_conn` 上下文管理器，归 W4。
  - 如果不做 (b)，就把 W4 排在 W3 合入之后。

**P0-6 波次 1 需要的表与字段缺失；迁移是线性的，并行补字段必然冲突**
- 位置：`services/gateway/app/models.py`、`migrations/versions/0001_gateway.py`、`migrations/schema_v1.json`
- 问题：
  1. **通用 request 幂等没有持久化**。G3、G6、G7、G8、G10、G13、G14、G15、G18、G19 都带 `request_id`，§9.1 要求「路由+主体作用域幂等」。但 `IdempotencyStore`（`middleware.py:85-120`）只存在进程内存里，重启就丢；并发进行中时还返回了 `RUN_IN_PROGRESS`，那是 L1 线程的错误码。
  2. **W1 需要、但表结构里没有的**：PIN 失败计数与 IP 频控（§7.3）；app 登录会话或 jti 撤销（G14 要撤销登录会话，`sessions` 表是聊天 sid）；`sessions.authorization_epoch`（取消词递增，§7.4）；`utterances` 缺 `parse_status`、`clarification_id`。
  3. **`form_actions`（129-139 行）** 缺 `kind`（FORM、CLARIFICATION）、`action_kind`、`normalized_fields`、`parent_utterance_id`、`created_at_wall`。**`hints`（141-151 行）** 缺 `label`（G22a）。
  4. **`evaluations`（175-190 行）** 缺 `equivalent_to`、`clock_version`，也缺 `UNIQUE(plan_id, revision)`。W3 和 W4 都会追加 revision，没有这个约束可能出现重复。
  5. **`otp_challenges`（210-220 行）** 缺 `view_hash`（OTP 要绑定 approval_id + view_hash，§7.10.3），也缺 `last_sent_at_wall`（30 秒重发间隔）。
  6. **`execution_jobs`（231-241 行）** 缺 `created_at_wall`、`updated_at_wall`、`deadline_wall`，恢复工作器扫描 UNKNOWN 时需要。
  7. **`audit_events`（304-323 行）** 缺 `plan_id` 列与索引，G11 `/plans/{id}/audit` 只能去扫 JSONB。
  8. **线性迁移冲突**：Alembic 是线性链，如果 W1–W4 各自加 `0002_*`，合并后会出现多个 head，`upgrade head` 直接失败。
  9. **DDL 与模型会漂移**：`schema_v1.json` 是冻结的 DDL，以后 `models.py` 改了，没有任何测试能发现两者已经不一致。
- 修改建议：
  - 0001 还没有合入，W0 直接在 0001 里补上以上全部内容，新增 `request_dedup(customer_id, route, request_id, request_hash, status_code, response, created_at_wall)`，主键为前三列，并配一个基于数据库的 `idempotent(conn, …)` 辅助函数。G4 继续用 `plans` 表上的唯一键。
  - 定下迁移规则：**波次 1 期间 worker 不写迁移**。需要改表就写进各自进度文件的「待决」，由指挥在集成分支上串行加 `0002_wave1`。
  - 增加 DB 测试：用 `alembic.autogenerate.compare_metadata` 断言迁移后的库和 `metadata` 没有差异。

**P0-7 运行镜像缺依赖，`make up` 和 CI 的 e2e job 会失败**
- 位置：`docker-compose.yml:64`（gateway 的 command 改成了 `services.gateway.app.main`）；`requirements.txt` 与 `.wheels` 没有重新生成
- 问题：`main.py` → `auth/deps.py` → `auth/tokens.py` 会 `import jwt`，但镜像里没有 PyJWT，gateway 容器启动就失败。`make up` 带 `--wait` 等健康检查，会卡在这里。`.github/workflows/checks.yml:24` 正好跑 `make up`、`make test-integration`，所以 W0 一合入，集成分支的 CI 就会变红，波次 1 的每个合入都过不了门禁。worker 待决第 2 条说「由指挥在集成分支做」，这样会让合入点本身就是坏的。
- 修改建议：在 W0 分支里完成 `uv export` → `requirements.txt`，再 `make wheels`。或者在依赖补齐之前，compose 暂时保留 `scripts.health_app`。合入前由指挥跑一次 `make up`。

### P1：开工前应修

**P1-8 fake L3 无法经真实客户端制造 W4 需要的故障**
- 位置：`services/gateway/app/l3/fake.py:66-75`、`148-169`
- 问题（都已用探针证实）：
  - `timeout` 在 ASGITransport 下只是延迟一下，然后照样返回 200；
  - `drop` 抛出的 `DropConnection(BaseException)` 绕过了 `L3Client` 的异常转换；
  - fake 没有状态：GET executions 永远返回 COMMITTED，reconcile 永远返回 CANCELLED，所以无法模拟「已提交但回包丢失」「prepare 丢包后 reconcile 写墓碑」「迟到的 execute」这几个 UNKNOWN 恢复的核心场景（§7.10.7）；
  - 不能按测试自定义 snapshot 和 ExecutionResult（W3 的预演需要真实账户；W4 需要 FAILED 加 LINKED_STEP_FAILED 的结果）；
  - STATE_CHANGED 的 `details` 是空的；不能返回 503；
  - 不校验 `X-Service-Token`；GET executions 不要求 `customer_id`。
- 修改建议：
  - 用自定义的 `httpx.AsyncBaseTransport` 包住 fake app，直接抛 `httpx.ReadTimeout` 或 `httpx.RemoteProtocolError`，这样走的是真实的客户端异常路径；
  - 按 `(customer_id, plan_id)` 维护 intent 状态机（PREPARED、COMMITTED、CANCELLED，墓碑不可复活）；
  - 行为增加 `commit_then_drop`、`prepare_drop`、`unavailable_503`、`business_fail`，并支持按调用次数设定行为（例如第 n 次返回 STATE_CHANGED）；
  - 提供 `set_snapshot(BankState)` 和 `set_result(...)`；
  - 加上令牌与查询参数校验，并记录每次调用的日志供断言；
  - 增加一条测试：fake + 真实 `L3Client` 端到端，确认能得到 `L3Timeout` 或 UNKNOWN。

**P1-9 L3 客户端没有区分「未送达」和「结果未知」，也不解析业务错误**
- 位置：`services/gateway/app/l3/client.py:59-69`、`71-113`
- 问题：
  - `ConnectTimeout` 和 `PoolTimeout`（请求肯定没发出去）被归为 `L3Timeout`，`RemoteProtocolError`（请求可能已经送达）被归为 `L3Unavailable`。可 execute 路径恰恰要靠这一区分，来决定是可以安全重试，还是必须进入 UNKNOWN（§7.8、§7.10.7）。
  - 方法直接返回原始 `httpx.Response`，W2、W3、W4、W5 会各自去解析错误体，解析出的 STATE_CHANGED、EXECUTION_CANCELLED、IDEMPOTENCY_CONFLICT 等也不一定一致。
  - 超时是模块常量，测试注入不了。
- 修改建议：异常分成 `L3NotDelivered`、`L3OutcomeUnknown`、`L3BusinessError(code, status, details)` 三类。请求与返回都用 contracts 里的模型（`Prepare`、`Execute`、`BankState`、`ExecutionStatus`）。超时从 Settings 注入，默认值保持 3、10、5 秒。

**P1-10 统一错误体没有覆盖框架错误**
- 位置：`services/gateway/app/middleware.py:57-79`
- 问题：404、405 返回的是 `{"detail":…}`（已证实），未捕获的异常返回纯文本 500。这不满足 §9.1，也会影响 W6 的 A8 测试按错误码断言。
- 修改建议：注册 `StarletteHTTPException` 处理器：404 和 405 都映射为 `NOT_FOUND` 404。405 也统一按 404 处理，不暴露这个路由存在、只是方法不对。再注册兜底的 `Exception` 处理器，返回 503 `DEPENDENCY_UNAVAILABLE`；如果需要新的错误码，写进待决，由指挥改 contracts。两个应用都要加，并补测试。

**P1-11 JWT 校验不够严格**
- 位置：`services/gateway/app/auth/tokens.py:70-91`
- 问题：PyJWT 会接受「aud 是数组且包含期望值」的令牌。已证实：同时带两个 aud 的令牌能当作 assistant 令牌通过，这打破了 app 令牌与 assistant 令牌的隔离（§7.3）。另外，`scope` 如果是字符串，会被拆成单个字符；`utts`、`scope` 的类型都没有校验。
- 修改建议：decode 之后，要求 `isinstance(aud, str)` 且等于期望值；`scope` 和 `utts` 必须是 `list[str]`；`sid` 必须是字符串。在 `test_auth.py` 里补上这几个反例。

**P1-12 gateway 库的所有权与迁移接线（worker 待决第 1 条）**
- 位置：`scripts/keys.py:52-58`（`CREATE DATABASE gateway OWNER gateway_app`）；`0001_gateway.py:27-34`
- 问题：
  - 运行角色是数据库 owner。在 PG16 中，public schema 归 `pg_database_owner` 所有，也就是归 gateway_app；schema 的 owner 可以 DROP 其中的表。所以 `REVOKE UPDATE, DELETE ON audit_events` 并不能真正保证只追加。
  - `make up` 从来没跑过 gateway 迁移，全栈环境里根本没有这些表。
  - 0001 之后新建的表不会自动授权给 gateway_app（没有 `ALTER DEFAULT PRIVILEGES`）。
  - PRD §3.3 的 `gateway_admin`（reset 时用来 TRUNCATE）没有创建。
- 修改建议：由指挥在 `scripts/**` 里仿照 `p1_database.py` 做：`ALTER DATABASE gateway OWNER TO postgres`、`ALTER SCHEMA public OWNER TO postgres`，生成 `gateway-migration.env`，在 Makefile 加 `migrate-gateway` 并接进 `up`。迁移里增加 `ALTER DEFAULT PRIVILEGES … GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gateway_app`，对 audit 类表单独撤销 UPDATE 和 DELETE。DB 测试补一条：gateway_app 不能 `DROP TABLE audit_events`。这一条不阻塞各 worker 跑单测，但必须在集成分支第一次 `make up` 之前完成。

**P1-13 审计哈希没有覆盖 `created_at_wall`**
- 位置：`services/gateway/app/audit/chain.py:66-90`、`112-121`
- 问题：参与哈希的事件里，`created_at_wall` 固定写成 None，真实时间由数据库的 `now()` 写入，所以篡改时间戳无法被发现。PRD §7.12 要求哈希覆盖除 hash 之外的整条事件。另外，`append` 在调用方传入的事务里执行，如果调用方的事务里还有 L3 HTTP 调用，审计锁会被一直持有，违背「各自小事务」的要求。
- 修改建议：在 Python 里用注入的时钟取时间，并把 RFC3339 字符串纳入哈希，`verify_chain` 按同样的方式重算。另外提供 `append_tx(engine_or_lock_conn, …)`，自己开启并提交小事务。README 写明：审计不得在包含 HTTP 调用的事务内追加。

**P1-14 阶段归属互相矛盾；② 的数据来源未定**
- 位置：`pipeline/contract.py:10`（注释写 W3）、`pipeline/audit.py:10`（注释写 W5）、`docs/progress/W0-foundation.md`「接口说明要点」与 L2-plan §3 不一致
- 问题：
  - L2-plan §3 把「⑨评估审计」和 G4 组装分给 W3，W0 文档却写 audit 归 W5，① contract 的归属在计划里没写。
  - ② 实体与所有权排在 ⑤ 快照之前，但所有权要用 L3 数据；W2 不知道应该依据 read_records，还是提前拉快照。
- 修改建议：指挥拍板，并写进任务书：① 和 ⑨ 归 W3，W5 只做 G11、G16 的读取端。② 只用会话读取账本做「已见实体与结构」检查（ENTITY_UNSEEN、PAYEE_NOT_FOUND）；归属的最终判定放在 ⑤ 之后，由 bankcore `resolved_steps` 统一返回 404 或 OWNERSHIP_MISMATCH，并在 `context.py` 中注明。如果要调换顺序，必须先改 PRD。

### P2：可在后续任务中处理

**P2-15 启动护栏补全**
- 位置：`config.py:34-63`、`main.py:53-66`
- 修改建议：
  - F、A、S、D 四个令牌必须非空且互不相同。`identify_service` 取第一个匹配，如果配置错误导致 F 和 A 的令牌相同，A 会被识别成 F，变成提权。
  - 启动错误改用专门的 `ConfigError`，不要借用 `Code.FORBIDDEN` 加 500。
  - 增加遍历路由的测试：`public_app` 中不能有任何以 `/internal` 开头的路由，`internal_app` 中只能有这类路由，防止后来的 worker 误挂。

**P2-16 索引与外键**
- 修改建议：补索引 `utterances(sid, status)`、`plans(sid)`、`approvals(customer_id, status)`（G17）、`read_records(sid)`、`workflow_events(customer_id, occurred_at)`、`notifications(customer_id, read_at_wall)`；补外键 `plans.sid` → `sessions.id`、`approvals.sid` → `sessions.id`。

**P2-17 契约错误 `details` 为空**
- 位置：`middleware.py:71-79`
- 修改建议：§9.1 规定 details 是字段错误。可以只返回字段路径和错误类型，不回显值，这样既不泄露数据，又方便 L1 自修。

**P2-18 Settings 没有收拢密钥与 URL 的读取**
- 修改建议：令牌、JWT 公钥与私钥路径、HMAC 密钥都由 `Settings` 统一读取，并注入到依赖里，测试只需替换 Settings。`BANK_BACKEND_URL` 这个名字可以接受（待决第 4 条），由指挥补记到 PRD §11.1。

---

## worker 4 条待决的处理意见

| 待决 | 意见 |
|---|---|
| 1. 迁移引导接线 | 见 P1-12。由指挥在 `scripts/**` 中完成，必须在集成分支第一次 `make up` 之前做 |
| 2. 运行镜像依赖 | 见 P0-7。必须在 W0 分支内完成，不能留到合入之后 |
| 3. import 路径 `services.gateway.app` | 同意。Dockerfile 的 WORKDIR 是 `/app`，`python -m` 能找到它。所有任务书统一使用这个路径；包内用相对导入。PRD §3.6 的目录名（api、internal、approval 等）按 P0-4 的路由分组对齐 |
| 4. 未列出的枚举 | 不要留成无约束的字符串。在 W0 的 `schemas/` 里定义 `ExecutionJobStatus`（建议与 plans 的执行段一致：EXECUTING、UNKNOWN、EXECUTED、FAILED、CANCELLED）和 `WorkflowEventStatus`，由指挥确认后加 CHECK，并在 PRD §7.2 补记。`event_decisions` 不存 RETRYING，这一点正确（RETRYING 不是终态） |

## 约束核对（AGENTS.md §2）

没有发现违反约束的地方：金额字段是 `amount_cents BIGINT`；L2 不碰 L3 的库；没有 LLM 授权路径；没有引入大型框架。冻结 DDL（`schema_v1.json`）的做法沿用了 bank-backend，可以接受，但要配合 P0-6 的漂移测试。过度设计方面没有发现问题，主要问题是**少定义了共享接触面**。

---

## 给 W1–W4 任务书的接口约定要点

前提：P0 全部修完。以下文件名按修改建议给出，指挥可以统一调整。

**所有任务通用**
- 不改以下文件：`pipeline/__init__.py`、`pipeline/context.py`、`schemas/**`、`auth/matrix.py`、`state.py`、`tables.py`、`migrations/**`、`main.py`、`pyproject.toml`、`uv.lock`、compose。需要改动时写进「待决」。
- 状态变更只能调用 `state.transition`；带 `request_id` 的接口只能用 `request_dedup` 辅助函数。
- 错误只能 `raise GatewayError(Code.X)`，code 只来自 `safebank_contracts.codes`。
- 调用 L3 只能通过 `L3Client`；测试使用 fake L3 的有状态 transport。
- 审计只能用 `audit.append_tx`，并且不在包含 HTTP 调用的事务内追加。
- 测试只放在 `tests/gateway/<任务名>/`。Makefile 只允许在末尾追加本任务的 `test-*` 目标。

**W1 身份与原话**
- 可以改：`auth/tokens.py`（签发、续签、撤销检查）、`auth/pin.py`、`api/auth.py`、`api/sessions.py`、`utterance/**`、`policies/intent_lexicon.yaml`、`tests/gateway/w1/`。
- 接口：产出 `schemas.NormalizedScope`、`UtteranceRef`，写入 `utterances` 与 `pending_clarifications`；矩阵已由 W0 登记全表，W1 只负责补齐矩阵测试 `test_auth_matrix.py`。
- 提供 `utterance.loader.load_for_plan(conn, sid, ids, caller) -> AuthorizationInputs`，供编排器填进 `ctx.authorization`。

**W2 读代理与来源污点**
- 可以改：`api/tools.py`（G2）、`pipeline/entity.py`、`pipeline/provenance.py`、`pipeline/intent.py`、`taint/**`、`policies/trust.yaml`、`policies/injection_patterns.yaml`、`tests/gateway/w2/`。
- 只能给 `policies/tools.yaml` 补规则值，不能改结构。
- 阶段只写 `ctx.provenance`、`ctx.intent` 和 findings；W1 合入前用 `AuthorizationInputs` 的 fixture。

**W3 预演、工作流与决策**
- 可以改：`api/plans.py`（G4）、`pipeline/contract.py`、`snapshot.py`、`preflight.py`、`workflow.py`、`decision.py`、`audit.py`（⑨）、`evaluation/repo.py`、`workflow/**`、`policies/{thresholds,workflow}.yaml`、`tests/gateway/w3/`。
- 预演只调用 bankcore；REQUIRE_APPROVAL 时调用 W4 的 `approval.service.create_for_evaluation`（W4 合入前是占位实现）。
- 实现 profile 开关（`Stage.enabled`）与 SHADOW_B1 模式。

**W4 审批与执行**
- 可以改：`api/approvals.py`、`api/execution.py`（G5–G10、G17）、`approval/**`（PlanView、view_hash、binding_mac、OTP）、`execution/**`（编排、claims、恢复工作器）、`locks.py`、`tests/gateway/w4/`。
- 复演调用 `pipeline.evaluate(ctx, mode=EXECUTE_REPLAY)`，结果用 W3 的 `append_evaluation` 落库。
- `locks.customer_lock` 必须能被 W5 复用，S1 和 demo 入口都要走它。

**W5（波次 2）**
- S1、通知、G11、G16、D 接口，在已有占位路由模块上实现，复用 `locks`、`state`、`audit`。

---

## 第二轮复审（3059b53，相对 378ba67）

- 复审人：只读评审 subagent（Claude），2026-10-23
- 我自己复跑了 `make test-gateway`（52 passed）和 `make test-gateway-db`（14 passed）。`make lint`、`make test`、`make up`、`make test-p1` 沿用指挥的复跑结果。
- 另外跑了 4 个只读探针：
  - **探针 A**：public_app 在不带任何凭证的情况下调 `POST /plans/evaluate` 和 `GET /approvals/apv_1`，都返回 503 `DEPENDENCY_UNAVAILABLE`，而不是 401。说明公共路由**没有挂接授权依赖**。
  - **探针 B**：用 `EvalContext(l3=L3Client(transport=FakeL3().transport()))` 调 `await ctx.bank_state()`，抛出 `AttributeError: 'dict' object has no attribute 'json'`。
  - **探针 C**：在临时 Postgres 16 上 `upgrade head`，然后用 `append_tx(now_wall=2026-10-15T09:00+08:00)` 追加一条审计，`verify_chain` 返回 `(False, 'tampered content at e1')`。同一流程用默认的 UTC 时钟追加则 `(True, …)`。
  - **探针 D**：通读代码。`state.transition` 没有调用 `legal()`，合法迁移表不起约束作用；仓库中不存在 `approval/service.py`、`approval/planview.py`、`evaluation/repo.py`、`locks.py` 中的任何一个，也不存在复演接口。

### 一、逐条核对

| 编号 | 判定 | 证据与说明 |
|---|---|---|
| P0-1 EvalContext 传数据 | **部分** | `pipeline/context.py:100-127` 已按阶段分好结果位置，有 mode、profile、wall_now、conn、l3、stage_status。剩下三个缺口：① 没有放「已解析步骤 + 每步 ActionKind、基础等级、outflow」的位置，而 ③④⑥⑦⑧ 都要用它；② `ProvenanceResult`、`IntentResult` 等（69-97 行）只是空壳，比如 `arg_sources: dict[str, Any]`、`matched: bool`，而文件头又写明「must not change the shape」，W2 和 W3 没法补自己需要的字段；③ `bank_state()`（135-149 行）运行即报错（探针 B，见 N3），而且它以 `steps=[]` 取快照，取不到收款人目录与对方客户，⑤ 还得再取一次，指挥决定 #4 的「只取一次」做不到 |
| P0-2 短路与 profile | **已修复** | `pipeline/__init__.py:40-64`：DENY 只跳过 ⑤⑥⑦；`decision.py:8` 中 `always_run=True`；E 类错误抛 GatewayError；`DISABLED_BY_PROFILE`（context.py:59-66）；`evaluate(mode=…)` |
| P0-3 对外模型与注册表 | **部分，必须在 W0 补完** | `schemas/**` 的模型和枚举都齐了（`enums.py:91-108` 定义了 ActionKind）。但 `policies/tools.yaml` 和 `app/policy/registry.py` 都不存在，见下方单独说明 |
| P0-4 路由与矩阵 | **部分** | 已做：路由模块拆分（`api/*.py`、`internal/scheduled.py`）；矩阵改成按 path_format 作键、值是多种凭证的元组，并登记了全表（`auth/matrix.py:51-102`）；双向覆盖检查（125-158 行）；`Caller` 带 utts 和 origin。**未做**：`build_public_app`（`main.py:54-66`）`include_router` 时没有挂 `Depends(caller_dependency(...))`，只有 internal_app 挂了（82、86 行）。公共路由实际上不鉴权（探针 A），也就不满足「未登记默认拒绝」。另外 `public_key` 参数接收了但没有用（`main.py:45`），JWT 路由永远拿不到公钥 |
| P0-5 共享状态与交叉接口 | **部分（核心未做）** | `state.py` 有迁移表和 `transition`，但第 107-124 行**没有校验迁移是否在 `TRANSITIONS` 里**：调用方传什么 `from_statuses` 和 `to`，它就执行什么，例如 EXECUTED → EVALUATED 也能执行。`test_state.py` 只测了「源状态不符时失败」，没有测「非法迁移被拒」。指挥决定 #1 要求的接口占位（G4 用的「创建审批、计算 view_hash」，W4 用的「执行前复演」，以及 `append_evaluation`、`customer_lock`）**没有任何代码**，只在进度文件中用文字描述。W3 和 W4 依然要各自设计这些签名 |
| P0-6 表与字段、迁移规则 | **已修复**（request_dedup 有新问题，见 N5） | `tables.py` 新增 `request_dedup`、`pin_failures`、`ip_ratelimit`、`auth_sessions`，以及第一轮列出的全部字段与约束（如 `evaluations` 加了 `UNIQUE(plan_id, revision)`）。防漂移测试有两个：`test_ddl_drift.py` 和 `db/test_drift.py`（compare_metadata）。README 写了「波次 1 不写迁移」 |
| P0-7 运行镜像依赖 | **已修复** | `requirements.txt`、`requirements-test.txt` 已重新导出；指挥实测 `make up` 6 个服务 healthy |
| P1-8 fake L3 | **基本修复**（剩余问题见 N7） | `l3/fake.py:52-69` 用自定义 transport 抛出真实的 ReadTimeout 和 RemoteProtocolError；intent 状态机（213-285 行）；按调用次数设定行为（99-107 行）；令牌校验与 `customer_id` 校验；`set_snapshot`、`set_result`；`calls` 调用日志 |
| P1-9 L3 客户端分类 | **已修复** | `l3/client.py:79-101`：三类异常，并解析业务错误体；超时可注入（58 行） |
| P1-10 框架错误统一 | **已修复** | `middleware.py:86-103` |
| P1-11 JWT 严格校验 | **已修复** | `auth/tokens.py:92-106`：aud 必须是等于期望值的字符串，scope 和 utts 必须是 `list[str]` |
| P1-12 库所有权与迁移接线 | **已修复** | `scripts/gateway_database.py`（库 owner 改为 postgres、建 `gateway_admin`、写 migration env）；`0001_gateway.py:35-51`（默认权限）；Makefile 的 `up` 已接入；有「不能 DROP」测试。小问题见 N9 |
| P1-13 审计哈希 | **部分** | 哈希已覆盖 `created_at_wall`，也有了 `append_tx`。但时区没有规范化，非 UTC 时钟会被误判为篡改（探针 C，见 N4） |
| P1-14 阶段归属 | **已修复（文档层面）** | README 分工表按指挥决定 #3 写了。② 依赖的 `bank_state()` 不可用，问题归入 P0-1 / N3 |

### 二、P0-3 的单独判断：`tools.yaml` 和注册表能不能留给 W2？

**不能，必须在 W0 补上。** worker 把它算作 P2 并留给 W2，这会让三个并行任务依赖一个还不存在、结构也由别人决定的接口：

- **W3**：⑧ 的风险等级是「基础等级 + 升级」，基础等级来自 `tools.yaml`（PRD §5.0、§7.9）。⑦ 的 RISKY_SEQUENCE、CUMULATIVE_OUTFLOW 需要每个工具的 ActionKind 判定和 outflow 标记。⑥ 的 B1 只能逐步预演，也要按工具分类。
- **W4**：PlanView 的步骤标题、`irreversible` 标记、OTP 要求（红级），都需要按工具查注册表。
- **W1**：G19 要校验 `action_kind` 与 `fields` 是否等于对应 W 工具的 args，这需要 ActionKind 到工具的映射。
- **W2**：③ 的敏感参数和 `allowed_origins` 来自注册表。

只有枚举不够：注册表的结构（字段名、卡类 LOOSEN / TIGHTEN 的判定函数签名、加载方式）如果由 W2 在并行期间定，W3 和 W4 只能先写死或自造 fixture，合入时必然要返工。这正是 L2-plan §0 要避免的情况。

**修改建议**：W0 新增 `services/gateway/policies/tools.yaml` 和 `app/policy/registry.py`，至少包括：
- `ToolPolicy` 数据类，字段为 tool、base_risk、sensitive_params、allowed_origins、outflow、irreversible、title_template；
- `action_kind(tool, args, shadow_state) -> ActionKind`，卡类工具按影子状态判定放松还是收紧（§7.6）；
- 加载函数与校验：工具集合必须等于 `WRITE_ARGS` 的 14 个工具，不允许缺失或多出；
- 按 PRD §9.4 的「基础等级、ActionKind、敏感参数」逐项填好 14 个工具的值。

之后 W2 只能提交值的修正，不能改结构。

### 三、第二轮新引入或遗留的问题

**P0（阻塞）**

- **N1 公共路由没有鉴权**（P0-4 遗留）
  - 位置：`main.py:54-66`
  - 问题：探针 A 已证实。W1–W4 写真实处理函数时，如果忘了加依赖，接口就会裸奔；矩阵覆盖检查只检查「登记了没有」，不检查「执行了没有」。
  - 修改建议：`FastAPI(dependencies=[Depends(caller_dependency(env, public_key))])` 加在 public_app 上，或在每次 `include_router` 时都加；处理函数通过 `request.state.caller` 或依赖返回值拿到 `Caller`；`build_public_app` 必须把 `public_key` 传进去。补测试：对矩阵中每个非开放路由，不带凭证、只带服务令牌、凭证换成另一种组合，都应返回 401 或 403，不得进入处理函数。

- **N2 状态迁移入口不执行合法迁移表**（P0-5 遗留）
  - 位置：`state.py:107-124`
  - 修改建议：
    - `transition` 先根据 `table.name` 查 `TRANSITIONS`，要求每个 `from_statuses` 到 `to` 都合法，否则直接抛错（代码错误，不是 409）；
    - 加 `test_illegal_transition_rejected`；
    - 同时补齐复演与重新审批需要的边（§7.10.4–5）：CONFIRMED → PENDING_APPROVAL、DENIED、REPLAN（复演后 view_hash 变化，或升级为 DENY / REPLAN），EVALUATED → PENDING_APPROVAL（已有）。
    - EXECUTING 状态是在复演之前写入还是之后写入，在 `state.py` 的注释里写明；如果是之前，还需要 EXECUTING → PENDING_APPROVAL、DENIED、REPLAN 这几条边。
    - `execution_jobs` 也纳入迁移表。

- **N2b 交叉接口占位不存在**（P0-5 遗留，指挥决定 #1）
  - 修改建议：按第一轮 P0-5 (b) 建立以下文件，每个函数给出签名、输入输出模型，函数体 `raise NotImplementedError`，并在文件头写明由谁实现：
    - `approval/service.py: create_for_evaluation`（W4）
    - `approval/planview.py: build_view -> (PlanView, view_hash)`（W4）
    - `evaluation/repo.py: append_evaluation`（W3）
    - `locks.py: customer_lock`（W4）
    - 执行前复演的入口，例如 `execution/replay.py: replay(ctx) -> Decision`（W3 实现、W4 调用；如果直接复用 `pipeline.evaluate`，就写明 EXECUTE_REPLAY 模式下各阶段的差异）

**P1（开工前应修）**

- **N3 `bank_state()` 不可用，取快照的范围也不对**
  - 位置：`context.py:145-148`
  - 问题：客户端现在返回 dict，这里却调用 `.json()`（探针 B）。另外以 `steps=[]` 取快照拿不到收款人目录与对方客户。
  - 修改建议：
    - 改成 `BankState.model_validate(await self.l3.snapshot(...))`；
    - 请求时带上 `plan_draft.steps`；
    - 规定 ⑤ 只能复用这份快照、钉住其中的 versions，不得再次拉取；
    - 补一条用 fake L3 的单元测试，断言只调用了一次 snapshot。

- **N4 审计链对非 UTC 时钟误报篡改**
  - 位置：`audit/chain.py:66`
  - 问题：asyncpg 读回的是 UTC 时间，`isoformat()` 与写入时 +08:00 的字符串不一致（探针 C）。
  - 修改建议：`_event` 中统一使用 `created_at_wall.astimezone(UTC).isoformat(timespec="microseconds")`；拒绝 naive datetime；DB 测试补一条 +08:00 时钟的用例。

- **N5 `request_dedup` 的进行中行可能永久残留，错误码也用错了**
  - 位置：`idempotency.py:42-66`
  - 问题：
    - `produce()` 抛异常时没有清理 IN_FLIGHT 行。如果调用方插入 IN_FLIGHT 和执行 `produce()` 不在同一个事务里，例如自动提交、或者 G8 先提交 IN_FLIGHT 再调 L3，异常或进程崩溃后，这个 request_id 会永远返回 409。
    - 进行中返回的是 `RUN_IN_PROGRESS`，这是 L1 线程的错误码；按 §9.8 应该用 `EXECUTION_IN_PROGRESS`。
  - 修改建议：在文档字符串里规定「插入、produce、更新必须在同一个调用方事务里；不得把 HTTP 调用放进这个事务，G8 的执行幂等走 `plans`、`execution_jobs`，不走这里」；`produce` 异常时删除这一行（同事务内回滚即可）；改用 `EXECUTION_IN_PROGRESS`；补「produce 抛异常后可以用同一 ID 重试」的测试。

- **N6 结果类型的所有权**
  - 位置：`context.py:69-97`
  - 修改建议：把各阶段的结果类型移到各自拥有者的模块里（例如 `pipeline/results/provenance.py` 归 W2，`preflight.py`、`workflow.py` 归 W3），`context.py` 只 import 它们；或者明确规定「拥有者可以在自己的结果类里新增字段，不能删改已有字段」。另外由 W0 在 `EvalContext` 上新增 `steps: tuple[ResolvedStep, ...]`，含 step、tool、action_kind、base_risk、outflow，规定由 ② 写入。这样既不让 W2、W3 争同一个文件，也不让它们被空壳结构卡住。

**P2（可在后续任务中处理）**

- **N7 fake L3 的遗留问题**
  - 位置：`fake.py:222-249`
  - 问题：
    - 不经过 prepare 就能直接 execute 并提交；
    - `business_fail` 把 intent 记成 COMMITTED，而 §7.10.6 规定业务失败回滚后 intent 应保持 PREPARED；
    - 同一 plan 用不同的 `plan_hash` 重复 prepare 时不报 IDEMPOTENCY_CONFLICT；
    - `_current_behavior` 和 `_pending_drop` 是整个 fake 共享的字段，W4 的并发双花测试会互相串扰。
  - 修改建议：W4 开工后由 W4 修，或者 W0 顺手修掉。

- **N8 schema 校验不全**
  - 问题：`AppFormPlanRequest.form_action_ids` 没有 1..5 的长度约束（§9.5）；`ExecutionAccepted.status` 应该是 `Literal["UNKNOWN"]`。

- **N9 角色权限细节**
  - 问题：`gateway_admin` 没有 LOGIN，也没有凭证，reset 用不了，W5 需要补；默认权限没有覆盖以后新建的序列（`ALTER DEFAULT PRIVILEGES … ON SEQUENCES`）。

### 四、最终结论

**否，还不能作为 W1–W4 并行基础。** 第二轮已修复的部分：P0-2、P0-6、P0-7、P1-9 至 P1-12，以及 P1-8 的大部分，基础设施质量明显提升。

剩余阻塞项都集中在「共享接触面没做实」：

1. **P0-3**：`policies/tools.yaml` 和 `app/policy/registry.py` 必须由 W0 建好结构并按 §9.4 填值，否则 W1、W3、W4 依赖一个不存在的注册表。
2. **N1（P0-4）**：公共路由要挂上授权依赖并传入 public_key，补「每个非开放路由不带凭证都返回 401 或 403」的测试。
3. **N2 与 N2b（P0-5）**：`transition` 要执行合法迁移表，并补齐复演与重新审批的边；交叉接口占位（审批创建、PlanView 与 view_hash、评估落库、客户锁、复演入口）要落成真实的签名文件。
4. **开工前顺手修完的 P1**：N3（`bank_state` 报错与取快照范围）、N4（审计时区）、N5（幂等的进行中行与错误码）、N6（结果类型的所有权与 `steps` 位置）。每条改动都很小，但不修的话 W2、W3 第一天就会撞上，或者被迫去改共享文件。

以上修完后，只需要定点复核：探针 A–D 全部变成符合预期的结果，新增测试通过。之后即可开启波次 1。如果时间紧，可以先只修 1、2、3 与 N3、N4，N5、N6 写进 W1、W3 的任务书作为首项，但这不是推荐做法。

---

## 第三轮定点复核（c691e15，相对 3059b53）

- 复核人：只读评审 subagent（Claude），2026-10-23
- 只核对第二轮复审中的阻塞项（P0-3、N1、N2、N2b）和 P1（N3–N6），同时检查本轮改动有没有引入新问题。
- 重跑的探针：
  - **A**：public_app 在不带凭证、只带 A 服务令牌、以及只带 F 服务令牌调 G1 三种情况下的响应。
  - **B**：`EvalContext.bank_state()` 调两次，看返回什么、发了几次 snapshot 请求。
  - **C**：在临时 Postgres 16 上 `upgrade head`，用 +08:00 时钟 `append_tx`，再跑 `verify_chain`。
  - **D**：检查交叉接口占位文件，并试一次非法状态迁移。
- 另外在同一个临时库上补跑了 N5：先让 `produce()` 抛异常，再用同一个 request_id 重试。

### 一、阻塞项与 P1 核对

| 编号 | 判定 | 证据 |
|---|---|---|
| P0-3 工具注册表 | **已修复**（有新问题，见 R2、R3） | `policies/tools.yaml` 登记了 14 个工具；`app/policy/registry.py:24-129` 有 `ToolPolicy`、`load_policies`（校验工具集合等于 `WRITE_ARGS`、base_risk 只能取三档）和 `action_kind(tool, args, shadow_state)`。基础等级和敏感参数逐项对照 PRD §9.4，全部一致 |
| N1 公共路由鉴权 | **已修复** | `main.py:47-55` 给 FastAPI 加了全局依赖 `caller_dependency(env, public_key)`；`deps.py` 把 `Caller` 写到 `request.state.caller`。探针 A：不带凭证调 `/plans/evaluate`、`/approvals/{id}` 返回 401；只带 A 服务令牌、不带 JWT 也是 401；F 服务令牌调 G1 进入占位实现（503）；`/health` 返回 200。还剩一处：`serve()` 没有加载公钥，见 R1 |
| N2 迁移表执行与新状态边 | **已修复** | `state.py:142-153`：每条边都对照 `TRANSITIONS` 检查，非法时抛 `ValueError`（探针 D 用 EXECUTED → EVALUATED 证实）；新增 CONFIRMED → PENDING_APPROVAL、DENIED、REPLAN 三条边；`execution_jobs` 纳入迁移表；文件头注明了 EXECUTING 在复演通过之后才写入 |
| N2b 交叉接口占位 | **已修复** | 新增 `approval/service.py`（`create_for_evaluation`）、`approval/planview.py`（`build_view -> (PlanView, view_hash)`）、`evaluation/repo.py`（`append_evaluation`）、`execution/replay.py`（`replay(ctx) -> Decision`）、`locks.py`（`customer_lock` 异步上下文管理器）。每个都写明了归属，函数体都是 `NotImplementedError`。`test_probes.py` 断言它们存在 |
| N3 bank_state | **已修复**（有一个小问题，见 R5） | `context.py:153-174`：改为 `BankState.model_validate(response)`，请求时带上 `plan_draft.steps`。探针 B：调两次返回同一个对象，snapshot 只请求了 1 次 |
| N4 审计 UTC | **已修复** | `chain.py:46-50`：拒绝 naive datetime，统一转成 UTC，精确到微秒。探针 C 在第二轮返回 `(False, tampered)`，本轮变成 `(True, '1 events')` |
| N5 request_dedup | **已修复**（有一个小问题，见 R6） | `idempotency.py:58-69`：`produce` 抛异常时删除进行中的那一行；进行中改为返回 `EXECUTION_IN_PROGRESS`；文档字符串写明「同一事务、不得包含 HTTP 调用、G8 不走这里」。补跑：`produce` 抛异常后，用同一个 ID 重试得到 `(201, …, False)` |
| N6 结果类型归属与 steps | **已修复** | `context.py` 文件头注明「各阶段结果类的拥有者可以新增字段，不能删改已有字段」；新增 `ResolvedStep`（step_id、tool、args、action_kind、base_risk、outflow）和 `ctx.steps`，规定由 ② 写入 |

附带的修复：fake L3 现在要求先 prepare 才能 execute；同一 plan 换了 `plan_hash` 会返回 `IDEMPOTENCY_CONFLICT`；`business_fail` 时 intent 保持 PREPARED。`AppFormPlanRequest` 已加上 1..5 的长度约束。这些都对应第二轮的 N7、N8。

### 二、本轮引入或暴露的新问题

只列真问题。R1–R3 改动的都是 worker 无权修改的共享文件或 W0 拥有的文件，所以需要在开工前由 W0 或指挥修掉。

**P1（开工前由 W0 或指挥修，各约 1–5 行）**

- **R1 生产入口不加载 JWT 公钥**
  - 位置：`main.py:116-117`（`serve()` 调用 `build_public_app(settings)` 时既没有传 `env` 也没有传 `public_key`）；`config.py` 里也没有公钥路径这一项。
  - 问题：部署之后，所有需要 JWT 的路由都会固定返回 401。`main.py` 已经冻结，W1 没有权限补上这一步。
  - 修改建议：在 Settings 中增加 `jwt_public_key_path`（默认 `/run/secrets/jwt-public.pem`，compose 已经挂载了这个文件），`serve()` 读取后传给两个应用。文件不存在时拒绝启动，不要静默降级成「全部 401」。

- **R2 pyyaml 不在运行依赖里**
  - 位置：`policy/registry.py:13` 在模块导入时就 `import yaml`，并在模块层执行 `load_policies()`。但 pyyaml 只在 `pyproject.toml` 的 dev 分组和 `requirements-test.txt` 里，`requirements.txt` 中没有。
  - 问题：W2、W3 一旦在运行路径上 import 注册表，gateway 容器就会启动失败，和第一轮的 P0-7 是同一类问题。`pyproject.toml` 只有 W0 能改。
  - 修改建议：把 `pyyaml==6.0.3` 移到主依赖，重新导出 requirements 并重建 wheels。

- **R3 `action_kind` 在找不到卡时默认判为收紧（fail-open）**
  - 位置：`registry.py:99-116`
  - 问题：已实测，`card_id` 不在影子状态中时，`update_card_limits` 把限额调到 99,999,999 分返回 `CARD_LIMIT_TIGHTEN`，`update_card_controls` 打开 online 返回 `CARD_CONTROL_TIGHTEN`。正常情况下 ② 会先拒绝不存在的卡，但这里作为安全判定，默认值应该偏保守。
  - 修改建议：找不到卡时直接抛错（让调用方按 NOT_FOUND 处理），或者返回 LOOSEN；补一条测试。

**P2（写进波次 1 任务书即可）**

- **R4 `outflow` 按工具定义，口径偏粗**
  - 问题：`tools.yaml` 中 transfer 的 `outflow: true`，但给本人账户转账（TRANSFER_SELF）不是对外转出。`subscribe_product` 也标成了 `outflow: true`，而 PRD §7.8 的 CUMULATIVE_OUTFLOW 和 SPLIT_TRANSFER 口径是「对外转账」。
  - 建议：W3 在实现时按 `action_kind == TRANSFER_EXTERNAL` 加上创建定时转账（SCHEDULE_CREATE）的口径计算，`ResolvedStep.outflow` 只作为初值。申购是否计入，由指挥裁定后改 tools.yaml 的值。

- **R5 app_form 计划的快照取不到步骤**
  - 位置：`bank_state()` 只从 `plan_draft.steps` 取步骤。
  - 问题：app_form 计划没有 PlanDraft，取快照时 `steps=[]`，拿不到收款人目录。
  - 建议：W3 在组装 G4 时，把由 form_action 生成的步骤放进同一个位置（例如构造一份内部 draft），或者由指挥改成从统一的 `draft_steps` 取。另外，复演必须使用新的 `EvalContext`（缓存的快照不能跨评估复用），这一点要在 `replay` 的说明中写明。

- **R6 数据库错误时的清理会掩盖原始异常**
  - 位置：`idempotency.py:61-69`
  - 问题：如果 `produce` 抛出的是数据库错误，事务已经中止，接下来的 DELETE 会再抛 `InFailedSQLTransaction`，原始异常就看不到了。行本身会随事务回滚消失，所以结果没错，只是诊断信息丢了。
  - 建议：DELETE 外面包一层 `try`，失败就忽略，然后重新抛出原始异常。

### 三、最终结论

**是（有条件）：可以作为 W1–W4 的并行基础。** 前两轮的所有 P0、P1 都已关闭，探针 A–D 全部符合预期。架构层面已经没有阻塞项。

条件是开工前由 W0 或指挥修完 R1、R2、R3。这三项改动的都是 worker 无权修改或由 W0 拥有的文件（`main.py`、`config.py`、`pyproject.toml`、`requirements.txt`、`policy/registry.py`），每项只有几行。修完只需复跑 `make test-gateway` 和 `make up`，不必再做完整复审。

### 四、波次 1 任务书接口约定：相对第一轮「接口约定要点」的更新

第一轮的要点整体仍然有效，以下按本轮实际代码更新或补充：

1. **文件与模块名以实际代码为准**：
   - 表定义在 `app/tables.py`（不是 models.py），对外模型在 `app/schemas/**`；
   - 幂等用 `app/idempotency.request_idempotent`：插入、produce、更新在同一事务里，事务内不得有 HTTP 调用，G8 的执行幂等走 `plans`、`execution_jobs`，不走这里；
   - 测试替身用 `FakeL3().transport()` 配合 `L3Client`。
2. **阶段归属按指挥决定 #3，与第一轮不同**：①②③④ 归 W2（第一轮写的是 ① 归 W3），⑤⑥⑦⑧⑨ 和 G4 组装归 W3。② 写 `ctx.steps`，其中的 action_kind 和基础等级用 `policy.registry.action_kind` 和 `policy()` 计算，不得另写一套映射。
3. **注册表**：`policies/tools.yaml` 和 `app/policy/registry.py` 的结构由 W0 冻结。任何任务只能提出值的修正，写进待决，由 W2 或指挥落地。outflow 的口径见 R4。
4. **状态迁移**：只能调用 `state.transition`。非法边会抛 `ValueError`，属于代码错误，不要捕获后转成 409。需要新边就写待决。EXECUTING 只在复演通过之后写入。
5. **鉴权**：所有公共路由已由全局依赖鉴权。处理函数从 `request.state.caller` 读取 `Caller`（含 customer_id、sid、utts、origin），不要再声明一遍依赖，也不得从请求体读取 origin 或 customer_id。授权矩阵冻结。
6. **交叉接口**：
   - W3 实现 `evaluation/repo.append_evaluation` 和 `execution/replay.replay`。
   - W4 实现 `approval/service.create_for_evaluation`、`approval/planview.build_view` 和 `locks.customer_lock`。
   - 双方签名都不能改，在对方合入之前各自用测试替身打桩。W3 的 G4 测试要用打桩的 `create_for_evaluation`；W4 的执行测试要用打桩的 `replay`。
   - `replay` 必须使用新的 `EvalContext`。
7. **结果类**：拥有者只能在自己的结果类里新增字段。`EvalContext` 本身的字段冻结。
8. **审计**：只能用 `audit.append_tx`，`now_wall` 必须带时区。
9. **迁移**：波次 1 期间不写迁移，需要改表写进待决，由指挥串行追加 `0002_wave1`。
10. **W1 新增事项**：如果 R1 没有在 W0 完成，就把「公钥加载」明确写进 W1 的任务书，并授权 W1 只改 `config.py` 和 `serve()` 中加载公钥的那几行。
