# 任务书 W0：L2 Gateway 基础骨架

你是 BankSense Gateway 项目 L2 阶段的开发 worker，在一个独立 git worktree 的分支 `feat/l2-foundation` 上工作。你完成的骨架是后续 4 个并行任务（W1–W4）的共同基础，**它的接口一旦定下，后面的人都按它写**，所以要清楚、稳定、有测试。

**最高原则：稳健与架构合理优先于速度。** 宁可少做、做对，也不要为赶进度留下含糊的接口。你的产出会先经过架构评审才放行后续任务。

## 1. 先读

1. `AGENTS.md`（全文，特别是 §2 不可违反的约束、§8 工作规则）。
2. `docs/plans/L2-plan.md`（全文，你是其中的 W0；§2.3、§2.4、§4 的约定对你同样适用）。
3. `PRD.md`：§3.3、§3.6、§7.1、§7.2、§7.3（只看令牌与凭证部分）、§7.8 末段（锁连接规则）、§7.12、§9.1、§9.5a、§9.8、§11.1。
4. 现有代码：`services/bank-backend/`（P1 的 L3，学习它的结构、迁移、测试数据库与 compose 写法）、`packages/contracts/`、`packages/bankcore/`、`docs/contracts/v1.json`、`Makefile`、`docker-compose.yml`、`tests/`。

## 2. 要交付的东西

全部放在 `services/gateway/` 下（以及下面列出的少量共享文件）。

1. **双应用单进程**：`public_app`（:8200，绝不注册 `/internal/*`）与 `internal_app`（:8201，只有 `/internal/*`）。一个 asyncio 进程里跑两个 `uvicorn.Server`，绑定地址取 `GATEWAY_PUBLIC_BIND` / `GATEWAY_INTERNAL_BIND`。保留现有 `/health`。
2. **配置与启动护栏**（PRD §7.3、§11.1）：DEMO_MODE 与 EVAL_MODE 同时为真拒绝启动；`GATEWAY_PROFILE` 非 B2 时必须 EVAL_MODE；`DEMO_CONSOLE_TOKEN` 存在时少于 32 字节拒绝启动。
3. **gateway 库迁移**（Alembic，独立于 bank-backend 的迁移目录）：PRD §7.2 表清单**全部**建表，字段、主键、唯一约束、状态枚举 CHECK 按 PRD 写；`audit_events` 加 `UNIQUE(customer_id, prev_hash)`。数据库角色：运行角色对 `audit_events` 只有 INSERT / SELECT（参照 bank-backend 的角色与 bootstrap 做法）。
4. **统一错误与请求约定**（PRD §9.1、§9.8）：错误体 `{error:{code,message,details,trace_id}}`；`X-Request-ID` 回显为 trace_id；错误代码只能来自 `safebank_contracts.codes`（缺的码登记到待决，不要自己改 contracts）；request_id 幂等的通用工具（同 ID 同体返回原结果，不同体 409 `IDEMPOTENCY_CONFLICT`）。
5. **凭证中间件骨架**：识别 F / A / S / D 四类服务令牌（请求头 `X-Service-Token`，常量时间比较）与 `Authorization: Bearer` JWT 的**校验接口**（Ed25519 / EdDSA，校验 alg、iss、aud、exp、nbf、jti、sub、sid、scope）。签发与登录逻辑留给 W1，但你要提供可测试的签发辅助函数供测试用。`auth/matrix.py` 定义静态授权矩阵的数据格式与查表函数，先登记 `/health` 与 S1 两条。
6. **L3 客户端与 fake L3**：httpx 异步客户端调用 P1 已有的 L3 内部接口（snapshot、read、plans/prepare、plans/execute、executions、reconcile、outbox），超时 prepare 3s、execute 10s、其余 5s，带 `GATEWAY_SERVICE_TOKEN`。另写 **fake L3**（测试替身）：按 `docs/contracts/v1.json` 校验请求与响应形状，可编程返回成功、STATE_CHANGED、超时、丢回包。
7. **审计追加库函数**（PRD §7.12）：每客户哈希链，追加前取 `pg_advisory_xact_lock(hashtext('audit'), hashtext(customer_id))`，再读 prev_hash 插入；提供校验整条链的函数。
8. **流水线插件接口**（最关键）：在 `services/gateway/app/pipeline/` 定义：
   - `EvalContext`（请求、调用方身份、origin、计划草稿、快照占位、findings 累积、审计句柄等，字段按 PRD §7.2 ①–⑨ 的需要设计）；
   - 阶段协议（如 `async def run(ctx) -> None`，以及 `Finding` 的结构：code、step_id、effect、resolver、message、evidence_refs，见 PRD §7.9、§9.8）；
   - 9 个阶段**各一个文件**（`contract.py`、`entity.py`、`provenance.py`、`intent.py`、`snapshot.py`、`preflight.py`、`workflow.py`、`decision.py`、`audit.py`），每个文件里只放一个返回「未实现」的占位实现；
   - `pipeline/__init__.py` 负责按顺序编排、短路规则（①失败不拉快照等，PRD §7.2）。后续 W2 负责 entity / provenance / intent，W3 负责 snapshot / preflight / workflow / decision，他们只改各自文件。
