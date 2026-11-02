# 交接：主会话 → Codex（2026-10-24，同一台机器）

Codex 在本机接手协调工作，**继续遥控本机 Claude Code CLI 的 DeepSeek V4 Pro worker 作业**。实现和评审都交给 worker 做，Codex 不写业务代码。其余规则以 `AGENTS.md`（§2、§8）、`docs/plans/L2-plan.md`、`docs/plans/briefs/wave1-common.md` 为准。

## 1. 先做这几件事

1. 读本文件、`AGENTS.md` 全文、`docs/plans/L2-resume-2026-10-24.md`（接续计划与合并顺序）。
2. **主 checkout 有未提交内容**：`AGENTS.md` §4 追加了本轮 4 条日志。这些要随下一个 PR（建议 W1 的 PR）一起提交：复制到 W1 worktree 后再提交，主 checkout 不要直接提交到 `main`。`git stash@{0}` 里是已经并进 PR #12 的旧副本，可以 `git stash drop`。
3. Docker 必须用无界面方式在后台运行（用户曾误关 GUI，导致引擎停止）：
   ```bash
   nohup /Applications/Docker.app/Contents/MacOS/com.docker.backend -with-frontend=false -unattended >/dev/null 2>&1 & disown
   ```
   核对 `docker info`。两个数据卷 `safebank_pg-bank` 和 `safebank_pg-agent` 的创建时间应为 2026-10-23T11:29:55Z，**禁止删除或重建**。长期栈的 6 个容器目前处于 Exited 状态，需要时在 `../Fintech2026-wt/main-baseline` 里（先 `git checkout --detach origin/main` 更新）串行执行 `make up`。

## 2. 本机布局

- 主 checkout：`/Users/zhangshijie/Desktop/Project/Fintech2026`（`main`，cca074b）。
- worktree 目录：`/Users/zhangshijie/Desktop/Project/Fintech2026-wt/<名>`，包括 main-baseline、S1f（已合并）、W1、W2、S3a、W0-foundation（旧）。
- **`.secrets`**：只有 `W0-foundation/.secrets` 与本机数据卷匹配。其他 worktree 一律软链接过去：`ln -s ../W0-foundation/.secrets .secrets`（`/.secrets` 已写入 `.git/info/exclude`）。主 checkout 自带的 `.secrets` 是旧版，与数据卷不匹配，不要用它 `make up`，也不要用它覆盖别的。
- 新 worktree 第一次使用前要 `make setup`。actionlint 已通过 Homebrew 安装（1.7.12）。

## 3. 怎么遥控 DeepSeek worker

用脚本 `scripts/ds-worker.sh`（未提交，可随下一个 PR 提交）：

```bash
scripts/ds-worker.sh impl   <worktree> <任务书/提示文件> <日志名> [要续接的 session_id]
scripts/ds-worker.sh review <worktree> <提示文件> <日志名>
```

- 脚本会清掉继承的 `ANTHROPIC_*` 环境变量，让 CLI 读取 `~/.claude/settings.json` 里的 DeepSeek 配置。**不清掉的话会走 Anthropic 账户计费**。不要打印 settings 里的 token。
- impl 模式：可以改文件、本地提交、跑 lint/test/test-gateway/test-gateway-db/专项目标。禁止 make up/down/test-p1*/test-integration、docker volume、git push。
- review 模式：只读，外加写评审报告；禁止 Edit、commit、make、docker。评审提示的公共前缀见 §6。
- 用后台方式运行。结果 JSON 在 `../Fintech2026-wt/.ds-logs/<日志名>.json`，看 `result`、`num_turns`、`session_id`。实时过程用 `scripts/watch-worker.sh <worktree 名>` 跟踪。
- worker 的白名单会拒绝用 `;` 或 `&&` 串联的组合命令，提示里要写明「单条命令」。
- 本轮之前的日志在旧位置：`/private/tmp/claude-501/-Users-zhangshijie-Desktop-Project-Fintech2026/89da7fbc-ae2f-4787-ada3-84d9c81217c3/scratchpad/workers/`（briefs 在同级 `briefs/`），续接时可以从那里查 session_id。

门禁（用户的长期授权）：worker 实现 → 协调者亲自复跑退出命令（含 actionlint）→ DeepSeek 只读评审 → 修完 → 推送并开 base `main` 的 PR → **当前 head** 的 unit 与 p1-postgres（push + PR 两套，共 4 项）全部 SUCCESS → `gh pr merge <n> --merge --match-head-commit <sha>`。`main` 没有分支保护，必须自己核对 CI。每一步都追加到 `AGENTS.md` §4。

## 4. 进行中的工作（交接时刻）

