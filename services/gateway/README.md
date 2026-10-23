# gateway（L2 Security Gateway）

三明治架构的中间层：L1（agent）的所有读工具、评估与执行都经这里进入 L3。L2 是唯一持有 `GATEWAY_SERVICE_TOKEN` 的一方，也是唯一能写 L3 的一方。本目录是 W0 打下的基础骨架，W1–W4 在此之上并行填业务。

> 注意 import 路径：本服务包名是 `services.gateway.app`（目录 `services/gateway/app/`），不是顶层 `app`。仓库共享 PYTHONPATH 里已有 bank-backend 的 `app` 包，为避免同名冲突，gateway 一律用 `services.gateway.app.*` 导入；包内用相对导入（`from .config import ...`）。

## 启动

```bash
make keys && make up          # 编排由根目录 Makefile 负责
# 或单独跑进程（需要两个绑定地址不同）：
GATEWAY_PUBLIC_BIND=127.0.0.1 GATEWAY_INTERNAL_BIND=127.0.0.2 \
  .venv/bin/python -m services.gateway.app.main
```

一个 asyncio 进程跑两个 `uvicorn.Server`：

- `public_app`：`:8200`，只有公共路由（`GET /health`），**绝不注册 `/internal/*`**。
- `internal_app`：`:8201`，**只有 `/internal/*`**（S 服务令牌），供 scheduler 回调。

## 测试

```bash
make test-gateway        # 单元测试（双端口隔离、启动护栏、错误/幂等、JWT、矩阵、fake L3、流水线）
make test-gateway-db     # 起唯一命名/随机端口的临时 Postgres 16，跑迁移与 DB 测试，测完销毁
```

`make test-gateway-db` 内部设置 `GATEWAY_DB_TESTS=1`；其余 pytest 调用会跳过 DB 测试（见 `tests/gateway/db/conftest.py`）。多个 worker 可并发跑 `test-gateway-db`，互不冲突（无固定子网、无固定容器名、随机宿主端口）。

## 环境变量（gateway.env）

| 变量 | 说明 |
|---|---|
| `GATEWAY_DATABASE_URL` | 运行角色 `gateway_app` 连 `gateway` 库 |
| `GATEWAY_PUBLIC_BIND` / `GATEWAY_INTERNAL_BIND` | 两个端口的绑定地址（必须不同、不能 `0.0.0.0`） |
| `GATEWAY_SERVICE_TOKEN` | L2 → L3 的令牌（L3 校验） |
| `FRONTEND_SERVICE_TOKEN` / `AGENT_SERVICE_TOKEN` / `SCHEDULER_SERVICE_TOKEN` / `DEMO_CONSOLE_TOKEN` | F / A / S / D 四类服务令牌（常量时间比较） |
| `APPROVAL_HMAC_KEY` / `OTP_HMAC_KEY` / `FINGERPRINT_KEY` | W4 / W5 用 |
| `DEMO_MODE` / `EVAL_MODE` | 互斥；非 B2 `GATEWAY_PROFILE` 只在 EVAL_MODE 下生效 |
| `GATEWAY_PROFILE` | `B0`/`B1`/`B2`/`B2_NO_PREFLIGHT`/`B2_NO_WORKFLOW`/`B2_NO_TAINT`，默认 `B2` |
| `GATEWAY_MAX_EXECUTIONS` | 并发执行上限，默认 8 |
| `BANK_BACKEND_URL` | L3 base URL，默认 `http://bank-backend:8300` |

## 目录结构

