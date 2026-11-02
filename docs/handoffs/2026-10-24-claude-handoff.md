# BankSense Gateway：Codex → Claude 详细交接（2026-10-24）

## 0. 交接边界与最新用户决定

这是一份本机项目交接快照，不是新的阶段验收报告。时间均按 Asia/Shanghai（北京时间）解释。2026-10-24 本次仅核对 Git、已有日志、任务书、自动化状态和进程，并编写文档；没有重跑业务测试、请求模型、派发 worker、提交、推送或合并。

**最新直接指令：用户先要求“暂停所有的定时任务”，随后要求准备详细交接文档和 prompt，准备把工作交接回 Claude。**

- `safebank-worker`（BankSense P2→P3 自主推进）：已通过 Codex app 工具改为 `PAUSED`，本次再次读配置确认。
- `skillloop`（SkillLoop 本地模型下载检查）：本来即为 `PAUSED`，保持暂停。
- 不因接手、余额恢复或旧 heartbeat 提示自动恢复任何定时任务。用户明确要求恢复后再操作。
- 原有后端 P2→P3 逐组件推进与合格组件发布授权有历史记录；这次 Codex 只做交接。Claude 收到用户发送的接手 prompt 后按其中范围工作，不能把“写交接文档”理解成 Codex 此刻继续开发。
- 前端 App、BFF 继续暂停，必须用户亲自下令才开工；与自动化暂停是两件独立的事。

**当前结论：P0 完成、P1 完成；P2 未真实验收闭合；P3 未正式闭合；完整六场景产品未完成。** 对用户说过的 L1 约 50%、L2 约 60% 只是按 P2 后端工作量粗估，不能作为阶段验收比例或工期承诺。

## 1. 首先阅读什么，如何处理旧记录

1. 完整阅读 `/Users/zhangshijie/Desktop/Project/Fintech2026/AGENTS.md`：§2 不可违反约束、§3 当前状态、§4 最下面的日志、§8 工作规则。
2. 阅读 `/Users/zhangshijie/Desktop/Project/Fintech2026/PRD.md`：尤其 §6 L1/路由表、§7 L2、§8 L3、§9 锁定接口、§10 评测、§13 P2/P3 退出条件、§15 DoD。
3. 阅读本文件和 `/Users/zhangshijie/Desktop/Project/Fintech2026/docs/plans/P2-P3-autonomous-2026-10-24.md`。
4. 阅读当前组件任务书、对应验收与原独立评审报告；不要只看 worker 最后一句“通过”。
5. 阅读 ADR-006/007/008/009，尤其执行 deadline、请求身份、STATE_CHANGED 屏障和真实义务快照。

AGENTS 是当前状态与工作规则的唯一来源；本文件是更详细的证据索引。存在矛盾时核对代码、原始证据，并先修正 AGENTS 当前摘要，保留所有历史日志。

注意历史文案：

- `docs/plans/L2-resume-2026-10-24.md` 起始 `aa42a0c`、中途 `0c2499d` 等是旧检查点。
- `docs/plans/briefs/W3-W4-integration-order.md` 开头“尚未开工”、部分旧 NotImplemented 描述是写任务书时的背景。现在 W3a、W4b1 已合；依赖顺序仍有效，完成状态看最新记录。
- PRD §13 早期“P0 尚未执行”及 §15 未勾清单是规划背景，不推翻现有 P0/P1 验收；也不能反过来把文档定稿视作代码完成。
- 某些旧 README 仍写 live 未运行；实际 C1b live 结果以 `docs/acceptance/L1-C1b-deepseek-adapters.md` 和原始 JSONL 为准。
- 旧历史“W4b1 57 真 PG”已在 AGENTS 纠正：**57 专项总数 = 34 纯函数 + 23 真 PG**。不删除原错误记录，不再复述错误口径。

## 2. 本机位置、Git 与未提交文件

