# gateway（L2 Security Gateway）

三明治架构的中间层：L1（agent）的所有读工具、评估与执行都经这里进入 L3。L2 是唯一持有 `GATEWAY_SERVICE_TOKEN` 的一方，也是唯一能写 L3 的一方。W0 打下的基础骨架已通过架构评审修订，W1–W4 在此之上并行填业务。

> import 路径：本服务包名是 `services.gateway.app`（目录 `services/gateway/app/`），不是顶层 `app`。仓库共享 PYTHONPATH 里已有 bank-backend 的 `app` 包，为避免同名冲突，gateway 一律用 `services.gateway.app.*` 导入；包内用相对导入（`from .config import ...`）。

## 启动

```bash
make keys && make up          # 根目录编排：build → postgres → bank 迁移/seed → gateway 迁移 → 全部服务
# 或单独跑进程（两个绑定地址必须不同）：
GATEWAY_PUBLIC_BIND=127.0.0.1 GATEWAY_INTERNAL_BIND=127.0.0.2 \
  .venv/bin/python -m services.gateway.app.main
```

一个 asyncio 进程跑两个 `uvicorn.Server`：

- `public_app`：`:8200`，只有公共路由（G1–G22a + `/health`），**绝不注册 `/internal/*`**。
- `internal_app`：`:8201`，**只有 `/internal/*`**（S 服务令牌），供 scheduler 回调（S1）。

启动时校验授权矩阵覆盖（`auth/matrix.coverage_errors`）：已挂载路由 ⊆ 矩阵、矩阵里每条记录都有对应路由（D 路由按模式），public 不能有 `/internal/*`、internal 不能有非 `/internal/*`。

## 测试

```bash
make test-gateway        # 单元测试（双端口隔离、护栏、错误、JWT、矩阵、fake L3、流水线、DDL 防漂移）
make test-gateway-db     # 起唯一命名/随机端口的临时 Postgres 16，跑迁移与 DB 测试，测完销毁
make test                # 全量（含 P1 116 单测）
make up && make test-p1  # 全栈健康 + P1 真实 PG 验收（指挥在集成分支跑）
```

`make test-gateway-db` 内部设置 `GATEWAY_DB_TESTS=1`；其余 pytest 调用会跳过 DB 测试。多个 worker 可并发跑 `test-gateway-db`，互不冲突。

## 环境变量（gateway.env）

| 变量 | 说明 |
|---|---|
| `GATEWAY_DATABASE_URL` | 运行角色 `gateway_app` 连 `gateway` 库 |
| `GATEWAY_PUBLIC_BIND` / `GATEWAY_INTERNAL_BIND` | 两端口绑定地址（必须不同、不能 `0.0.0.0`） |
| `GATEWAY_SERVICE_TOKEN` | L2 → L3 |
| `FRONTEND_SERVICE_TOKEN` / `AGENT_SERVICE_TOKEN` / `SCHEDULER_SERVICE_TOKEN` / `DEMO_CONSOLE_TOKEN` | F/A/S/D 服务令牌，常量时间比较，启动时校验互不相同 |
| `APPROVAL_HMAC_KEY` / `OTP_HMAC_KEY` / `FINGERPRINT_KEY` | W4/W5 用 |
| `DEMO_MODE` / `EVAL_MODE` | 互斥；非 B2 `GATEWAY_PROFILE` 只在 EVAL_MODE |
| `GATEWAY_PROFILE` | `B0/B1/B2/B2_NO_PREFLIGHT/B2_NO_WORKFLOW/B2_NO_TAINT`，默认 B2 |
| `GATEWAY_MAX_EXECUTIONS` | 并发执行上限，默认 8 |
| `BANK_BACKEND_URL` | L3 base URL，默认 `http://bank-backend:8300` |
| `JWT_PUBLIC_KEY_PATH` | EdDSA 公钥路径，默认 `/run/secrets/jwt-public.pem`；缺失/读不到拒绝启动 |

## 目录结构

