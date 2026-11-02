# L3 · Banking Backend

FastAPI + PostgreSQL 合成银行后端，提供账户、账本、收款人、银行卡、定时指令、订阅、AA 收款、风险测评与基金业务。服务只在 `bank-net:8300` 监听，不发布宿主端口，所有路由包括健康探针均校验网关服务令牌。

## 状态与执行

- 18 个读工具通过 `POST /internal/read/{tool}` 提供，返回值遵循共享契约。
- `POST /internal/snapshot` 在只读一致性事务中加载银行状态，供网关预演。
- `prepare / execute / reconcile` 管理执行身份、版本与未知结果恢复。
- 业务效果、客户版本、执行结果、操作事件与 outbox 在同一事务中提交。
- 到期任务通过内部网络请求 L2 授权；模拟时钟支持定时转账、代扣、基金确认与结算。

金融规则由 [`bankcore`](../../packages/bankcore/README.md) 提供，与 L2 预演共用。金额只用整数分，份额用四位 Decimal；运行角色无 DDL / TRUNCATE 权限，迁移权限只进入维护容器。

## 启动与数据

从根目录按[部署指南](../../docs/deployment.md)操作。`make up` 执行迁移并安装 `p1_core_v1` 基础测试客户，`make seed-demo` 增量安装四个演示客户与合成历史。种子重跑保留既有业务状态，不等于重置。

既有定时与代扣条款需在首次使用前通过维护 CLI 导出并安装到空网关库。该步骤与普通用户审批分开，不生成虚假的执行或 OTP 记录。

| 变量 | 用途 |
|---|---|
| `BANK_DATABASE_URL` | `bank_app` 运行角色数据库连接 |
| `GATEWAY_SERVICE_TOKEN` | 内部业务接口认证 |
| `SCHEDULER_SERVICE_TOKEN` | 到期事件调用 L2 |
| `S1_URL` | 内部授权地址，Compose 为 `http://gateway:8201` |
| `SCHEDULER_ENABLED` | 是否启用调度器 |
| `FINGERPRINT_KEY` | 账户指纹 HMAC |
| `DEMO_MODE` / `EVAL_MODE` | 挂载受控演示接口 |
| `BANK_MIGRATION_DATABASE_URL` | 仅维护容器使用的管理员连接 |
| `P1_TEST_DATABASE_URL` | 独立 `bank_p1_test` 测试库 |

## 代码入口

| 路径 | 内容 |
|---|---|
| `app/state.py` | 统一状态加载与效果持久化 |
| `app/reads.py` | 业务读工具与结果适配 |
| `app/main.py` | 服务装配、内部路由与就绪探针 |
| `app/scheduler.py` / `app/events.py` | 到期事件、状态与调度 |
| `app/seed_demo.py` / `app/seed_consents.py` | 增量合成数据与既有条款导出 |
| `fixtures/l1_seed_v1.json` | 固定演示数据 |
| `migrations/` | 前向 Alembic 迁移 |

## 测试

```bash
make test
make test-postgres-bootstrap
make test-obligations
make test-p1
make test-p1-concurrency
make test-p1-consistency
make contracts-check
```

并发与一致性目标先运行 `make test-p1`。`make test-integration` 针对当前部署检查真实网络、HTTP 与重启恢复，会改变测试客户状态并重启服务，只在专用部署检出执行。数据卷不因排错而删除。

已完成的真实场景与尚待完整彩排等范围见[验证说明](../../docs/validation.md)。