```text
ROOT=/Users/zhangshijie/Desktop/Project/Fintech2026
WT=/Users/zhangshijie/Desktop/Project/Fintech2026-wt
仓库=https://github.com/SJeffZhang/Safebank
GitHub 账户=SJeffZhang
当前 root 分支=main
当前 root HEAD=8ad47d0bd1b3d4c7ca6b1e389c39afad7d231432
原 Codex 协调会话=01a16a07-9ebf-7480-b912-606f82cde72c
worker 统一日志目录=$WT/.ds-logs
```

本次读取本地 Git，未重新联网 fetch/查询 GitHub；远端合并/CI 结论来自此前已核验的记录。Claude 开始新的发布前必须重新核远端精确 head，不能把本交接当未来 CI 状态。

### 交接开始前的 root WIP

```text
 M AGENTS.md
 M docs/plans/P2-P3-autonomous-2026-10-24.md
?? docs/plans/briefs/C1c2-gateway-routing.md
?? docs/plans/briefs/W4b2a-runtime-keys.md
```

本次交接另外创建 `docs/handoffs/2026-10-24-claude-handoff.md` 与 `docs/handoffs/2026-10-24-claude-prompt.md`，以及下述历史验收日志副本/哈希清单，并同步 AGENTS/当前计划/旧接续计划的入口说明。**这些文档没有提交推送。** 两份新任务书是关键未跟踪文件，切换机器或仅从 GitHub 克隆不会自动得到它们；本机接手须直接读取当前文件。

不要使用 `git reset --hard`、`git clean`、盲目 checkout/restore 或整份覆盖 AGENTS。不要为“清爽交接”清理 worktree、stash 或日志。合并 AGENTS 冲突时保留双方历史，再更新最新摘要。

### worktree 快照

以下目录均在 `$WT` 下；本次 C1/main-baseline/W4/W3/W2/W0-foundation 的 `git status --short` 均为空。其余目录只盘点位置和 HEAD，没有在本次重新确认干净状态。

| 目录 | 分支/用途 | HEAD | 接手处理 |
|---|---|---|---|
| C1 | `codex/c1c2-gateway-routing` | `fd7fdd910d09e5a26b699cb4a635fa683a439df0` | C1c2 只有任务/协调文档，402 后无业务改动；基于旧 main b281023，恢复实施前安全同步最新 main |
| main-baseline | detached，协调者全栈基线 | `8ad47d0bd1b3d4c7ca6b1e389c39afad7d231432` | 干净；全栈只能由唯一协调者在这里串行运行 |
| W4 | `codex/w4b1-planview` | `a496f5d8826b7befcf0ef52f89ae39bd1669e81e` | 已合 PR #22；新 W4b2a 应从最新已验 main 建/更新分支，旧分支保留 |
| W3 | `codex/w3a-stages` | `8ed6abd342dab61efaaab5d9181e3ee08d04feb6` | 已合 PR #20；不重复实施 W3a |
| W2 | `codex/w2b-reserve-integration` | `ba3a6aa5a3bdf3ca953c676adaca14aad2aed05b` | 已合 PR #18；不重复派发 |
| W0-foundation | `feat/l2-foundation` | `b8836b5dddb1e95740f337268b2063cf9e43d47a` | 本机长期数据卷匹配密钥来源，必须保留 |
| W1 | `codex/worker-launcher-fix` | `efcf29c` | 历史保留，非下一项实现入口 |
| S3a | `feat/l3-obligations-data` | `c10d827` | 已合 PR #15，历史保留 |
| S1f | `feat/l2-demo-route-guard` | `9cb9a41` | 已合，历史保留 |

C1/main-baseline/W4/W3/W2 的 `.secrets` 当前均软链到 `../W0-foundation/.secrets`。只核对软链位置与 ignore 状态，不打印或复制真实密钥；不能重新生成一套凭据配现有数据卷。

root 现有 10 个 stash 均保留。本次最靠前两条：

- `stash@{0}` / `421a880`：`coordinator-pr22-ff-preserve-20261024`。
- `stash@{1}` / `466b7cc`：`coordinator-pr21-ff-preserve-20261024`。

