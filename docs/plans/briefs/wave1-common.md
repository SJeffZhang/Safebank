# 波次 1 公共约定（W1–W4 都必须遵守）

**最高原则：稳健与架构合理优先于速度。** 你是四个并行 worker 之一，其他三个同时在改别的文件。遵守下面的边界，就不会冲突；越界就会让合并失败、整个波次返工。

## 1. 必读

1. `AGENTS.md`（§2 约束、§8 规则）。
2. `docs/plans/L2-plan.md`（§0、§2.3–§2.5、§3 中你的任务、§4）。
3. `services/gateway/README.md`（W0 的骨架说明、分工表、插件接口）。
4. `docs/reviews/L2-W0-architecture.md` 的「给 W1–W4 任务书的接口约定要点」和「第三轮定点复核」两节。
5. `PRD.md` 中你的任务书列出的章节。

## 2. 冻结的文件（任何人都不改，需要改写进「待决」）

`services/gateway/app/pipeline/__init__.py`、`pipeline/context.py`（`EvalContext` 结构冻结；各阶段结果类只能由拥有者**新增字段**）、`schemas/**`、`auth/matrix.py`（矩阵已登记全表，冻结）、`state.py`、`tables.py`、`migrations/**`、`main.py`、`config.py`、`policy/registry.py`（结构冻结；`policies/tools.yaml` 只能修正值，且只有 W2 能改）、`pyproject.toml`、`uv.lock`、`requirements*.txt`、`docker-compose.yml`、`packages/contracts/**`、`packages/bankcore/**`、`services/bank-backend/**`、`AGENTS.md`、`PRD.md`。

## 3. 必须使用的接口

- 状态变更只能调用 `state.transition`。非法边抛 `ValueError`，那是代码错误，不要转成 409。`EXECUTING` 只在复演通过后才写入。
- 带 `request_id` 的接口只能用 `idempotency.request_idempotent`（同一事务内完成，事务内不发 HTTP；G8 执行不走它）。
- 错误只能 `raise GatewayError(Code.X)`，code 只来自 `safebank_contracts.codes`；缺码写进「待决」。
- 调用方身份从 `request.state.caller` 取，不要重复声明鉴权依赖；`origin`、`customer_id` 绝不从请求体读取。
- 调 L3 只能通过 `L3Client`；测试用 `FakeL3().transport()`。
- 审计只能用 `audit.append_tx`，传入时间必须带时区，且不在包含 HTTP 调用的事务里追加。
- ActionKind、基础等级、outflow 只能用 `policy.registry` 计算（`action_kind()`、`outflow()`），不得另写一套。卡不在影子状态时它会抛 `LookupError`，调用方按实体不可见处理。
- 交叉接口（签名不能改，对方合入前各自用替身打桩）：
  - W3 实现 `evaluation/repo.py: append_evaluation`、`execution/replay.py: replay`（`replay` 必须用新的 `EvalContext`，不能复用缓存快照）。
  - W4 实现 `approval/service.py: create_for_evaluation`、`approval/planview.py: build_view`、`locks.py: customer_lock`。
- 预演只调用 `bankcore`，不写任何业务算术。

## 4. 数据库迁移

波次 1 期间**不写迁移**。需要新表或新字段，写进进度文件「待决」，由指挥在 `main` 上串行追加 `0002_wave1`。在那之前，用现有表能做的先做，做不了的停在可交付点。

## 5. 测试与环境

- 测试只放在 `tests/gateway/<你的任务名>/`（需要数据库的放 `tests/gateway/<任务名>/db/` 并复用 `make test-gateway-db` 的临时库机制）。
- Makefile 只允许在末尾追加你自己的 `test-<任务名>` 目标。
- **不要跑 `make up`、`make down`、`make test-p1`**（四个 worker 同时跑会争用同一套容器与固定子网），全栈测试由指挥统一跑。
- **禁止删除、重建任何 Docker 数据卷**。

## 6. 交付

- 只在你的分支上本地提交，不 push；提交信息末尾 `Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>`。
- 进度写 `docs/progress/<任务名>.md`：做了什么、文件清单、每条退出命令的实际结果、待决（含需要的迁移、缺的错误码、对冻结文件的修改请求）。
- 遇到 PRD 不闭合或与骨架冲突：不自行发明设计，写进「待决」，选最保守的可交付方案继续。
- 最终回复简短：完成项、退出命令结果、未完成项、待决。
