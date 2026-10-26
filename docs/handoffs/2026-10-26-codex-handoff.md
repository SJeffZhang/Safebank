# 交接：Claude 主会话 → Codex（2026-10-26）

> **快照说明**：本文写于 W4c HEAD `0478105` 时。之后主会话已继续：P2-1/P2-3 已修（`dbb5426`，test-w4c 38 passed）、已合入 main `51c3b90`；最新进度以 AGENTS §4 为准。
>
> 接手者先读本文件，再读 `AGENTS.md`（§2 约束、§4 最新日志、§8.6 进度网站、§8.7 派发规则）。本机就是协调机；worktree、Docker 卷、`.secrets` 软链都在原位，**不要重建或删除**。


> **Codex 2026-10-26 现场复核**：本文剩余步骤已由前任继续完成至 PR #31 合并 `00aca15`、合并后组合回归、长期库 `0003_plan_draft` 与进度页发布。本次另亲跑 W4c 38 passed，核实精确 head 的 push/PR 四 CI、六服务 healthy及两卷 CreatedAt 未变。当前状态见 AGENTS §3，证据见 `docs/acceptance/L2-W4c-execute.md`「合并后交接核对」；不要按本历史表重复修复或合并。E0b/live/try-agent 继续暂停。

## 0. 用户最新指令（优先级最高）

1. **全力解决 W4c**（用户原话：「全力解决w4c」）。
2. **暂停**：E0b 收尾 + live 评测、`make try-agent` 驱动脚本。两项都未开工，只在 AGENTS §5 登记为待办，不要启动。
3. 实现与评审仍交本机 Claude Code CLI 的 DeepSeek worker（`scripts/ds-worker.sh`）；协调者负责拆任务、复跑、红绿/变异核验、发布。worker 一次只派一个任务，用全新会话，提示里给定点事实、点名要读的文件（AGENTS §8.7）。
4. 百炼额度在本会话末刚恢复，有界验证 `probe-recover-20261026-1` 已通过（模型 deepseek-v4-pro）。若再遇 402/403/429：立即停止，不重试、不换模型，等用户确认后只做一次有界验证。

## 1. 仓库与分支现场

| 位置 | 分支 / HEAD | 状态 |
|---|---|---|
| 主 checkout `Fintech2026` | `main` = `51c3b90`（PR #30 E0a 合入后） | 有未提交：`AGENTS.md`（PR #30 之后的全部日志行）、`scripts/ds-worker.sh`（固定 deepseek-v4-pro、MAX_THINKING_TOKENS、DS_MAX_TURNS、wip 快照）、本交接文件。**随 W4c 的 PR 一起带入**（复制到 W4c 分支后提交）。 |
| `../Fintech2026-wt/W4c` | `codex/w4c-execute` HEAD `0478105`（基线 `9b8c65a`，尚未合入 `51c3b90`） | 工作区干净；`make test-w4c` 36 passed / 2 failed（见 §2） |
| `../Fintech2026-wt/R-W4c` | detached `95225bc` | 评审 worktree，已 `make setup`；复审时 `git checkout --detach <新 HEAD>` |
| `../Fintech2026-wt/e0b-l1-runner` | `codex/e0b-l1-runner` HEAD `ba76448` | 干净；**暂停** |
| `../Fintech2026-wt/main-baseline` | detached `51c3b90` | 合并后组合回归专用；`.secrets` 软链到 W0 worktree |
| `../Fintech2026-wt/.ds-logs/` | — | worker 日志（`<名>.json/.err`）；提示词在 `briefs-2026-10-24/` |
| `refs/wip/*` | — | 启动器自动快照，用 `git show-ref | grep wip` 查看 |

`git stash list` 里还有若干条协调者保留的 stash（`coordinator-pr*-ff-preserve-*`、`W4c-T2c2 WIP`），不要 drop。

## 2. W4c 当前状态（G8 执行 / G5 / 屏障 / UNKNOWN 恢复）