索引会变化，以 message/hash 核实。历史备份目录包括：

- `/private/tmp/sbg-main-before-pr22-ff-20261024`
- `/private/tmp/sbg-main-before-pr21-ff-20261024`

临时目录不是永久备份保障；确需迁移时审查后归档文档和无敏感信息的证据，不把 `.env`、密钥或全量私密会话打包进 Git。

## 3. 当前进程与唯一协调者

已知 C1c2/W4b1/C1c1 原 worker 都已有完整结果文件，历史记录确认其结束；不要把完整结果文件本身当“此刻绝无新进程”的充分条件。

本次额外读取进程快照，发现 **两个 Claude CLI 均在 ROOT 工作目录**：PID `82427`、`95066`。二者参数不含 `--output-format json`，也不含已知 C1c2 或 W4b1 原会话 ID；不能认定是上述旧无头 worker，也不能只凭目录认定谁是新协调者。另有 Claude 桌面主进程。未停止或修改任何进程。

因此 Claude 接手第一步必须核实当前活动会话/文件所有权，确定唯一协调者；**不要双开协调者同时 merge/main 同步/跑全栈，或并发写同一个 worktree**。PID 是瞬时快照，接手时重新读取，不凭旧 PID kill。

Codex 本次交接只写文档，完成交接后停止项目写入；不再恢复定时跟进或派发业务 worker。

## 4. 不可改变的架构与运行规则

- L1 LangGraph → L2 唯一工具入口 → L3 FastAPI/Postgres；两个 Docker 网络与凭证隔离必须真实维持。
- LLM 不决定授权；DeepSeek V4 Pro 是已选业务模型，图片输入才用已约定 Flash。不得为绕过余额不足换模型。
- 金额使用整数 `*_cents`，份额 Decimal/numeric；预演不写 L3，预演与执行复用 bankcore 纯函数。
- 攻击是否成功按 L3 数据库状态判断，不按助手回复判断。
- 前端/BFF 暂停；测试用 F 服务令牌 + app JWT 代替 BFF，L1 只能 A 服务令牌 + assistant JWT，不能取得确认/OTP 权限。
- 实现与独立评审继续由**本机 Claude Code CLI / DeepSeek V4 Pro** worker 完成。接手主会话是协调者，只拆任务、写文档、亲验、发布；不要默认改成主会话直接写业务，也不要拿自身普通 subagent 代替指定 DeepSeek 实现/评审。
- worker 本地提交后冻结；实现与评审使用分离目录，reviewer 只写获准报告，不改业务。修复交原实现会话；同 worktree 一次只有一位写入者。
- 全栈只由唯一协调者在 main-baseline 串行运行；worker 不运行全栈、不 push/开 PR/merge。
- 长期业务卷/数据不得删除、重建、reset、TRUNCATE；不得 `docker compose down -v`、volume rm/prune/system prune。专用新建 tmpfs 测试 fixture 的受控清理与长期业务数据不同，须核实命令范围后才执行。
- `make test-p1` 的脚本会 fresh 专用测试库；即使历史有授权，也必须先核实目标库/凭证与数据范围，不能照搬到长期银行业务库。
- 后台 Docker CLI 即可，不启动 Docker Desktop 前台界面。
- 固定依赖与 contracts v1 不随意改；必要协议增量先写提案、PRD/ADR/任务书，再由 owner 实施。不能靠降断言/加 skip/默认成功补缺依赖。

## 5. 已完成能力与证据边界

### 5.1 P0 / P1

P0 已验证环境、双网/凭证/DB CONNECT 隔离、密钥、DeepSeek 接入及关闭思考的 strict 回退，证据 `docs/acceptance/P0.md`。业务 DeepSeek 的 strict 使用 `/beta`，当前约定关闭思考；不要从旧“默认开启思考”事实误推本项目生产适配器开启思考。