9. **测试基础设施**：`make test-gateway-db`：启动**唯一命名、随机宿主端口、不使用固定子网**的临时 Postgres 16 容器，跑 gateway 迁移与需要数据库的测试，结束销毁（多个 worker 会同时跑它，不能互相冲突）。`make test-gateway`：gateway 的单元测试。两个目标都**追加在 Makefile 末尾**。
10. **依赖**：如需 PyJWT、argon2-cffi、pypinyin（W1 会用），由你一次性加入 `pyproject.toml` 并更新 `uv.lock`，精确版本；同步在 `docs/third-party.md` 登记版本、license 与用途（license 以 PyPI 或仓库 LICENSE 为准，写明来源）。
11. **compose**：只在需要时调整 gateway 服务（第二个端口、bank-net 接入、环境变量）；不要动其他服务；保持 P1 的 `make up`、`make test-p1` 不被破坏。
12. `services/gateway/README.md`：启动、测试、环境变量、目录结构、插件接口说明（后续 worker 会读它）。

## 3. 约束

- 只改 `services/gateway/**`、`tests/gateway/**`（新建）、`Makefile`（只在末尾追加）、`docker-compose.yml`（只改 gateway 服务）、`pyproject.toml` / `uv.lock`（只加依赖）、`docs/third-party.md`、`docs/progress/W0-foundation.md`。**不改** `packages/contracts/**`、`packages/bankcore/**`、`services/bank-backend/**`、`AGENTS.md`、`PRD.md`。
- 不实现业务规则（登录、原话解析、污点、预演、审批、执行编排都是后续任务），只留接口与占位。
- 金额只用整数分；不引入 PRD 未提到的大型框架。
- 不 `git push`；不碰工作区外的文件；不读取或打印 `.env` / `.secrets` 中的值。
- PRD 不闭合或矛盾时，不自行发明设计：写进 `docs/progress/W0-foundation.md` 的「待决」一节，选最保守的可交付方案继续。

## 4. 退出条件（全部实际跑过）

- `make lint` 通过。
- `make test-gateway` 通过：双端口路由隔离（public 上 `/internal/*` 404，internal 上没有公共路由）、启动护栏、错误格式、request 幂等、JWT 校验（篡改、过期、错 aud、错 scope）、服务令牌、授权矩阵查表、fake L3 契约校验、流水线编排与短路。
- `make test-gateway-db` 通过：空库 `upgrade head` 两次成功；运行角色不能 UPDATE / DELETE `audit_events`；并发追加审计不分叉、篡改能被校验函数发现。
- `make test` 全量仍通过（不破坏 P1 的 116 个单测）。
- 能做到的话再跑一次 `make up` 确认全部服务 healthy（如果和别的环境冲突跑不了，在进度文件里说明）。

## 5. 交付

1. 在分支 `feat/l2-foundation` 上本地提交（可以多个提交），提交信息末尾加 `Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>`。不 push。
2. `docs/progress/W0-foundation.md`：做了什么、文件清单、每条退出命令的实际结果摘要、待决问题、给 W1–W4 的接口说明要点。
3. 最终回复（会被指挥读取，保持简短）：完成项、退出命令结果、未完成项、待决问题。