任务书 `docs/plans/briefs/W4c-execute.md`（文末裁定 C1–C8）；逐任务记录 `docs/progress/W4c-execute.md`；验收草稿 `docs/acceptance/L2-W4c-execute.md`；独立评审 `docs/reviews/W4c-execute-review.md`（R-W4c-r1：REQUEST_CHANGES，无 P0，8 项核对全过）。

已完成并经协调者复跑+变异核验：T0–T7（replay、0003_plan_draft 迁移、G8 ALLOW/CONFIRMED、STATE_CHANGED 屏障与 attempt+1 恢复、UNKNOWN 对账、并发/迟到/撤销/origin、G5、request_id 重放）；CI 已加 `make test-w4c`（`95225bc`）。

**评审修复轮 1 进度**（红测在 `tests/gateway/w4c/db/test_g8_review_fixes.py`，`961dcb0`）：

| 评审项 | 状态 |
|---|---|
| P1-1 恢复路径把 execute 的 STATE_CHANGED 当 UNKNOWN | **已修**（`0478105`），`test_resume_state_changed_enters_fence_again` 通过 |
| P1-2 fence prepare 写死 attempt=1 | **已修**：Prepare 用 `exec_identity.attempt`，ACK 校验 `fence_result.attempt != exec_identity.attempt`（`execution.py:499`，协调者修） |
| P2-1 PAUSED + plan DENIED/REPLAN 误入恢复分支 | **未修**：`test_paused_replan_plan_is_not_resumed` 失败，`KeyError: 'decision'`——第二次 G8 的 409 没有 `details.decision`。修法：在 `execute_plan` 进入 PAUSED 恢复分支前（约 `execution.py:1185`），若 `plan.status in {DENIED, REPLAN}` → 直接 `GatewayError(Code.STATE_CHANGED, 409, ..., details={"decision": <最新 evaluation 的 decision JSON>})`，不访问 L3、不改 DB。`GatewayError` 的 details 参数名看 `services/gateway/app/errors.py`。 |
| P2-2 此类 PAUSED job 无归档路径 | 归 W5，不修，已在评审与验收文档登记 |
| P2-3 对账得终态后未为本次 request_id 写终态 dedup | **未修**：`test_reconcile_writes_terminal_dedup_for_current_request` 失败（no request_dedup row for r2）。修法：G8 经 `_handle_reconcile` / `_reconcile_job` 得到终态 200 时，调用 `_write_request_dedup_row(conn, plan_id=..., customer_id=..., request_id=<本次>, request_hash=<本次>, status_code=200, response=<本次返回体>)`（该函数已是「只覆盖非终态」的 upsert）。需要把 request_id/request_hash 传进对账分支。 |

### W4c 剩余步骤（按顺序）