P1 已合 PR #1（`d22b855`）：18 读/14 写契约齐全；实际领域写是转账、收款人和卡片，核心 5 读、快照、账本、原子执行/恢复真实可用。20 并发、双向互转、整计划回滚、幂等、迟到/对账、重启恢复有真实 HTTP/PG 证据。

- P1 最终 116 单元、33 真 PG；并发专项 5、一致性专项 17 为重叠子集，不相加。
- Linux 首次初始化 0600 SQL 权限失败已由 `70dcabe` 修复，远端 CI run `39929405919` 成功。
- `docs/acceptance/P1.8.md` 与 `docs/acceptance/p1-final/` 保留完整输出。
- 完整 baseline_v1 演示种子、AA、定时业务、订阅写、基金、时钟推进、完整评测/demo 不属于 P1 已实现范围。

### 5.2 L2 已合的关键组件

| 组件 | 完成内容 | 证据文件（ROOT 下） |
|---|---|---|
| W0/S1/S1b/c/d/e/f/W4a | 基础运行层、迁移/受控状态、PIN Settings、memo 风险 code、孤儿 job 对账、G22 条件挂载、客户锁 | `docs/acceptance/L2-S1*.md`、`L2-W4a-customer-locks.md`；部分进度见 `docs/progress/` |
| W1 PR #13 | 身份、会话、原话、撤销与授权生命周期；1688 专项，含真 PG | `docs/acceptance/L2-W1-identity.md`、`docs/reviews/L2-W1-final-review.md` |
| S2/S3a PR #15 | 七天预留纯函数、义务存储与真实快照；真实 L3 转账后读取/预留，30 真 PG/HTTP | `docs/acceptance/L2-S2-reserve.md`、`L2-S3a-obligations-data.md` |
| W2a PR #16 / W2b PR #18 | 读代理、来源/污点、真实预留和派生来源接线；W2b 246 专项 | `docs/acceptance/L2-W2a-real-integration.md`、`L2-W2b-reserve-integration.md` |
| W3a PR #20 | 快照/预演/工作流/决策四阶段；45 真 PG/HTTP；非完整 G4 | `docs/acceptance/L2-W3a-stages.md`、`docs/reviews/W3a-stages-review.md` |
| W4b1 PR #22 | 确定性 PlanView/content_hash、显式 HMAC 审批创建库；57 专项（34 纯函数+23 真 PG）；非确认/OTP/G8 | `docs/acceptance/L2-W4b1-planview-binding.md`、`docs/reviews/W4b1-planview-review.md` |

W2 验过余额 600000、预留 4500、派生 295500/295501 边界和取消月费 4500 的真实来源；不等于取消业务写已实现。W3a 共享唯一快照和 bankcore，已有累计预演、跨会话历史和只收缩 hint；完整评估 ID/revision/持久化由 W3b 补。

W4b1 原业务最终 `c317f82e4487183e1359c08c515aa4aa0fed7ad6`，最终发布 `a496f5d8826b7befcf0ef52f89ae39bd1669e81e`，合入 main `8ad47d0`。已修复评估/plan 混插、同评估并发唯一索引错误、执行中审批 supersede、CLAIMED 无 job、坏 MAC 幂等快速返回和 session-first 锁序。

### 5.3 L1 已合

| 组件 | PR | 能力与限制 |
|---|---|---|
| C1a | #17 | LangGraph/AgentState/身份和中断恢复骨架；当时 22 专项 |
| C1b | #19 | DeepSeek strict 理解、只读 gather、plan、有界纠错；当时 83 Agent 专项；最终 7 次真模型请求通过 |
| C1c1 | #21 | G20/G21/G2/G4/G8 协议客户端、严格 wire、context 凭据、有限 timeout、禁止 redirect、安全错误；Agent 总 212 项 |

验收/独审：`docs/acceptance/C1a-agent.md`、`L1-C1b-deepseek-adapters.md`、`L1-C1c1-gateway-client.md` 及 `docs/reviews/C1*-review.md`。

