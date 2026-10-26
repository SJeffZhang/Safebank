# 发给 Codex 的接续 prompt（直接复制以下全部内容）

你现在接手 SafeBank Gateway 项目的后端协调工作（第一负责人），工作目录 `/Users/zhangshijie/Desktop/Project/Fintech2026`，仓库 `SJeffZhang/Safebank`（私有），同一台机器上前一位协调者是 Claude 主会话，现已停工。

**先只读核查，不要改任何东西：**
1. 完整阅读 `docs/handoffs/2026-10-26-codex-handoff-v2.md`，再读 `AGENTS.md`（§2 约束、§3 状态、§4 最后约 20 条日志、§5 待办、§8.6、§8.7）。
2. 核对现场：`git -C /Users/zhangshijie/Desktop/Project/Fintech2026 log --oneline -1`（应为 main `00aca15` 或其后的文档合并）、`git worktree list`、`git stash list`（19 条，不要 drop）、`docker ps`（六个 `safebank-*` healthy）、`docker volume inspect -f '{{.Name}} {{.CreatedAt}}' safebank_pg-bank safebank_pg-agent`（应为 2026-10-23T11:29:55Z）、长期栈 gateway 库版本 `0003_plan_draft`。确认没有正在运行的 `claude -p` worker。
3. 如果分支 `docs/codex-handoff-20261026` 的文档 PR 尚未合并：核对它只含 .md，按 CI 门禁（push 与 pull_request 两次运行 head_sha 都等于完整 SHA、四个 job 全 success）后 `gh pr merge <n> --merge --match-head-commit <完整 SHA>`。

**工作方式（必须遵守）：**
- 实现与评审都交本机 Claude Code CLI 的 DeepSeek worker，经 `scripts/ds-worker.sh impl|review <worktree 绝对路径> <提示词绝对路径> <日志名>` 后台启动；你负责拆任务、在提示里写定点事实（签名、行号、模板测试文件）、亲自复跑退出命令、红绿与变异核验、发布 PR。一次只派一个任务，默认全新会话，`DS_MAX_TURNS` 40–70，测试命令加 `perl -e 'alarm 300; exec @ARGV'`。
- 模型只用 `deepseek-v4-pro`；遇 402/403/429/连接拒绝立即停止全部派发，不重试、不换模型，等用户确认后只做一次有界验证。
- 不打印 `.env`/`.secrets`/`~/.claude/settings.json`；不删除或重建任何 Docker 数据卷；全栈命令只由你在 `../Fintech2026-wt/main-baseline` 串行执行；不对长期业务库 reset/TRUNCATE。
- 前端/BFF 暂停；定时任务 safebank-worker、skillloop 保持 PAUSED；不要自动恢复。
- 不伪造测试结果：区分 worker 自报、你亲自复跑、远端 CI、mock/skip 与真实 PG/HTTP。不为提高指标或过测试放宽 L2 规则。
- 每个操作写进 `AGENTS.md` §4；每合并一个组件更新进度网站 `../safebank-progress/data/progress.json` 并核对线上。

**当前暂停中、需等用户明确下令才开工的任务：**
- E0b 收尾 + live 稳定性评测；
- `make try-agent` 命令行三层真实闭环驱动脚本。

**用户下令后的建议顺序**（详见交接 v2 §4）：P2 真实三层验收（确认前 L3 无写、确认后只记一次账，写 `docs/acceptance/P2.md`）→ E0b → W5（含 W4c 遗留的孤儿 PAUSED job 归档、UNKNOWN 启动扫描、崩溃恢复真实测试）→ W6 → P3。

核查完成后，先用中文向用户简要报告现场是否与交接一致、有无异常，再等待用户指示，不要主动开始新任务。
