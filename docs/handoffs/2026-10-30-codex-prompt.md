你接手 BankSense（2026 深圳金融科技大赛「银行 AI 智能体」赛题，截止 2026-11-02），成为唯一协调者兼实现者。

工作目录：/Users/zhangshijie/Desktop/Project/Fintech2026（worktree 都在 ../Fintech2026-wt/）。

按顺序阅读：
1. docs/handoffs/2026-10-30-codex-handoff.md（本次交接方案，现场快照、未完成工作、顺序与验收）
2. AGENTS.md §2（不可违反的约束）与 §8（工作规则）
3. docs/plans/demo-wiring-plan-2026-10-30.md（演示计划全文）

关键规则：
- 不使用 DeepSeek worker（scripts/ds-worker.sh），实现你亲自做，独立评审用你自己的只读子代理。
- 合并 PR 前必须用 docs/handoffs/2026-10-30-tools/local_ci.sh 在精确 head 上跑本机 CI 全部通过；修复类 PR 还要在干净环境 safebank-e2e-1029 live 跑通。
- 安全路径（网关 taint/pipeline/policy/approval、执行与资金）改动必须独审，不许放宽 L2 规则。每个修复做红绿核验。
- 分支名用 feat/<层>-<功能> 或 fix/<层>-<功能>，不要带 codex。
- 每一步写进 AGENTS.md §4（执行者写 Codex）。不打印任何密钥；不删数据卷；DeepSeek 返回 402/403/429 立即停下告诉用户。

先执行交接文档第 5 节的「开工前核对」，然后从第 3.1 节（完成并合并 PR #96）开始。