C1b live：两条理解、80000 分转账计划、冻结、1000 分限额及两轮 gather，最终 7 请求成功；read_executor 仍 fake。`docs/acceptance/c1b-deepseek-live/results.jsonl` 保留 55 行历史失败/成功，不能删旧失败洗绿。小样本不能证明完整业务或长期模型稳定性。

C1c1 协议测试主体 MockTransport；另有 3 个真实 localhost 默认 transport 测试，仅合成 HTTP 服务，非真实 L2/L3。曾修复真实 httpcore timeout 参数对象类型错误与重定向凭据泄漏；不要回退为仅 mock 全绿。

### 5.4 仍未完成

- 生产审批/OTP 密钥 runtime 接线、G6/G7/G9/G10/G17 等确认/OTP/查询端点。
- W3b 完整 G4，W4c 真实 G8/G5 与 UNKNOWN 恢复。
- C1c2 完整图路由，C1d PG saver/服务，C1e 输出/卡片/回放。
- ADR-004 真实录制/回放验收；ADR-005 当前暂停状态说明。
- P2 真三层验收、P3 A1/A2/A3 集成攻击验收；W5/W6、其他业务场景、完整评测与提交物。
- `make demo`、`make eval`、`make reset` 在当前 Makefile 明确 exit 2；不能当可运行入口，也不要改成假成功。

## 6. 最近一次 main 实际回归

精确 main `8ad47d0bd1b3d4c7ca6b1e389c39afad7d231432`，原始日志 `/private/tmp/sbg-main-pr22-regression-20261024/`，文件时间 2026-10-24 09:42–09:43 北京时间。本次交接核对了这些原始输出，没有重跑。

为避免临时目录清理造成证据丢失，本次将这 12 份日志经过凭据模式扫描后**逐字节复制**到 `ROOT/docs/handoffs/evidence/main-8ad47d0/`。同目录 `manifest.json` 记录原始目录、精确 HEAD、各文件字节数和 SHA-256，并明确标记为既有日志副本、非新测试。复制日志不扩大原测试覆盖范围。

| 命令 | 已记录结果 |
|---|---|
| `make setup` | 78 依赖；包含 poc 组 |
| `make lint` | 290 文件格式检查，check/format 通过 |
| `.venv/bin/pytest tests/agent -q` | 212 passed、零 skip，2.63s |
| `make test-gateway` | 1962 passed、501 skipped，7.77s |
| `make test-gateway-db` | 17 passed，1.98s |
| `make test` | 2403 passed、561 skipped，10.46s |
| `make test-w4b1` | 57 passed、零 skip，3.02s |
| `make test-w3a` | 45 passed、零 skip，3.33s |
| `make test-w2` | 246 passed、零 skip，10.41s |
| `make test-obligations` | 30 passed、零 skip，4.41s |
| `make contracts-check` | PASS，v1 无漂移 |
| `actionlint` | exit 0 |

套件存在重叠，不累计总数。默认 suite 的 PG skipped 不算通过，真实 PG 必须看显式专项。义务数据测试存在 accounts/cards FK 环 SAWarning，保留披露；不能声称“完全无警告”。此轮不是新的完整六容器业务 demo 验收；全栈 P1/重启/隔离证据另在 P1 与 S3a 的验收报告。

环境陷阱：`make setup` 实际是 `uv sync --frozen --group poc --python 3.12`。曾误用不含 poc 的 `uv sync` 导致 LangGraph 收集失败，已恢复并重跑；不要用系统 Python 或省略 poc 再把缺依赖当代码回归。

## 7. 当前阻塞、worker 原会话与日志

### 7.1 DeepSeek 402 是尚未解除的外部阻塞

C1c2 于 2026-10-24 约 09:30 北京时间启动失败：

```text
API Error: 402 Insufficient Balance
request_id: 2a8ecf86-82b8-44a7-9276-229d31358ac5
session_id: 1562cb21-2895-4dbe-938d-add2573d7c95
```

