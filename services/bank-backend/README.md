# bank-backend：P1 核心

真实 FastAPI + PostgreSQL 后端，仅在 `bank-net:8300` 提供服务；所有已注册路由（含 health/readyz）需要 `X-Service-Token: GATEWAY_SERVICE_TOKEN`。不向宿主发布端口。L1 无服务令牌，也不能连接该网络。

从项目根目录执行 `make setup && make up`。该命令创建本地隔离凭证、构建镜像、启动数据库、执行 Alembic 迁移、幂等加载 `p1_core_v1` 核心测试数据后启动服务；重复运行保留已有数据。需要运行中的后台 Docker 引擎，不要求打开 Desktop 窗口。已有银行数据不自动重置。

P1 核心数据是甲/乙两个合成客户（cus_a/cus_b），账户 acc_a=600000 分、acc_s=2000000 分、acc_b=200000 分，卡 crd_a。本数据不是 PRD 的正式 baseline_v1。真实 HTTP 验收会做一笔 1 分本人互转，重复验收累计该变化。

已实现：

- `POST /internal/read/{tool}`：get_accounts、list_payees、get_transactions、get_cards、get_profile。
- `POST /internal/snapshot`：唯一状态加载器，REPEATABLE READ + READ ONLY。
- `POST /internal/plans/prepare`、`/execute`，`GET /internal/executions/{plan_id}?customer_id=`，`POST /internal/executions/{plan_id}/reconcile`。
- `GET /internal/outbox?after=&limit=`、`POST /internal/outbox/ack`。每轮从 after=0 扫描未 ack 项，按 event_id 幂等处理再 ack；after 只用于本轮分页，不能当永久提交水位。
- 转账（本人、本行、他行）、新增收款人、卡限额/渠道/冻结/解冻/挂失补卡。其他业务工具明确返回 TOOL_NOT_ALLOWED；调度、AA、订阅、基金和时钟推进属于后续阶段。

执行只接受已 prepare 且 attempt/证据/请求身份匹配的信封。锁序为 intent → clock FOR SHARE → 排序后的客户版本；引用摘要在锁内复核。全部业务效果、客户版本、执行结果、操作事件及 outbox 同事务提交。404、PREPARED 或超时都不能推断失败；通过 reconcile 获得 COMMITTED/CANCELLED，后者永久阻断迟到请求。

L2 的授权/审批/OTP 仍属于 P2/P3。当前 HTTP 验收信封仅由可信本地测试驱动生成，没有给 L1 增加入口。金额/份额和工具 JSON Schema 以 `packages/contracts` 为准，执行领域规则与预演共享 `packages/bankcore`。

环境变量：

| 变量 | 用途 |
|---|---|
| BANK_DATABASE_URL | bank_app 运行角色，仅 DML；只能使用 PostgreSQL + asyncpg |
| GATEWAY_SERVICE_TOKEN | 所有 L3 路由认证 |
| FINGERPRINT_KEY | 账户指纹 HMAC |
| BANK_MIGRATION_DATABASE_URL | 仅临时维护/迁移容器持有的管理员连接；不注入银行服务 |
| P1_TEST_DATABASE_URL | 仅测试驱动持有；必须指向独立 bank_p1_test |

验收命令：

```sh
make test                 # 本地单元；未配置真实 PG 的集成项显示 skipped
make lint
make test-postgres-bootstrap # 独立Linux新卷初始化/权限/重启回归
make test-p1              # 重建独立测试库、空库迁移、重复升级、全部真实 PG 验收
make test-p1-concurrency  # 在 test-p1 构建后独立运行
make test-p1-consistency
make test-integration     # 正在运行的真实服务：网络、HTTP、L3 重启恢复
make contracts-check
```

测试库清理仅针对固定名称 bank_p1_test；运行角色无 DDL/TRUNCATE。迁移 0001 固化 DDL，0002 分离请求与解析后摘要；0002 对非空旧执行表拒绝猜测迁移。迁移仅前进，不回退删除账本。

`make demo/eval/reset` 仍明确失败，等待后续完整演示、评测与正式种子数据实现。CI 配置见 `.github/workflows/checks.yml`；本地验收证据见 `docs/acceptance/P1.*.md`。

Linux 全新部署通过 scripts/postgres-entrypoint.sh 适配宿主0600 SQL的所有者；启动副本位于容器私有tmpfs（目录0700/文件0600），再运行官方entrypoint。原始只读挂载和宿主密钥权限不变，正式bank卷不为修复CI而删除。
