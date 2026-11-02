# L2 · Security Gateway

网关是助手、表单与银行后端之间的业务入口。它独立验证授权来源，预演金融状态，检查跨步骤与跨会话风险，并负责审批、OTP、执行恢复和审计。授权规则不依赖 LLM 判断。

## 评估与执行

```text
契约 → 实体 → 来源与污点 → 意图 → 快照 → 累积预演 → 工作流 → 决策 → 审计
```

结果为 `ALLOW`、`REQUIRE_APPROVAL`、`REPLAN` 或 `DENY`。工具基础风险与 ActionKind 来自 [`policies/tools.yaml`](policies/tools.yaml)；金额、目标和派生值要通过来源与意图范围检查。

确认面板与 `view_hash` 由 L2 生成，审批绑定具体计划和风险内容。执行时再次复演，状态变化不能沿用不相符的审批。表单步骤只从持久化 action 重建，并验证会话、有效期和授权代际。未知执行结果通过 L3 对账恢复。

定时转账、商户代扣与基金事件通过内部回调检查当前授权与业务状态；审计哈希链记录决策与执行事件。完整说明见[架构文档](../../docs/architecture.md)。

## 运行边界

一个进程启动两个应用，共用受控运行资源：

| 应用 | 监听位置 | 范围 |
|---|---|---|
| public | `agent-net:8200` | App / Agent 业务接口；不注册 `/internal/*` |
| internal | `bank-net:8201` | 调度回调；不注册公共接口 |

路由授权矩阵在启动时检查覆盖，调用方身份决定 `origin`。L1 不能自称表单来源。BFF、Agent、scheduler 与控制台使用独立凭证；只有网关运行路径使用 L3 业务调用令牌。

包导入名为 `services.gateway.app`，避免与银行后端的 `app` 同名。

## 启动与环境变量

根目录启动步骤见[部署指南](../../docs/deployment.md)。迁移由独立 `gateway-maintenance` 容器执行。配置集中于 [`app/config.py`](app/config.py)。

| 变量 | 用途 |
|---|---|
| `GATEWAY_DATABASE_URL` | 网关运行角色数据库连接 |
| `GATEWAY_PUBLIC_BIND` / `GATEWAY_INTERNAL_BIND` | 两个不同网络的绑定地址 |
| `GATEWAY_SERVICE_TOKEN` | 网关调用 L3 |
| `FRONTEND_SERVICE_TOKEN` / `AGENT_SERVICE_TOKEN` / `SCHEDULER_SERVICE_TOKEN` | F / A / S 调用身份 |
| `DEMO_CONSOLE_TOKEN` | 独立演示控制台身份 |
| `APPROVAL_HMAC_KEY` / `OTP_HMAC_KEY` | 独立审批与验证码密钥，至少 32 字节且互异 |
| `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` | Ed25519 签验密钥 |
| `DEMO_PINS_FILE` | 本机生成的合成客户 PIN |
| `BANK_BACKEND_URL` | L3 地址，默认 `http://bank-backend:8300` |
| `DEMO_MODE` / `EVAL_MODE` | 互斥；演示与评测接口仅在对应模式挂载 |
| `GATEWAY_PROFILE` | 默认完整 B2；非 B2 只允许 EVAL 模式 |
| `GATEWAY_MAX_EXECUTIONS` | 执行并发上限，默认 8 |

Compose 用短暂引导进程将只读宿主秘密文件复制至私有 tmpfs，再永久降权到 UID/GID 10001。宿主私钥与 PIN 保持 0600；最终应用不以 root 执行。资源、密钥或撤销校验缺失时拒绝继续。

## 代码入口

| 路径 | 内容 |
|---|---|
| `app/pipeline/` | 九阶段评估与执行复演 |
| `app/utterance/` | 原话登记、解析、范围与生命周期 |
| `app/approval/` | 确认面板、审批绑定、OTP |
| `app/execution/` | 执行、状态变化屏障与恢复 |
| `app/auth/` | 凭证、JWT、PIN、授权矩阵与撤销检查 |
| `app/internal/scheduled.py` | 到期业务授权与终态处理 |
| `app/audit/` | 哈希链与校验 |
| `app/runtime.py` | 双应用共享运行资源 |
| `migrations/` | 前向 Alembic 迁移与 DDL 快照 |

## 测试

```bash
make test-gateway
make test-gateway-db
make test-shared-runtime
make test-w1
make test-w2
make test-w3b
make test-w4b1
make test-w4b2b
make test-w4b2c
make test-w4c
make test-s1
```

数据库与秘密权限目标需要 Docker，使用独立临时环境。完整 CI 列表见 [`checks.yml`](../../.github/workflows/checks.yml)，已有实测与未覆盖项见[验证说明](../../docs/validation.md)。
