# W0 进度：L2 Gateway 基础骨架

分支 `feat/l2-foundation`（git worktree）。执行者：Claude Code（DeepSeek V4 Pro）。日期 2026-10-23。

## 交付内容

全部落在 `services/gateway/**` 与 `tests/gateway/**`，外加仅追加的 `Makefile`、只改 gateway 服务的 `docker-compose.yml`、只加依赖的 `pyproject.toml`/`uv.lock`、`docs/third-party.md`。

1. **双应用单进程**：`public_app`（:8200，无 `/internal/*`，`GET /health`）与 `internal_app`（:8201，只有 `/internal/*`，S 令牌；`GET /internal/health` + `POST /internal/scheduled/authorize` W5 占位 503）。一个 asyncio 进程两个 `uvicorn.Server`，绑定 `GATEWAY_PUBLIC_BIND` / `GATEWAY_INTERNAL_BIND`。
2. **配置与启动护栏**：DEMO_MODE/EVAL_MODE 互斥；非 B2 profile 必须 EVAL_MODE；profile 白名单；`DEMO_CONSOLE_TOKEN` 存在时 < 32 字节拒绝；两个绑定地址必须不同且非 `0.0.0.0`。
3. **gateway 库迁移**：Alembic 独立目录 `services/gateway/migrations/`。PRD §7.2 全部 18 张表（sessions、utterances、read_records、pending_clarifications、form_actions、hints、plans、evaluations、approvals、otp_challenges、authorization_claims、execution_jobs、scheduled_grants、event_decisions、workflow_events、cooldowns、notifications、audit_events），状态枚举 CHECK、部分唯一索引（每 sid 一个 OPEN clarification / PENDING approval、每 plan 一个可用 approval、`UNIQUE(customer_id,prev_hash)`、`UNIQUE(event_id,type)`、`UNIQUE(customer_id,origin,request_id)`）。`audit_events` 增加 `seq`（`audit_sequence`）作哈希链单调序。角色：`gateway_app` 对 `audit_events` 只有 INSERT/SELECT（其余表 SELECT/INSERT/UPDATE/DELETE）。
4. **统一错误与请求约定**：`{error:{code,message,details,trace_id}}`，`X-Request-ID` 回显，code 只来自 `safebank_contracts.codes.Code`（无需新增，故 contracts 未动）；`IdempotencyStore`（同 ID 同体回原结果、不同体 409 `IDEMPOTENCY_CONFLICT`）。
5. **凭证中间件骨架**：F/A/S/D 服务令牌常量时间比较；`verify_jwt`（EdDSA，alg/iss/aud/exp/nbf/jti/sub/sid/scope）与测试用 `sign_jwt`；`auth/matrix.py` 静态矩阵格式 + `lookup`，登记 `/health`（开放）与 S1（S）两条；`auth/deps.py` 提供依赖。
6. **L3 客户端与 fake L3**：httpx 客户端（prepare 3s / execute 10s / 其余 5s，`GATEWAY_SERVICE_TOKEN`，超时→`L3Timeout`、连接失败→`L3Unavailable`）；`FakeL3` 按 `docs/contracts/v1.json`（jsonschema）校验请求/响应形状，可编程 `ok/state_changed/timeout/drop`。
7. **审计哈希链**：`audit/chain.py` 追加（`pg_advisory_xact_lock(hashtext('audit'), hashtext(customer_id))` → 读 prev_hash → 插 `hash=sha256(prev_hash||canonical(event))`）与整链校验；`UNIQUE(customer_id,prev_hash)` 兜底。
8. **流水线插件接口**：`pipeline/context.py`（`EvalContext`/`Finding`/`Effect`/`Resolver`/`Stage`）、9 个阶段各一个占位文件、`pipeline/__init__.py` 编排 + DENY 短路（audit 恒跑）。
9. **测试**：`make test-gateway`（40 项单元）、`make test-gateway-db`（唯一命名/随机端口临时 Postgres 16 + 迁移两次 + 4 项 DB 测试）。
10. **依赖**：新增 `pyjwt==2.15.1`、`argon2-cffi==25.1.0`（+bindings 26.1.0）、`pypinyin==0.55.0`，全部 MIT，`docs/third-party.md` 登记。
11. **compose**：gateway 服务仅加 `command: [python, -m, services.gateway.app.main]`，未动其他服务、未破坏 P1 目标。

## 文件清单