1. 修 P2-1、P2-3（可派一个 DeepSeek 小任务，提示里直接给上表的修法与行号；或协调者亲改）。`perl -e 'alarm 300; exec @ARGV' make test-w4c` 应为 38 passed。
2. 变异核验：去掉 P2-1 的 DENIED/REPLAN 拦截 → 该测试失败；去掉 P2-3 的写入 → 该测试失败；还原。
3. 复跑全部退出命令（全部加 alarm 防挂死）：`make lint test test-gateway test-gateway-db test-wave1-state test-w1 test-w3b test-w4b1 test-w4b2b test-w4b2c test-w4c test-shared-runtime contracts-check` + `actionlint`。
4. 派原评审者定点复审：在 R-W4c `git checkout --detach <新 HEAD>`，提示词以 `.ds-logs/briefs-2026-10-24/review-common.md` 开头，只审 `95225bc..HEAD`，报告追加到 `docs/reviews/W4c-execute-review.md`（先写骨架、边做边写）。
5. 合入 `origin/main`（`51c3b90`，含 E0a；预计 Makefile/checks.yml 追加行冲突，两边都保留；AGENTS 冲突保留双方全部日志）。合并后再跑一次第 3 步。
6. 把主 checkout 未提交的 `AGENTS.md`、`scripts/ds-worker.sh`、本交接文件复制进 W4c 分支；更新 `docs/acceptance/L2-W4c-execute.md` 的复跑数字与评审结论；提交。
7. 敏感扫描（`git diff --name-only origin/main...HEAD`，确认无 `.env`/`.secrets`/私钥），push，开 PR（base main）。Actions API 核 push 与 pull_request 两次运行的 head_sha 都等于完整 SHA、四个 job 全 success，再 `gh pr merge <n> --merge --match-head-commit <完整 SHA>`。
8. 合并后：主 checkout 先 `git stash push`（保留）再 ff；main-baseline 切新 main 跑组合回归（上述套件 + test-w2 test-w3a test-obligations test-agent-db test-replay test-verify test-cards，及 `pytest tests/evaluation/corpus -q`）。
9. **长期栈迁移**：gateway 库仍为 0002；合并后协调者在 main-baseline 串行 `make up`（会迁移到 0003），确认六服务 healthy、两个持久卷 CreatedAt 不变，再 `make test-integration`。
10. 更新进度网站 `../safebank-progress/data/progress.json`（W4c done、PR 号、回归数字；只写实测），push 后核对线上（AGENTS §8.6）。

## 3. 关键技术事实（W4c 期间查明，别再踩）

- **测试时钟**：w3b 夹具把网关时钟固定在 2026-10-24（WALL），真实 L3 用真实时间核 deadline。W4c 夹具用 `support.py` 的 `_use_real_clock` + `_anchor_to_real_time`；**不要**重锚 `read_records.observed_at`（会导致 UNKNOWN_SENSITIVE_ARG）。
- **读取记录新鲜度**：`provenance.py:43–44` 要求读取记录 versions 等于当前快照。bump 客户 `state_versions` 会让 read 来源过期 → 复演 REPLAN（正确行为，不能恢复）。要构造「可恢复的 STATE_CHANGED」，只 bump L3 `clock.version`。
- **锁**：G8 读事务（含复演快照）持有 session 行锁；测试里不要在 L3 钩子内同步等待会改该行的另一连接（会死锁）——用后台任务排队。
- **状态保护**：`_protect_transition` 在 live job 存在时拒绝 plan 迁移，所以写事务内顺序是 plan→EXECUTING、再插 claims、再插 job。
- **request_dedup**：键是具体路径 `POST /plans/<plan_id>/execute`；终态永不覆盖；非终态标记只表示「已用过」，旧 request_id 不得进入恢复（attempt+1）。
- 调试时临时改动用备份文件还原，**不要**对含未提交成果的文件 `git checkout --`（本会话曾因此丢过一次修改）。
- worker 启动：只用 `run_in_background` 调 `scripts/ds-worker.sh impl|review <worktree 绝对路径> <提示词绝对路径> <日志名>`；不要在前台命令里用 `&`；提示词必须用绝对路径（启动器会先 cd 到 worktree）。

## 4. 之后的工作（W4c 合并后，按用户下一步指示）

- 全栈配置：agent.env 注入 `DEEPSEEK_API_KEY`、`GATEWAY_URL`（只核变量名，不打印值）；P2 真实三层验收（`docs/acceptance/P2.md`，确认前无写、确认后 L3 只记一次账）。
- 暂停中、待用户恢复：E0b 收尾 + live 评测（任务说明见 AGENTS §4 2026-10-26「并行做这两个测试」条）；`make try-agent`。
- W5（定时授权/通知/审计/D 接口，含孤儿 PAUSED job 归档与启动扫描）、W6（网关层安全验收）任务书已在 `docs/plans/briefs/`。
- 前端/BFF 仍暂停；定时任务 safebank-worker/skillloop 保持 PAUSED。