```text
services/gateway/
├── alembic.ini
├── migrations/                 # 独立 Alembic；波次 1 期间 worker 不写迁移
│   ├── env.py                  # 读 GATEWAY_MIGRATION_DATABASE_URL
│   ├── schema_v1.json          # 从 app/tables.py 固化的 DDL（表拓扑序 + 索引排序）
│   └── versions/0001_gateway.py
└── app/
    ├── main.py                 # 双应用单进程入口 + 路由 include + 矩阵覆盖断言（仅 W0 改）
    ├── config.py               # Settings + 启动护栏
    ├── errors.py               # GatewayError + 统一错误体
    ├── middleware.py           # trace-id、404/405/500 统一错误、契约 details
    ├── db.py                   # engine / ready
    ├── tables.py               # gateway 库全部表（CHECK 由 schemas.enums 生成）
    ├── state.py                # 状态迁移唯一入口（legal 迁移表 + transition 校验）—— 只此一处改状态
    ├── idempotency.py          # request_dedup 持久幂等辅助（request_idempotent）
    ├── locks.py                # customer_lock（W4 实现、W5 复用）
    ├── schemas/                # 对外的 Pydantic 模型 + 枚举（仅 W0 改，改即接口变更）
    ├── policy/
    │   └── registry.py         # ToolPolicy + action_kind + outflow + 加载校验（结构仅 W0 改，W2 只改值）
    ├── approval/               # W4：service.create_for_evaluation / planview.build_view
    ├── evaluation/             # W3：repo.append_evaluation
    ├── execution/              # W4：replay（W3 实现、W4 调用）
    ├── auth/
    │   ├── tokens.py           # F/A/S/D 令牌 + 严格 EdDSA JWT 校验/测试签发（W1 扩展签发）
    │   ├── matrix.py           # 静态授权矩阵全表 + lookup + 覆盖断言（仅 W0/指挥改）
    │   └── deps.py             # resolve_caller / caller_dependency（解析 Caller、推导 origin、写 request.state.caller）
    ├── api/                    # 公共路由模块，各任务只改自己的
    │   ├── auth.py sessions.py          # W1（G1/G14/G22；G3/G15/G18–G22a）
    │   ├── tools.py                      # W2（G2）
    │   ├── plans.py                      # W3（G4）
    │   ├── execution.py approvals.py     # W4（G5/G8；G6/G7/G9/G10/G17）
    │   ├── notify_audit.py               # W5（G11/G12/G13/G16）
    │   └── demo.py                       # W5（D0–D12，仅 DEMO/EVAL 挂载）
    ├── internal/scheduled.py   # W5（S1）
    ├── l3/
    │   ├── client.py           # httpx 客户端（prepare 3s/execute 10s/其余 5s，可注入；L3NotDelivered/L3OutcomeUnknown/L3BusinessError）
    │   └── fake.py             # 有状态 fake L3：真实超时/丢包/commit_then_drop、令牌校验、intent 状态机、可编程快照与结果
    ├── audit/chain.py          # 审计哈希链 append / append_tx / verify（hash 覆盖 created_at_wall，时区规范到 UTC）
    └── pipeline/               # 评估流水线（仅 W0 改 __init__.py / context.py）
        ├── __init__.py         # 编排 + DENY 只短路 ⑤⑥⑦ + profile 开关
        ├── context.py          # EvalContext（每阶段一个结果槽 + steps + 惰性 L3 状态访问器）/ Finding / Stage
        └── {contract,entity,provenance,intent,snapshot,preflight,workflow,decision,audit}.py
```

`policies/tools.yaml` 在 `services/gateway/policies/`（注册表 `app/policy/registry.py` 读取并校验 14 个写工具）。

## 分工与共享规则（W1–W4 阅读）

- **阶段归属**：① contract、② entity、③ provenance、④ intent → **W2**；⑤ snapshot、⑥ preflight、⑦ workflow、⑧ decision、⑨ audit 阶段 + G4 组装 → **W3**；W1 生产原话/scope，W4 审批与执行。
- **不改的文件**：`pipeline/__init__.py`、`pipeline/context.py`、`schemas/**`、`auth/matrix.py`、`state.py`、`tables.py`、`migrations/**`、`main.py`、`policy/registry.py` 的结构、`policies/tools.yaml` 的结构、`pyproject.toml`、`uv.lock`、compose。需要改就写「待决」由指挥统一改。
- **工具注册表**：`policies/tools.yaml` 的结构与 14 个工具的集合由 W0 锁定，W2 之后只能提交值的修正。`action_kind` 对找不到的卡 fail-closed（抛 `LookupError`）；对外转出口径用 `registry.outflow(tool, args, shadow_state)`（只有 TRANSFER_EXTERNAL 与目标为他人的 schedule_transfer 计，申购/赎回不计）。
- **状态变更**只能调 `state.transition`（会校验合法迁移表，非法边直接 `ValueError`）；**request_id 接口**只能用 `app.idempotency.request_idempotent`（进行中返回 409 `EXECUTION_IN_PROGRESS`，produce 抛异常清进行中行）；错误只能 `raise GatewayError(Code.X)`；调 L3 只能用 `L3Client`（测试用 fake L3 的 `transport()`）；审计只能用 `audit.append_tx`（不要在含 HTTP 调用的事务里 append）。
- **交叉接口占位**（签名已定，实现方标注在 docstring）：`approval.service.create_for_evaluation`（W4）、`approval.planview.build_view`（W4）、`evaluation.repo.append_evaluation`（W3）、`locks.customer_lock`（W4）、`execution.replay.replay`（W3 实现、W4 调用）。
- **波次 1 不写迁移**：需要改表写进「待决」，由指挥在集成分支串行加 `0002_wave1`。
- **② 实体阶段**用 `ctx.bank_state()`（惰性、带缓存的 L3 快照，带计划步骤）核对所有权并写 `ctx.steps`；⑤ 只钉住 versions/clock_version，不重复拉取。

