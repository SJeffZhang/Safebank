# 交接说明：BankSense Gateway → Codex（2026-10-23）

> 写给在 Codex 中接手本项目的协调 agent。仓库 `SJeffZhang/Safebank` 的 `main` 是唯一事实来源；本文件之外的一切以 `AGENTS.md`（当前状态与规则）和 `PRD.md`（设计规格）为准。

## 1. 项目与当前进度

参赛项目（2026 深圳国际金融科技大赛，银行 AI 智能体赛题，**提交截止 2026-11-02**）：三明治架构的 AI 银行助手。

- **L1 Agent**（LangGraph，`services/agent/`）：理解意图、生成计划，不持有写权限。**尚未开发。**
- **L2 Security Gateway**（`services/gateway/`，核心创新）：所有读写的唯一入口；原话授权、污点判定、累积金融预演、跨会话工作流规则、审批 / OTP、原子执行与恢复、审计。**本阶段正在做。**
- **L3 Banking Backend**（`services/bank-backend/`）：账本、转账、卡、快照、执行意图。**P1 已完成并合入 main**（PR #1）。

| 已完成并在 main | 说明 |
|---|---|
| PRD v2.1 定稿 | `PRD.md`，三轮多 agent 审核；决议 D1–D47 在 §0.5 |
| P1：契约 v1 + bankcore + L3 核心 | 冻结契约 `docs/contracts/v1.json`；CI 全绿 |
| L2 W0：网关基础骨架 | PR #2（merge e2e91db）；4 轮架构评审，报告 `docs/reviews/L2-W0-architecture.md` |

**下一步：L2 波次 1（W1–W4 四路并行）**，任务书已写好但未启动。之后是 `0002_wave1` 迁移、波次 2（W5、W6，任务书待写）。详见 `AGENTS.md` §5「L2 进度」。

## 2. 开工前必读（按顺序）

1. `AGENTS.md` 全文。重点：§2 不可违反的约束（共 13 条，第 13 条是**前端暂停**）、§3 当前状态、§5「L2 进度」、§8 工作规则（§8.0：所有操作都要追加到 §4 进度日志）。
2. `docs/plans/L2-plan.md`：§0 最高原则、§2.5 合并流程、§3 各任务范围、§4 共享文件约定。
3. `docs/plans/briefs/wave1-common.md`：波次 1 公共约定（冻结文件、必须使用的接口、迁移规则、测试隔离、交付格式）。
4. `docs/plans/briefs/W1-identity.md`、`W2-provenance.md`、`W3-decision.md`、`W4-execution.md`：四份任务书。
5. `services/gateway/README.md`：W0 骨架说明、分工表、插件接口。
6. `docs/reviews/L2-W0-architecture.md`：「给 W1–W4 任务书的接口约定要点」与「第三轮定点复核」两节（含 10 条接口约定更新）。
7. `PRD.md` 中各任务书列出的章节。

## 3. 用户定下的工作原则（必须遵守）

1. **稳健与架构合理优先于并行。** 并行只是手段；会带来接口冲突、重复造轮子或架构漂移时，减少并行路数，必要时改串行。
2. **前端暂停。** 前端 App 与 BFF（PRD §9.6 F1–F13、§9.10）一律不开工，直到用户亲自下令。测试中直接用 F 服务令牌 + app JWT 调 gateway。
3. **逐组件 PR。** 每完成一个完整组件（一个 W 任务），测试通过就开 PR（base `main`），审核通过、CI 全绿就合并（merge commit）。这是用户的长期授权，满足条件不必逐个询问。
4. **审核要真实。** 协调者在 worker 的分支上**亲自复跑**退出命令，不采信自报；另派只读评审 agent 对照 PRD 审查；问题修完才开 PR。
5. **不编造。** 没跑过的测试不写通过；对开源项目、赛题的断言要有来源。

## 4. 用 Codex 多 agent 推进波次 1