```text
services/gateway/
├── alembic.ini
├── migrations/                 # 独立于 bank-backend 的 Alembic 迁移
│   ├── env.py                  # 读 GATEWAY_MIGRATION_DATABASE_URL，import services.gateway.app.*
│   ├── schema_v1.json          # 从 app/models.py 固化的 DDL（历史迁移只读它）
│   └── versions/0001_gateway.py
└── app/
    ├── main.py                 # 双应用单进程入口
    ├── config.py               # Settings + 启动护栏（PRD §7.3/§10.1/§11.1）
    ├── errors.py               # GatewayError + 统一错误体
    ├── middleware.py           # trace-id、异常处理、request_id 幂等工具
    ├── db.py                   # engine / ready
    ├── models.py               # gateway 库全部表（PRD §7.2）
    ├── auth/
    │   ├── tokens.py           # F/A/S/D 服务令牌 + EdDSA JWT 校验接口/测试签发
    │   ├── matrix.py           # 静态授权矩阵数据格式 + lookup
    │   └── deps.py             # FastAPI 依赖（服务令牌 / JWT / 矩阵守卫）
    ├── l3/
    │   ├── client.py           # httpx 客户端（prepare 3s / execute 10s / 其余 5s）
    │   └── fake.py             # fake L3：按 docs/contracts/v1.json 校验，可编程 ok/state_changed/timeout/drop
    ├── audit/
    │   └── chain.py            # 每客户审计哈希链追加/校验（审计 advisory lock）
    └── pipeline/               # 评估流水线插件接口（W2/W3 只改各自文件）
        ├── __init__.py         # 编排 + 短路（仅 W0 改）
        ├── context.py          # EvalContext / Finding / Effect / Resolver / Stage
        └── {contract,entity,provenance,intent,snapshot,preflight,workflow,decision,audit}.py
```

## 流水线插件接口（W2 / W3 / W5 阅读）

- 九个阶段按 PRD §7.2 ①–⑨ 顺序编排：`contract → entity → provenance → intent → snapshot → preflight → workflow → decision → audit`。
- 每个阶段一个文件，各暴露一个 `stage`（`Stage` 实例，实现 `async def run(self, ctx: EvalContext) -> None`）。W2 只改 `entity.py` / `provenance.py` / `intent.py`；W3 只改 `snapshot.py` / `preflight.py` / `workflow.py` / `decision.py`；`contract.py` 归 G4 组装（W3），`audit.py` 归 W5。**不要改 `pipeline/__init__.py` 与 `context.py` 的既有签名**，除非在 `docs/progress` 登记并和指挥确认。
- `EvalContext` 字段：`request`、`caller`（F/A/S/D）、`origin`、`customer_id`、`session_id`、`plan_draft`、`plan_id`、`snapshot`（占位，由 ⑤ 填）、`findings`、`audit`（审计句柄）、`short_circuit`。
- `Finding`：`code`（`safebank_contracts.codes.Code`）、`effect`（`Effect`）、`step_id`、`resolver`（仅 REPLAN）、`message`、`evidence_refs`。
- 短路规则：任一阶段出现 `Effect.DENY` finding 后，其余阶段跳过，`audit` 恒运行；REPLAN/升黄红/INFO 不短路。阶段通过 `ctx.add_finding(...)` 累积结论。

## 待决（给指挥）

- `gateway` 库的迁移/引导接线：`scripts/keys.py` 目前把 `gateway` 库 owner 设为 `gateway_app`，而 0001 迁移按 bank-backend 的模式以 postgres（owner）运行、再授予 `gateway_app`。需要一次与 `scripts/p1_database.py` 类似的 gateway 引导（`ALTER DATABASE gateway OWNER TO postgres` + `gateway-migration.env`），属于 `scripts/**`，不在 W0 允许范围。
- `requirements.txt` / `.wheels/` 未重新生成：新增 PyJWT/argon2-cffi/pypinyin 已进 `pyproject.toml` + `uv.lock`，本地 `.venv` 可用；Docker 运行镜像需要 `uv export` 重新导出 requirements 并 `make wheels`，由指挥在集成分支做。
- 状态枚举只按 PRD §7.2 明确列出的写 CHECK；`execution_jobs.status`、`workflow_events.status` 等 PRD 未列枚举的字段保持无 CHECK 字符串。
