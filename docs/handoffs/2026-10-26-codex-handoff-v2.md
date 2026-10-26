# 交接 v2：Claude 主会话 → Codex（2026-10-26 晚，W4c 合并之后）

> 本文件取代同目录 `2026-10-26-codex-handoff.md`（那份是 W4c HEAD `0478105` 时的快照，仅供追溯）。
> 接手顺序：本文件 → `AGENTS.md`（§2 约束、§3 状态、§4 最新日志、§5 待办、§8.6 进度网站、§8.7 派发规则）→ 需要时再读 PRD 对应章节。

## 0. 现在的状态一句话

L1 C1a–C1e、L2 W0–W4c 与共享组件、L3 P1/S2/S3a 都已合入 main；**main = `00aca15`**（PR #31 W4c）。合并后组合回归全绿，**本机长期栈网关库已迁移到 `0003_plan_draft`**，`make test-integration` 通过，进度网站已同步。主会话按用户指令在此停工。

## 1. 用户指令与约束（必须遵守）

- 用户最近的指令：「全力解决w4c」（已完成合并）；「先暂停E0b 收尾 + live 评测」与 `make try-agent` 驱动脚本——**两项仍暂停**，不要自行开工，等用户下令。
- 实现与评审交本机 Claude Code CLI 的 DeepSeek worker：`scripts/ds-worker.sh impl|review <worktree 绝对路径> <提示词绝对路径> <日志名>`，只能用后台方式运行（不要在前台命令里用 `&`），提示词必须绝对路径。协调者负责拆任务、写定点事实、复跑、红绿/变异核验、发布。
- 派发规则（AGENTS §8.7）：一次只派一个任务；默认全新会话；提示里给签名/行号/模板文件并限制读取文件数；`DS_MAX_TURNS` 一般 40–70；测试只看汇总。
- 模型固定 `deepseek-v4-pro`（经 CC Switch 走阿里百炼）。遇 402/403/429/ECONNREFUSED：停止全部派发，不重试、不换模型；用户确认恢复后只做一次有界验证（见 §4 示例）。
- 不打印/复制 `.env`、`.secrets` 内容、`~/.claude/settings.json`；不重建密钥。
- **禁止删除或重建任何 Docker 数据卷**（`safebank_pg-bank`、`safebank_pg-agent`，CreatedAt 均 `2026-10-23T11:29:55Z`）；不 `docker compose down -v`、不 prune；长期业务库不 reset/TRUNCATE（只有 tmpfs 测试库可以）。
- 全栈命令（`make up/down/test-p1*/test-integration`）只由协调者在 `../Fintech2026-wt/main-baseline` 串行执行。
- 前端 App/BFF 暂停；定时任务 safebank-worker / skillloop 保持 PAUSED。
- 组件发布长期授权：亲验 + 独立评审通过后推分支、开 base main PR；Actions API 核 push 与 pull_request 两次运行的 `head_sha` 都等于完整 SHA、四个 job 全 success，再 `gh pr merge <n> --merge --match-head-commit <完整 SHA>`。合并后：主 checkout 先 `git stash push -u` 再 ff；main-baseline 跑组合回归；更新进度网站并核对线上。
- 一切操作写进 AGENTS §4（§8.0）。

## 2. 本机现场

| 位置 | 状态 |
|---|---|
| `Fintech2026`（主 checkout） | `main` = `00aca15`，干净（本交接文件所在分支除外，见 §6） |
| `../Fintech2026-wt/main-baseline` | detached `00aca15`；`.secrets` 软链到 `../W0-foundation/.secrets`（与持久卷匹配），全栈命令在此运行 |
| `../Fintech2026-wt/e0b-l1-runner` | `codex/e0b-l1-runner` HEAD `ba76448`，干净，**暂停** |
| `../Fintech2026-wt/W4c`、`R-W4c` 等 | 已合并组件的旧 worktree，保留不删；新任务请从最新 main 新建 worktree |
| `../Fintech2026-wt/.ds-logs/` | worker 结果 JSON/stderr；提示词在 `briefs-2026-10-24/`；合并回归日志 `main-00aca15-regression/` |
| `git stash list` | 19 条，均为协调者保留的 ff 前现场与旧 WIP，**不要 drop** |
| `refs/wip/*` | 启动器自动快照，可追溯 |
| 长期栈 | 六容器 healthy；gateway 库 `0003_plan_draft`（plans.plan_draft jsonb nullable），bank 库 `0003_obligations` |
| 后台进程 | 无运行中的 worker 或测试 |

## 3. 最近一次组合回归（main `00aca15`，main-baseline，全部 exit 0）

lint 364；test 2727 passed / 743 skipped；test-gateway 2018/650 skip；gateway-db 17；shared-runtime 79；wave1-state 231；w1 1688；w2 246；w3a 45；w3b 33；w4b1 57；w4b2b 58；w4b2c 56；**w4c 38**；obligations 30（1 条既有 FK 环 SAWarning）；agent-db 34；replay 47；verify 70；cards 19；contracts-check PASS；corpus 25；actionlint 通过。随后 `make up`（exit 0，迁移到 0003）与 `make test-integration`（exit 0，含 L3 进程重启幂等恢复）。日志在 `.ds-logs/main-00aca15-regression/`。

## 4. 下一步（等用户下令后按序做）