- **隔离**：每个子 agent 一个 git worktree、一个分支（分支名见任务书），全部从最新 `main` 拉出；子 agent 只在本地提交，由协调者推送和开 PR。
- **分工**：四个子 agent 各执行一份任务书，并遵守 `wave1-common.md`。文件所有权已经划清：阶段 ①–④ 归 W2，⑤–⑨ 与 G4 归 W3，W1 负责原话与身份，W4 负责审批与执行；`EvalContext`、`schemas/`、`auth/matrix.py`、`state.py`、`tables.py`、迁移、`main.py`、`config.py`、`policy/registry.py` 结构**冻结**。
- **交叉接口**（签名不能改，对方合入前用替身打桩）：W3 实现 `evaluation/repo.append_evaluation` 与 `execution/replay.replay`；W4 实现 `approval/service.create_for_evaluation`、`approval/planview.build_view`、`locks.customer_lock`。
- **合并顺序建议**：W1、W2 先合（W2 依赖 W1 的 `AuthorizationInputs`），再 W3（依赖 W2 的 `ctx.steps`），最后 W4（依赖 W3 的 `replay`）。后合的分支在合并前先同步最新 `main` 并复跑测试。
- **迁移**：波次 1 期间子 agent 不写迁移；把需要的表 / 字段写进各自 `docs/progress/W*.md` 的「待决」，由协调者汇总后串行追加 `services/gateway/migrations/versions/0002_wave1.py`，单独开 PR。
- **进度记录**：子 agent 写 `docs/progress/<任务名>.md`，不改 `AGENTS.md`；协调者把结果汇总进 `AGENTS.md` §4 与 §5。

## 5. 环境与已知坑

- **首次在新环境启动**：`make setup && make keys && make up`。`.secrets/` 不在仓库里，`make keys` 会新生成；所以接手后必须先跑一次 `make keys`，再 `make up`。
- **全栈命令不能并发**：`docker-compose.yml` 的项目名固定为 `safebank`，网络是固定子网。多个子 agent 同时跑 `make up` / `make down` / `make test-p1` 会互相抢容器。子 agent 只跑 `make lint`、`make test`、`make test-gateway`、`make test-gateway-db`（临时库，随机端口，可并发）和自己的 `make test-w*`；全栈测试由协调者串行跑。
- **禁止删除或重建 Docker 数据卷**（`docker volume rm`、`docker compose down -v`、`docker system prune`）。之前有 worker 误删过 P1 的数据卷。
- **Linux 文件权限**：密钥文件默认 0600。容器里非 root 用户读宿主挂载文件会 Permission denied，macOS 上测不出来，只在 CI（Linux）暴露。P1 与 W0 都踩过这个坑；JWT 公钥现在以 0644 写入。新增需要挂载的文件时，先想清楚容器内用户能不能读。
- **包名**：gateway 的 Python 包是 `services.gateway.app`（避免与 L3 顶层 `app` 冲突）。
- **自动合并**：仓库已开「Allow auto-merge」，但 `main` 没有分支保护（必需检查），所以 GitHub 在检查跑完前不接受 PR 的自动合并。要么等 CI 全绿后手动 `gh pr merge --merge`，要么经用户同意给 `main` 加「必须通过 unit 与 p1-postgres」的分支保护。
- **CI**：`.github/workflows/` 中 `unit`（pytest + ruff）与 `p1-postgres`（bootstrap、`make up`、P1 测试、集成、契约检查）两个 job。

## 6. 尚待处理的小项

- 波次 2 的 W5（S1 定时授权、通知、G11/G16、D 接口 gateway 侧）、W6（网关层安全验收）任务书未写；范围见 `docs/plans/L2-plan.md` §3。
- 依赖 L3 后续能力（事件 job、时钟推进、演示注入）的部分，gateway 侧先用 fake L3 实现，真实联调登记在 `docs/plans/L2-plan.md` §7「待联调」。
- W0 评审留下的 P2：app_form 计划取快照时的 steps、`replay` 必须用新的 `EvalContext`（W3 处理）；幂等清理在数据库错误时会掩盖原始异常。

## 7. 第一件事

1. 克隆仓库，读完 §2 的必读材料。
2. `make setup && make keys && make up && make test && make test-gateway && make test-gateway-db`，确认 main 是绿的。
3. 在 `AGENTS.md` §4 追加一条「Codex 接手」的日志。
4. 按 §4 开四个子 agent 启动波次 1；协调者自己不写业务代码，负责复跑、审核、开 PR、合并、记日志。
