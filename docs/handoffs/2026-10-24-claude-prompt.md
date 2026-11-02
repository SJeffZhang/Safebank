# 可直接发送给 Claude 的接手 Prompt

下面代码块完整复制到 Claude。它用于让 Claude 接手协调，不会自动恢复 Codex 定时任务。

```text
请接手 BankSense Gateway 的后端协调工作，先完整读取本机交接材料、核实现场，再按既有授权和门禁继续。不要只回复“已了解”或重新做一轮泛泛的架构规划。

一、位置与先读资料

项目：/Users/zhangshijie/Desktop/Project/Fintech2026
worktrees/worker日志：/Users/zhangshijie/Desktop/Project/Fintech2026-wt
仓库：https://github.com/SJeffZhang/Safebank，GitHub账户使用SJeffZhang。

依次读：
1. 项目AGENTS.md全文，重点§2、§3、§4最新日志、§8。
2. docs/handoffs/2026-10-24-claude-handoff.md全文。
3. PRD.md，重点§6/6.1.1、§7、§8、§9、§10、§13的P2/P3退出条件和§15。
4. docs/plans/P2-P3-autonomous-2026-10-24.md。
5. docs/plans/briefs/C1c2-gateway-routing.md、W4b2a-runtime-keys.md，以及W3-W4-integration-order.md。
6. 当前相关验收、独立评审及ADR-006/007/008/009。

文件均相对上述项目根目录。不要只从GitHub克隆接手：本机有未提交/未跟踪的重要计划和交接文件。旧L2-resume计划里的早期SHA、未开工文案不能当最新状态；以AGENTS最新记录、实际代码、原始证据核实。

二、最新用户决定与职责

我刚要求暂停全部定时任务。safebank-worker和skillloop都应保持PAUSED，不因接手、额度恢复或旧heartbeat内容自行启用。前端App和BFF也继续暂停，除非我亲自下令。

我把后端协调交回给你。沿用既有逐组件推进及发布授权：P2真实验收闭合后才继续P3，不需要每个普通步骤再问我“是否继续”。但余额/权限等外部阻塞须如实登记，不能拿授权当作外部条件已经恢复。

你作为唯一协调者，只做拆任务、写文档、核查、亲自复跑验收、发布和记日志。具体业务实现及独立评审仍交给本机Claude Code CLI、DeepSeek V4 Pro的独立worker，使用scripts/ds-worker.sh；不要自行改成主会话写业务或普通subagent替代指定DeepSeek实现/评审。不要因为现在交回Claude就擅改模型或工作分工。

三、当前准确状态

本地main：8ad47d0bd1b3d4c7ca6b1e389c39afad7d231432，最新已合PR #22。
P0、P1已完成。P1实际业务是转账/收款人/卡片、核心读取/快照、账本、原子执行及恢复；18读/14写是契约齐全，不能说六场景全部业务已实现。

L1已合：C1a PR17图骨架；C1b PR19真实DeepSeek适配；C1c1 PR21网关协议客户端。C1b最后7次真实模型请求通过，但read executor仍fake，不是真实三层闭环。当前Agent回归212项。C1c2完整图路由、C1d真实PG saver/服务、C1e输出/卡片/回放仍待完成。

L2已合：共享运行层/锁/受控状态，W1身份原话，S3a义务与快照，W2b来源/真实预留，W3a快照/预演/工作流/决策阶段，以及W4b1确定性PlanView/HMAC审批创建库。W3a不是完整G4，W4b1不是生产确认/OTP或G8。运行密钥、确认/OTP端点、W3b真实G4、W4c真实G8/UNKNOWN恢复仍待完成。

P2未真实验收闭合，P3也未闭合；不要按测试数量、容器healthy或库组件完成就标阶段完成。完整六场景、demo/eval和性能/安全评测仍未完成。L1约50%、L2约60%只是此前对P2后端工作量的粗估，不是验收指标。

四、第一步先核对现场，防止双协调和重复派发

保留root全部WIP、未跟踪任务书、worktrees、10个历史stash和.ds-logs。不得git reset --hard/git clean或整份覆盖AGENTS。合并日志冲突必须保留双方历史。

main-baseline在兄弟worktree目录，HEAD同main且上次检查干净。C1当前codex/c1c2-gateway-routing，HEAD fd7fdd910d09e5a26b699cb4a635fa683a439df0，只有任务/协调文档，尚无C1c2业务改动；恢复前安全同步最新main。W4旧分支a496f5d已合PR22，不要重复发布。

交接时发现本机还有两个工作目录为项目根目录的Claude CLI，PID曾为82427/95066，均非已知headless JSON worker；它们可能是交接会话，不能擅自kill。PID只是快照，先确认哪个会话持协调权，保证一个协调者、每个worktree一个写入者；不要两个会话同时跑全栈或merge。Codex本轮只写交接文档，交接后不继续派业务任务。

五、当前阻塞和恢复方式

C1c2在2026-10-24约09:30北京时间启动即失败：DeepSeek API 402 Insufficient Balance，无业务变更。原结果：
/Users/zhangshijie/Desktop/Project/Fintech2026-wt/.ds-logs/C1c2-gateway-routing.json
原实现SID：1562cb21-2895-4dbe-938d-add2573d7c95。
JSON subtype可能是success，但is_error=true且result是402，不能算成功。

目前没有余额恢复证据。先核对是否已有我充值/恢复的说明或可信成功调用证据；没有则不反复probe、不无限重试、不换模型。先完成不依赖API的核查/文档，准确报告阻塞。已有恢复证据时仅作一次有界验证，然后续原C1c2会话，用新log-name保存，不覆盖旧日志。

ds-worker.sh会清除继承的ANTHROPIC变量并用本机配置走DeepSeek。先读脚本；不要直接运行可能走错账户的claude -p，不打印任何key、.env或~/.claude/settings.json。核modelUsage为deepseek-v4-pro[1m]，不能把CLI估算Claude价格当真实DeepSeek费用。

六、下一组件顺序

L1：按C1c2任务书做G20/G21权威ingest、context安全read、定向有界REPLAN、完整返回码路由。STATE_CHANGED/POLICY_CHANGED直接用details.decision而不重复evaluate；UNKNOWN不盲重提、不写成功。然后才C1d真实PG持久化/恢复/服务、C1e输出/掩码/确定性卡片/回放。

L2：W4b2a运行密钥资源任务已准备未派发。仅显式Settings→实例Runtime的审批/OTP密钥快照，缺配置前置拒绝、两实例隔离；不要把调用全局approval.configure当实例隔离，也不要顺手做端点。之后另拆审批库实例适配与确认/OTP/查询端点，再W3b真实G4，最后W4c真实G8/G5及UNKNOWN恢复。

旧W3 3bcbe8e、旧W4 3856a4c只读参考，不整体覆盖现行locks/state/迁移。只有owner文件无冲突时才并行，稳健优先。

七、实现、验收、评审、发布门禁

每组件：明确输入输出/白名单/依赖/真实与fake范围/退出命令→DeepSeek实现并本地冻结→你核完整结果、进程结束、HEAD与工作区→亲自复跑所有退出和必要真实PG/HTTP/live→另目录DeepSeek独审→问题交原实现worker修复→再审再验→敏感扫描→push并开base main PR。

必须精确完整候选SHA的push和pull_request两事件，各unit与p1-postgres，共四项全部COMPLETED SUCCESS，且Actions的head_sha/event核对无误，才gh pr merge --merge --match-head-commit完整SHA。不用watch或密集轮询；运行中退出等待下一次有界核对，定时器仍暂停。合后安全同步main，在main-baseline实际跑组合回归，写AGENTS与验收，再开始下一组件。

实现与评审使用分离worktree；未结束worker不接管，不重复派发。修复保留原会话。没有实际执行不能写通过，skip/fake/未实施入口不能算真实验收。

最近已验main十二退出：setup78依赖、lint290、agent212、gateway1962+501skip、gateway-db17、all2403+561skip、W4b1 57（34纯函数+23真PG）、W3a45、W2 246、义务30、contracts/actionlint通过。原始日志/private/tmp/sbg-main-pr22-regression-20261024，时间约09:42–09:43北京；交接目录docs/handoffs/evidence/main-8ad47d0另有逐字节副本和SHA-256清单。这是既有证据，不是你已复跑。套件重叠不能相加，skip不算通过。

使用make setup安装poc依赖；省略--group poc曾导致LangGraph缺依赖。CI实际文件.github/workflows/checks.yml。make demo/eval/reset当前明确未实现exit2，不能伪装可用。

八、数据与安全硬约束

三层/双网/凭证隔离、L2唯一授权、LangGraph、DeepSeek V4 Pro、整数分/Decimal、bankcore纯函数复用都不改。L1不能拿F/app审批权限或银行写令牌。前端/BFF替代测试继续用F服务令牌+app JWT，L1仅A+assistant JWT。

现有长期业务卷/数据不得删除、重建、reset、TRUNCATE；禁止down -v、volume rm/prune。仅新建独立tmpfs fixture可按任务书清理。全栈只你一个协调者在main-baseline串行执行，worker不跑全栈、不push/PR/merge。make test-p1会fresh专用测试库，先核目标不能误碰业务数据。所有worktree仅用匹配本机长期数据卷的W0-foundation/.secrets，不重生/复制/打印密钥。Docker只后台CLI，不启动Desktop界面。

九、P2/P3结束条件

P2必须真实L1→L2→L3黄级转账：确认前DB无写；F/app取L2面板并匹配view_hash确认后resume/G8真实记账且只一次；审批错配/跨客户/过期/错误hash拒绝DB不变；验证实际双网、PG checkpoint重启、输出、STATE_CHANGED/UNKNOWN恢复。完成真实录制/回放及ADR004/005、main组合回归、独审和docs/acceptance/P2.md，无阻塞遗留后才标P2完成。

然后P3真实验A1拆分、A2备注注入/复述、A3跨会话危险序列及变体，按L3状态判成功、B0/B1/B2差异如实报告，不报未实测ASR/TSR/FPR，不因进度快跳门禁。前端暂停不豁免安全验收。

请先完成只读接手核查，给我一个简洁的“现场是否一致、谁持协调权、当前阻塞、下一最小组件”结论；在授权与外部条件具备后直接推进，不反复询问已经授权的普通步骤。若仍402，明确停在何处，完成不受影响事项后等待，不伪造通过、不无限重试。定时任务与前端始终保持暂停，除非我明确恢复。
```