| 组件 | 状态 | 下一步 |
|---|---|---|
| S1f | **已合并** PR #12（cca074b） | — |
| W1 `feat/l2-identity`（`../Fintech2026-wt/W1`，HEAD 159d811，未推送） | 已同步 main（358bd48）并修完两条 P2。协调者亲自复跑五条退出命令 + actionlint 全部通过（test-w1 1688 passed，零 skip）；新测试红绿已实测。评审报告 `docs/reviews/L2-W1-final-review.md` 已提交 | **DeepSeek 增量评审 R-W1d 在交接时仍在运行**，结果写到 `../Fintech2026-wt/.ds-logs/R-W1d.json`（若进程结束晚于复制，则在原 scratchpad workers/ 下），并追加进该评审报告末尾。结论为 APPROVE 后：把主 checkout 的 AGENTS 日志和 `scripts/ds-worker.sh` 带进去 → 推送 → 开 PR → CI 全绿 → 合并 |
| S3a `feat/l3-obligations-data`（`../Fintech2026-wt/S3a`，基于 cca074b） | **DeepSeek 实现 worker 在交接时仍在运行**：进程 `claude -p`，会话 `cb43a8a8-7395-4b65-bd3a-3571fd3da79a`，实时记录在 `~/.claude/projects/-Users-zhangshijie-Desktop-Project-Fintech2026-wt-S3a/`。已改 contracts、`v1.json` 快照、bankcore reserve、bank-backend 的 models/state/db、Makefile 等，尚未提交 | 等它结束：读 原 scratchpad `workers/S3a.json`（进程写完后才有内容）。如果被中断，用 `scripts/ds-worker.sh impl ... <session_id>` 续接。审核重点：`docs/contracts/v1.json` 只允许经核对的内部增量；历史迁移 0001/0002 字节不变；`make test-obligations` 零 skip。全栈门禁（make up、test-p1、test-p1-concurrency、test-p1-consistency、test-integration）由协调者串行执行 |
| W2 `feat/l2-provenance`（`../Fintech2026-wt/W2`，8063f27） | memo 增量评审 APPROVE（报告在该 worktree `docs/reviews/L2-W2-memo-review.md`，未提交）。P2：单独的 ¥/￥ 没有数字时也会被升为黄级 | W1 合并后：同步 main，接入真实 G3/load_for_plan 与全局撤销，删掉 fixture 替身，并修这条 P2 |
| S3a 字段 F1 | 复审 APPROVE（`S3a/docs/reviews/L2-S3a-fields-f1-recheck.md`） | 实施时补 PRD §8.3（已写进 S3a 的提示） |
| W3 / W4 | 旧检查点 3bcbe8e / 3856a4c | 依次在 W2、W3 之后：同步 main → 核对缺口 → 续写 |
| W5 / W6 | 任务书未写 | — |

## 5. 待用户确认的新安排：L1 并行开发

交接前，另一个会话（「BankSense Gateway 主要开发」）转达了用户的决定：**L1（PRD §13 流 C）立即与 L2 并行**。理由是 L2-plan 里「不做 L1」是协调者自己收窄的范围，不是用户的决定（用户只暂停了前端和 BFF）。要求如下：

1. 修正 `docs/plans/L2-plan.md` 中「不做 L1」那一条；在 `AGENTS.md` §4 记录这次纠正，并在 §3、§5 增加 L1 进度行。
2. 按 PRD §6 写 L1 任务书（如 `docs/plans/briefs/C1-agent.md`，另附公共约定），拆成小组件：图骨架与 AgentState；DeepSeek 适配与 strict 计划生成（沿用 ADR-002：/beta、关闭思考、reasoning_content 处理）；gateway 客户端与 §6.1.1 返回码路由表；审批 interrupt/resume；回放录制。只改 `services/agent/` 与 `tests/agent/`。gateway 未完成的接口按 PRD §9.5 写 fake 测试，fake 不能当作联调通过。
3. 从最新 `main` 新建 worktree `feat/l1-agent`，派 DeepSeek worker 实现、DeepSeek 只读评审，走同一套门禁。
4. 与 W1 收尾、S3a 并行（文件不重叠）。

交接时这项还没开始。它是经别的会话转述的，**开工前请向用户确认一句**。

## 6. 只读评审提示的公共前缀

见 `../Fintech2026-wt/.ds-logs/briefs-2026-10-24/review-common.md`（全部提示文件都已复制在该目录），要点：只读；只写指定的评审报告；可以用 git diff/log/show 和定点 pytest；单条命令；每条发现要有 file:line、失败场景、修复建议，分 P0/P1/P2；最终给出 APPROVE / REQUEST_CHANGES。
