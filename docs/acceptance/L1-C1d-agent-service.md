# C1d 验收：L1 真实 Postgres checkpoint、线程锁、服务入口与重启恢复（2026-10-25）

- 分支 `codex/c1d-agent-service`，基线 main 1ce15f0，之后同步 main 72a4161。
- 任务书：`docs/plans/briefs/C1d-agent-service.md`，以末尾的协调者裁定 D1–D10 为准。设计笔记：`C1d-design-notes.md`。
- 实现：本机 Claude Code CLI 的 DeepSeek worker，SID 4ccd5c8d。Task 0–7 实现完成后，又做了 5 轮修复。
- 独立评审：DeepSeek 只读 worker，SID 22142881，报告见 `docs/reviews/C1d-agent-service-review.md`。首轮 APPROVE（P2×4），修复轮 5 复审 APPROVE。

## 范围

- **checkpoint**：使用 `AsyncPostgresSaver`（postgres-agent 库）。checkpoints、checkpoint_writes、checkpoint_blobs 三张表里都没有任何令牌或密钥。
- **线程锁**：按 thread 加 `pg_try_advisory_lock`，并发请求同一 thread 时返回 409；旧 interrupt 会被新输入替代（E7 已在真实 PG 上验证）。
- **HTTP/SSE 服务**（PRD §6.5）：保留 `/health`。生产路径组装真实端口，即 C1b DeepSeek 适配器、C1c1 GatewayClient 与 C1c2 路由；fake 只能由测试显式注入（D9）。
- **凭证**：agent 只用 `AGENT_SERVICE_TOKEN` 作 A 身份，L2 地址取自 `GATEWAY_URL`（默认 `http://gateway:8200`），任何路径都不读取 `GATEWAY_SERVICE_TOKEN`（D10，已登记 PRD §11.1）。
- **重启恢复**：用新建的 saver/graph 实例，从同一个 PG checkpoint 恢复。
- **依赖（D3）**：langgraph、langchain、langchain-deepseek、langgraph-checkpoint-postgres、psycopg 及其传递依赖从 `poc` 组移入主依赖，版本与原 uv.lock 一致，没有新增包。requirements.txt、wheels、third-party 已同步。
- **测试与 CI**：新增 `make test-agent-db`（真实 tmpfs PG，31 项，零 skip），已加入 CI p1-postgres。

## 协调者亲自核验（e5edf16 / 5f88a8c）

| 命令 | 结果 |
|---|---|
| make setup / lint | exit 0 / 304 files |
| make test | 2499 passed / 591 skipped |
| .venv/bin/pytest tests/agent -q | 288 passed / 30 skipped（DB 项默认 skip） |
| make test-agent-db | 31 passed |
| make contracts-check | PASS |
| make test-gateway / test-gateway-db | 1982/501 skip / 17 |
| make wheels | exit 0 |
| actionlint | exit 0 |
| make demo | exit 2（未实现，如实登记） |

红绿核验：
- D10：把 main.py 换回 249ca98 后，对照、实验、源码扫描 3 条测试全部失败；还原后通过。
- 并发锁：把 `pg_try_advisory_lock` 改为恒真后，测试失败；还原后通过。

过程中被协调者驳回的问题：
- 生产路径使用 fake 端口且默认 ALLOW（D9）；
- 读取 `GATEWAY_SERVICE_TOKEN`，违反凭证隔离（D10）；
- 两轮「红测」在旧代码上并不失败，或未实际运行；
- 自报的测试数（266）与实测不符；
- 误提交 .bak 文件。

最终提交前以上问题均已修正。

## 已知问题与未覆盖

- 已知问题：
  - P2-3：`GET /state` 为读取 saver 而编译整张图；
  - P2-4：首次请求时才惰性组装生产端口；
  - P2-5：存在冗余 import。
- 尚未进行真实三层联调：L2 的 G4/G8 未实现，届时会如实走固定模板。
- 未运行 `make up`（全栈容器）。`agent.env` 尚未注入 `GATEWAY_URL` 与 `DEEPSEEK_API_KEY`，部署配置由协调者在全栈阶段完成。
- C1e（输出校验、卡片、回放）不在本组件范围内。