1. **P2 真实三层验收**（PRD §13 定义的 P2：App 里让小安完成一笔需确认的转账；前端暂停时用 F 服务令牌 + app JWT 扮演 BFF）：
   - 先只核变量名：长期栈 `agent.env` 是否有 `DEEPSEEK_API_KEY`、`GATEWAY_URL`（C1d 要求；当前很可能缺 DEEPSEEK_API_KEY，需用户提供或授权注入，不要打印值）。
   - 走真实链路：G15 会话 → G3 原话 → L1 graph（真实 DeepSeek）→ G4 → G9 面板 → G6 确认 →（红级时 G10 OTP）→ G8 → L3；以 L3 数据库前后对比判定（确认前无写、确认后只记一次账），写 `docs/acceptance/P2.md`。
   - `make try-agent` 驱动脚本就是这一步的工具（用户已暂停，等指令）。
2. **E0b 收尾 + live 评测**（暂停中）：现有 `evaluation/runner/{seed,harness,l1,loop}.py` 与 `evaluation/metrics/compute.py`。缺：live/replay 端口工厂（复用 `services/agent/app/main.py:_build_production_ports` 的 C1b 适配器，gather 要接 gateway 读桥而不是 dummy）、运行入口与 `make eval-l1`、报告与失败样本存档、验收文档、DeepSeek 评审、PR。live 只由协调者跑，先小批量冒烟再全量 405 条 × ≥3 次。已知发现：L2 对「hello」「查询余额」等无写意图原话会建 FORM_REQUIRED 澄清，结果记为 CLARIFY，如实统计、不改 L2。
3. **W5**（任务书 `docs/plans/briefs/W5-ops.md`）：定时授权、通知、审计接口、D 接口；另含 W4c 遗留：孤儿 PAUSED job（屏障判 DENIED/REPLAN 后）归档、UNKNOWN 启动扫描/后台恢复、进程崩溃后重建网关的真实恢复测试（裁定 C5 第二项）。
4. **W6** 网关层安全验收（`docs/plans/briefs/W6-acceptance.md`）；之后 P3 攻击 A1/A2/A3 以 L3 状态判定。
5. 已知小问题：W4b2b P2-4（supersede 与 G6/G7 锁序）待统一；W3b `_policy_version()` 非 B2 时未含 profile（归 W6）；app_form 计划创建未实现。

有界验证示例（额度恢复后只做一次）：
```bash
echo '只回复两个字：可用' > /tmp/probe.md
DS_MAX_TURNS=1 scripts/ds-worker.sh review /Users/zhangshijie/Desktop/Project/Fintech2026-wt/main-baseline /tmp/probe.md probe-<日期>-1
```
然后看 `.ds-logs/probe-<日期>-1.json` 的 `is_error=false` 且 modelUsage 只有 `deepseek-v4-pro`。

## 5. 这几天踩过的坑（W4c 期间查明）

- **测试时钟**：w3b 夹具把网关时钟固定在 2026-10-24（WALL），真实 L3 用真实时间核 deadline。W4c 夹具在 `tests/gateway/w4c/db/support.py` 用 `_use_real_clock` + `_anchor_to_real_time`；不要重锚 `read_records.observed_at`（会 UNKNOWN_SENSITIVE_ARG）。
- **读取记录新鲜度**：`services/gateway/app/pipeline/provenance.py:43–44` 要求读取记录 versions 等于当前快照；bump 客户 `state_versions` 会让 read 来源过期 → 复演 REPLAN（正确行为）。要造「可恢复的 STATE_CHANGED」只 bump L3 `clock.version`。
- **锁与死锁**：G8 读事务（含复演快照）持有 session 行锁；测试钩子里不要同步等待会改该行的另一连接，改用后台任务排队。跑测试一律加 `perl -e 'alarm 300; exec @ARGV'` 防挂死；超时被杀的测试会留下 `sbg-gateway-db-*`/`sbg-obligations-db-*` tmpfs 容器，只可清理属主进程已退出且无挂载的那些。
- **状态保护顺序**：`_protect_transition` 在 live job 存在时拒绝 plan 迁移，G8 写事务顺序是 plan→EXECUTING、claims、job、审计。
- **request_dedup**：键为具体路径 `POST /plans/<plan_id>/execute`；终态永不覆盖；非终态标记只表示「已用过」，旧 request_id 不得触发 attempt+1。
- **worker 常见失败**：用满轮数不提交（启动器会快照到 `refs/wip/<日志名>`）；红测自报「失败」但对旧代码其实通过（必须协调者亲自红绿）；越界改共享代码（如 pipeline/entity.py 跳过阶段）；为过测试放宽断言。复杂集成任务给模板文件+行号+「第 5 轮前建文件」的流程限制效果最好。
- 调试临时改动用备份文件还原，不要对含未提交成果的文件 `git checkout --`。
- `git worktree add` 用绝对路径（相对路径曾误建到 `Fintech2026-wt/Fintech2026-wt/`）。
- GitHub 偶发 `SSL_ERROR_SYSCALL`：重试一次或使用本地已有 `origin/main` 引用。

## 6. 本交接文件的提交方式

本文件与 `2026-10-26-codex-prompt.md` 以及 AGENTS §4 新日志在分支 `docs/codex-handoff-20261026` 提交并开文档 PR（只含 .md）。接手者核对后按 §1 的 CI 门禁合并即可。