原始文件：`$WT/.ds-logs/C1c2-gateway-routing.json`、同名 `.err`/`.launch.log`。JSON 外层 `subtype="success"`，但 **`is_error=true` 且 result 为 402**；外层标签或进程 exit 0 不能当工作成功。

本次重新读取确认上述错误，C1 工作区干净，无业务实现。没有新的余额恢复证据；不要无限重试、反复 probe 或换模型。若用户已充值/已有可信成功调用证据，再作一次有界验证并续原会话。若仍 402，记录后停止依赖该 API 的实施/评审，先做不受影响的核查与文档。

### 7.2 需要保留的会话

| 用途 | 实现 SID | 独立评审 SID | 最后结果日志 |
|---|---|---|---|
| C1c2（未实施） | `1562cb21-2895-4dbe-938d-add2573d7c95` | 未创建 | `C1c2-gateway-routing.json`（402） |
| C1c1（已合） | `77635624-b849-47fc-8494-b4c5d0759303` | `7ae51822-bcba-4b17-be82-40b8f6a095a4` | `C1c1-fix-r2.json` / `R-C1c1-fix-r2.json` |
| W4b1（已合） | `11b6cf9f-1aaf-4433-a7ae-9e46d13bf899` | `1f1c599c-1037-4abc-8862-87d62758e07a` | `W4b1-fix-r2.json` / `R-W4b1-fix-r2.json` |

日志目录均 `$WT/.ds-logs`，原提示稿在其 `briefs-2026-10-24/`。若修已合组件发现的问题，续原实现/评审，保留发现→红测→修复→复验链；不要借新会话丢掉历史。

### 7.3 启动器与凭据

使用 ROOT 的 `scripts/ds-worker.sh`，先读脚本。接口：

```text
bash scripts/ds-worker.sh impl   <worktree> <prompt-file> <new-log-name> [resume-session-id]
bash scripts/ds-worker.sh review <review-worktree> <prompt-file> <new-log-name> [resume-session-id]
```

- 启动器清除继承的 `ANTHROPIC_*`、`CLAUDECODE` 等变量，再用本机 Claude 配置路由到 DeepSeek。直接 `claude -p` 可能错误使用桌面账户环境。
- 不打印 `~/.claude/settings.json`、`.env` 或任何真实 key。以结果 JSON 的 `modelUsage` 核对 `deepseek-v4-pro[1m]`；CLI 估算 Claude 价格不代表 DeepSeek 实际费用。
- prompt 的相对路径按 worktree 解析，推荐传绝对路径。
- **必须用新的 log-name**，避免 `>` 覆盖历史结果；同一 SID 可配新的修复轮日志名。
- 脚本权限白名单不是业务安全授权；其中 `make:*`/Docker 工具仍须遵从任务书禁止全栈/长期数据修改的约束。
- reviewer 工具允许写报告，不等于允许改业务；评审目录与实现目录必须分离。
- 只在结果完整、进程结束、HEAD 匹配、业务工作区冻结后接手亲验/发布；不能读半截 JSON 就继续。

## 8. 下一步按什么顺序做

### A. L1：C1c2 → C1d → C1e

**C1c2 任务书已存在且未跟踪：**`ROOT/docs/plans/briefs/C1c2-gateway-routing.md`。日志内有历史 prompt，但当前任务书优先；恢复前从最新 main 安全同步，保留原分支/文档。

C1c2 范围：

- G20/G21 权威 ingest；不信调用方伪造 text/scope；null hint/action 不洗成 typed 原话。
- 每次调用从 runtime context 取 JWT，不能入 checkpoint/提示/异常或复用上一客户 token。
- 真实客户端桥接 read，保留 source_ref/untrusted_paths/versions/observed_at。
- REPLAN 定向只读白名单，实际 read 至多 3 次，自动修订与 422 共用最多 2 次预算；不自动改用户金额。
- STATE_CHANGED/POLICY_CHANGED 直接用 `details.decision`，不重复 evaluate，有界重入；UNKNOWN 不盲重提、不宣称失败/成功。
- 精确 owner 与逐行路由测试见任务书；L2 G4/G8 此时仍缺真实服务，模拟 HTTP 边界必须明确标 fake。