## 流水线插件接口

- 阶段按 §7.2 ①–⑨ 顺序编排；DENY 后只短路 ⑤ snapshot/⑥ preflight/⑦ workflow，⑧ decision 与 ⑨ audit 恒运行（`always_run=True`）。E 类错误（如契约 E422）`raise GatewayError`，不写成 finding。
- `EvalContext` 每阶段一个结果槽：`identity`/`authorization`（W1）、`provenance`/`intent`（W2）、`snapshot`/`preflight`/`workflow`/`decision`（W3），加上 `mode`、`profile`、`wall_now`、`conn`、`l3`、`stage_status`。`Finding` 即 `schemas.FindingView`（effect 枚举为 `FindingEffect`）。
- `GATEWAY_PROFILE` 消融通过 `Stage.enabled(profile)` 关闭阶段；`evaluate(ctx, mode=...)` 支持 EVALUATE / EXECUTE_REPLAY / SHADOW_B1（SHADOW_B1 不写库、不审计）。

## 待决（给指挥）

- `BANK_BACKEND_URL` 与 `ExecutionJobStatus`/`WorkflowEventStatus` 的取值请补记 PRD §11.1 / §7.2。
- `requirements.txt`/`requirements-test.txt` 已按锁重新导出，`.wheels` 已生成，镜像可起。
- `gateway_admin` 尚无 LOGIN 凭证（reset 用），W5 需要补；`ALTER DEFAULT PRIVILEGES … ON SEQUENCES` 已在 0001 里补上，但对已跑过 0001 的旧卷不会重放，fresh `make up` 会带上。
- 公共路由已挂全局 `caller_dependency`：非 `/health` 路由不带凭证一律 401（默认拒绝），处理函数经 `request.state.caller` 取 `Caller`。

## 集中运行资源与秘密文件（S1）

生产入口 `serve` 在启动两个监听器前进入一次 `app.runtime.gateway_runtime`，统一创建 AsyncEngine、L3Client 和 UTC wall clock；两应用共享 `app.state.db/l3/wall_now/settings`。只在两个监听器都停止后关闭资源，启动中途出错也释放已创建的资源。处理器不得另建池。单独构造 app 的单元测试仍兼容原签名；生产资源由入口负责注入。

`JWT_PRIVATE_KEY_PATH` 默认 `/run/secrets/jwt-private.pem`；私钥必须是普通 0600 文件，拒绝符号链接，且必须与公钥构成 Ed25519 密钥对。`DEMO_PINS_FILE` 默认 `/run/secrets/demo_pins`，PIN 解析与哈希由 W1 `auth.pin.configured_hashes()` 提供，集中加载后放入 `app.state.pin_hashes`。W1 模块尚未合并时该资源明确未配置；S1 不提供默认 PIN 或成功替身。W1 加载函数存在但读文件失败时拒绝启动。

`caller_dependency` 在验证 F/A 客户 JWT 后调用 W1 `tokens.check_revocation(conn,caller,now=...)`；DB、时钟或检查函数缺失均拒绝（503），撤销/过期/缺行错误由 W1 决定并传递。S/F/D 的纯服务凭证及控制台凭证按原授权矩阵处理。真实登录→会话→撤销验收须在 W1 合并后补跑；S1 的签名替身测试只证明依赖接线。

Compose 的 gateway 使用短暂 root 引导进程 `scripts.gateway_entrypoint` 读取 `/run/bootstrap` 中只读挂载的宿主 0600 私钥和 demo_pins。它只将两文件复制到 `/run/gateway-private` 私有 tmpfs（目录 0700、文件 0600、属主 UID/GID 10001），设置进程内 JWT_PRIVATE_KEY_PATH/DEMO_PINS_FILE 后清空附加组并降到 UID/GID 10001，再 exec gateway。最终应用无 effective/permitted capabilities，保留 no-new-privileges；宿主文件权限不变。

引导仅增加 CHOWN（赋予副本所有权）、DAC_OVERRIDE（读取不同宿主 UID 的 0600 文件）、SETUID/SETGID（永久降权）四项 capability；其他服务仍保持原用户及 cap_drop ALL。私钥与 PIN 不进入镜像或日志，源挂载不可写。运行目录不是独立 tmpfs、缺文件或权限错误均拒绝启动。

```bash
make test-shared-runtime # 明确启用真实临时PG与独立Linux秘密权限验收
```

该目标要求 Docker 后台可用，使用已有 `safebank-p0:local` 为 Linux 验收基底，只构建独立随机 tag，既不替换现有镜像 tag，也不启动/停止 compose 栈。临时 PG 的 PGDATA 与秘密验收均显式使用 tmpfs，不分配 Docker 数据卷。

W4 锁服务的集中初始化接口仍待 W4 定义（建议 `locks.configure(db,max_executions=...)`），S1 未伪造该实现。