- 新增：`services/gateway/__init__.py`、`app/**`（config/errors/middleware/db/models/main + auth/ l3/ audit/ pipeline/ 共 24 个 py）、`alembic.ini`、`migrations/{env.py,schema_v1.json,versions/0001_gateway.py}`、`tests/gateway/**`（9 个单测文件 + `db/conftest.py` + 2 个 DB 测试）、`docs/progress/W0-foundation.md`。
- 改写：`services/gateway/README.md`。
- 追加/微改：`Makefile`（末尾两个目标）、`docker-compose.yml`（gateway command）、`pyproject.toml`/`uv.lock`（依赖）、`docs/third-party.md`。

## 退出命令实测结果

| 命令 | 结果 |
|---|---|
| `make lint` | 通过（ruff check + ruff format --check，106 文件） |
| `make test-gateway` | 40 passed |
| `make test-gateway-db` | 4 passed（临时 Postgres 起→迁移 upgrade head 两次→断言→销毁） |
| `make test` | 156 passed, 37 skipped（33 个 P1 集成 + 4 个 gateway DB 因无 DB 环境 skip；P1 116 单测未被破坏） |

DB 测试覆盖：空库 `upgrade head` 两次成功且 `alembic_version='0001_gateway'`；`audit_events` tableowner=postgres；运行角色可 INSERT/SELECT、UPDATE/DELETE 报 `DBAPIError`；10 路并发追加不分叉（`verify_chain` 通过）；管理员篡改 payload 后 `verify_chain` 检出。

`make up` 未跑（L2-plan §2.4：全栈只由指挥在集成分支串行跑）。

## 待决

1. **gateway 迁移引导接线**：`scripts/keys.py` 把 `gateway` 库 owner 设为 `gateway_app`，而 0001 迁移按 bank-backend 模式需以 postgres 运行后授予 `gateway_app`。需仿 `scripts/p1_database.py` 做 gateway 引导（`ALTER DATABASE gateway OWNER TO postgres` + `gateway-migration.env`），在 `scripts/**`，不在 W0 范围。W0 的 `make test-gateway-db` 已按正确角色分工在临时库验证。
2. **运行镜像依赖**：`requirements.txt`/`.wheels` 未重生成（PyJWT/argon2/pypinyin 已进 pyproject+uv.lock，本地 .venv 可用）。Docker 镜像需 `uv export` 重导 requirements + `make wheels`，由指挥在集成分支做。
3. **状态枚举**：只对 PRD §7.2 明确列出的枚举写 CHECK；`execution_jobs.status`、`workflow_events.status` 等未列枚举的字段保持无 CHECK 字符串。
4. **`BANK_BACKEND_URL`**：PRD §11.1 未列 L3 base URL 的环境变量名，W0 采用 `BANK_BACKEND_URL`（默认 `http://bank-backend:8300`），已写 README。如需更名请在 W1 前定。
5. **import 路径**：gateway 包名为 `services.gateway.app`（目录 `services/gateway/app/`），不是顶层 `app`，以避免与 bank-backend 的 `app` 在共享 PYTHONPATH / `make test` 单进程里冲突。W1–W4 的接口说明按此路径；指挥据此下发后续任务书。
6. **S1 占位**：`/internal/scheduled/authorize` 已注册（S 令牌，矩阵已登记），返回 503 `DEPENDENCY_UNAVAILABLE`，W5 实现。

## 给 W1–W4 的接口说明要点

- 只改各自文件；`pipeline/__init__.py`、`context.py`、`auth/matrix.py` 的既有签名不要动（改动先写进度并和指挥确认）。
- W1（身份/原话）：用 `auth/tokens.py` 的 `verify_jwt`/`sign_jwt`（测试签发）+ `auth/matrix.py` 全量登记；PIN 用 argon2-cffi，近音用 pypinyin。
- W2（读代理/来源/污点）：实现 `pipeline/entity.py`、`provenance.py`、`intent.py`；读代理调 `app/l3/client.py`，测试用 `app/l3/fake.py`。
- W3（预演/工作流/决策）：实现 `pipeline/snapshot.py`、`preflight.py`、`workflow.py`、`decision.py`、`contract.py`（G4 组装）；预演只调 `packages/bankcore` 纯函数。
- W4（审批/执行）：实现 L3 prepare/execute/reconcile 编排（用 `app/l3/client.py`）；`IdempotencyStore` 语义最终落到 `plans`/`evaluations` 表。
- W5（定时/通知/审计/D）：实现 `pipeline/audit.py` 调用 `app/audit/chain.py`；S1 占位替换为真实现。