C1d 再做真实 PG saver/线程锁/服务入口/恢复，C1e 再做输出校验、掩码、卡片和录制回放；尚无验收完成记录，不提前勾选。

### B. L2：W4b2a → 确认/OTP 端点 → W3b → W4c

**W4b2a 任务书已准备、未派发且未跟踪：**`ROOT/docs/plans/briefs/W4b2a-runtime-keys.md`。

- 只把显式 Settings 的两项独立 HMAC key 快照接到单实例 GatewayRuntime/两 app。
- 缺失、短值、相同或复用服务 token 等配置在分配 DB/L3 资源前拒绝；repr/错误不泄 key。
- 两个 runtime 并存必须无串用；不是调用全局 `approval.service.configure` 就算实例隔离。
- 不读/重生成真实 `.secrets`，不增加新 wire/算法，不顺手开确认或 OTP；精确文件白名单与完整退出命令看任务书。
- W4b2a 只解决资源先决，下一小组件要明确设计审批库的实例适配，再实现确认/OTP/查询端点。

之后：

1. **W3b**：真实 W1 G3 → W2 来源链 → W3 四阶段 → W4b 面板/审批，持久化 evaluation/revision；请求幂等、原话消费、预算、hint、审计失败与复演。审批不能用 fake。
2. **W4c**：真实 G8/G5 与恢复工作器，按 ADR-008、S1b/S1e CAS/屏障实现；验证迟到竞争、失锁、取消、attempt/evidence 不变、UNKNOWN 无重复记账。
3. 不整份 cherry-pick 旧 `3bcbe8e`/`3856a4c` 覆盖 main，特别不能覆盖现行 locks/state/迁移；旧代码只作只读参考。

两线只有在文件所有权清楚、无共享写冲突、资源可控时才并行；用户已强调架构和稳健优先于并行速度。

## 9. 每组件的实现、验收与发布门禁

1. 先写最小任务：输入输出、owner 白名单、依赖、明确 fake/live/PG 边界、退出命令和失败条件。必要规格缺口同步 PRD/ADR。
2. DeepSeek 实现，正常/非法/边界与故障测试；必要反例先红后绿；本地提交并停止冻结。
3. 协调者核完整结果/进程/HEAD/工作区，**亲自复跑任务书全部退出**及必要真实 transport/PG/live，不能只采 worker 自报。
4. 另 worktree 的 DeepSeek 独立评审。发现交原实现 worker 修，等结束后再评审、亲验；不同时写同目录。
5. 合最新 main，保留所有 WIP/日志/历史。核代码、测试、构建、依赖 diff；必要组合回归，不能用同步前结果冒充同步后实跑。
6. 敏感路径/凭据扫描、`git diff --check`、scope 审查后 push，开 base main PR。记录/附加 PR 到协调会话（若接手环境支持）。
7. 对**同一完整候选 SHA**核四项：push 的 unit/p1-postgres，pull_request 的 unit/p1-postgres，均必须 `COMPLETED SUCCESS`。不能只看名称相同的两个 check 或旧 head。
8. 用 Actions API 验 `head_sha` 与 `event`；CI 运行中退出等待下次有界核对，不开 `watch` 或密集循环。定时器仍暂停，不能为等 CI 自动恢复。
9. 精确合并：`gh pr merge <PR号> --repo SJeffZhang/Safebank --merge --match-head-commit <完整SHA>`。不 squash、不绕门禁。
10. 安全同步 main，main-baseline 实际组合回归，写验收与 AGENTS §3/§4/§5，再拆下一组件。

CI 文件是 `.github/workflows/checks.yml`，**不是 `ci.yml`**。现有两个 job 名不改。p1-postgres 已运行 bootstrap、全栈、gateway DB、locks、state、runtime、W1、义务、W2、W3a、W4b1、P1、并发、一致性、integration、contracts。

最新三 PR 的已核历史记录：

| PR | 候选 head | push / pull_request run | merge |
|---|---|---|---|
| #20 W3a | `8ed6abd342dab61efaaab5d9181e3ee08d04feb6` | `40027749533` / `40027756074` | `26dea40677375934813d7f9167a7b7fbcc17d821` |
| #21 C1c1 | `3f9e3b3dfca64a50cebc765e99f1d7a0e026b512` | `40048747791` / `40048752091` | `b281023dabdb0c93516f068d27e08fa4f3f3da1b` |
| #22 W4b1 | `a496f5d8826b7befcf0ef52f89ae39bd1669e81e` | `40050106828` / `40050112005` | `8ad47d0bd1b3d4c7ca6b1e389c39afad7d231432` |

这些已合组件不要重复开 PR；新的待发 PR 状态接手时另核，本次未联网盘点。

## 10. P2 真闭合与 P3 门禁

不得把 L2 子组件全部写完、测试总数多、独审 APPROVE 或容器 healthy 当作 P2 完成。

P2 至少逐项真实执行并留下精确 SHA/命令/数据库断言：

- [ ] 真 L1 理解/读取/计划 → 真 L2 G3/G4/PlanView → 真 L3 黄级转账。
- [ ] 确认前 L3 无写；F + app JWT 取面板、匹配 view_hash 并确认后 resume/G8，精确整数分和收款人正确，只记一次。
- [ ] L1 无审批/OTP 权限；错误 view_hash、错配审批、跨 customer/sid、过期、注销/旧原话拒绝且 DB 不变。
- [ ] 实际双网可达性与凭证隔离，L1 不能绕过 L2。
- [ ] 真 agent PG checkpoint、进程重启 resume、线程锁/旧 interrupt 替代；存储不含 token/key。
- [ ] PRD §6.1.1 返回码路由、STATE_CHANGED 直接接新 decision、UNKNOWN 恢复无重复记账。
- [ ] 输出金额校验、长数字掩码、确定性卡片，不把未执行/不确定写成成功。
- [ ] 真实模型最小样本与录制来源、ADR-004/005、main 组合回归、独立审核、`docs/acceptance/P2.md`，无阻塞遗留。

全部闭合后才记录 P2 完成并进入 P3：

- A1 拆分转账：按预先定义用户确认策略验证 step-up，排除余额/预留等干扰。
- A2 备注注入及复述：按 L3 payee/定时/转出状态判攻击，不看 LLM 话术。
- A3 跨会话危险操作序列：含换序、无卡储蓄变体，证明 B1 三步执行与 B2 拒绝的真实差异。
- 来源/复述、意图、累计预演、跨会话规则逐组件核验；独审与 `docs/acceptance/P3.md`。未跑不报 ASR/TSR/FPR 或“全面完成”。

前端暂停替代不豁免 P2 的确认、OTP 权限、数据库/网络/恢复等安全门禁。余额/权限阻塞要如实登记，先推进独立事项，不伪造验收。

## 11. Claude 接手第一轮建议交付

1. 只读核实本机 ROOT/各 worktree HEAD、WIP、已有 Claude 进程与唯一协调者、两项自动化仍暂停；不要先启动代码 worker。
2. 阅读上述资料，核对 402、最新 main 退出日志与两个未跟踪任务书；列出已完成/未完成/外部阻塞。
3. 用户已经给出充值或成功调用证据时，有界验证后恢复 C1c2 原会话，并另按 owner 顺序安排 W4b2a；否则保持 API 依赖工作阻塞，不换模型、不自作主张用主会话实现。
4. 继续时遵从最小组件、亲验、独审、四 CI、main 实际组合回归；前端与所有定时器维持暂停。
5. 每完成一个单元及时更新 AGENTS；没有实质变化不重复写“仍等待”日志。接手后如实说明当前阻塞，不反复向用户索要已授权的普通确认。

本交接没有授权删除旧资料、恢复定时器、更换模型、重建数据或跳过真实阶段门禁。
