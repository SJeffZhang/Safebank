# W3b 验收：真实 G4 /plans/evaluate 端到端与全局锁序统一（2026-10-25）

- 分支 `codex/w3b-g4-evaluate`，基线为 main 0472689，先后同步 fe829d0 与 5c15d84。
- 任务书 `docs/plans/briefs/W3b-g4-evaluate.md`，以末尾协调者裁定 B1–B7 为准；设计笔记 `W3b-design-notes.md`。
- 实现：本机 Claude Code CLI 的 DeepSeek worker（SID a208bae0）。期间多次因 API 中断（ECONNREFUSED / 403 / 轮数上限），中途未提交的现场均备份在 `.ds-logs/wip-W3b-*`。
- 独立评审：DeepSeek 只读 worker（SID a97b1f5c），报告 `docs/reviews/W3b-g4-evaluate-review.md`。首轮 REQUEST_CHANGES（P1×1、P2×3），修复轮 6 后复审 APPROVE。

## 范围

- **G4 端到端**：W1 原话与授权输入 → W2 ①–④ → W3a ⑤–⑨ → 持久化 evaluation（ID、revision、真实 policy_version）→ 结论为 REQUIRE_APPROVAL 时，由 W4b1 create_for_evaluation 生成审批与 PlanView → 用 model_copy 回填 Decision（B3）。
- **各结论的状态**：REPLAN 也写入 evaluation（B2）；DENY 时 plan 状态为 DENIED（B4）；以上迁移都只经 `state.transition`；请求按 request_id 幂等。
- **B1 冷静期原子写入**：pipeline 只负责收集冷静期，G4 在同一写事务里先写冷静期、再写 evaluation 与审计，三者一起提交或一起回滚。
- **B5 全局锁序**：统一为 session → plan → evaluation → approval → otp_challenge。G6、G7、G10 都先读出 sid、锁定 session，再锁 approval，并复核 approval 未发生变化。这一项同时解决了 W4b2b 的已知问题 P2-4（ABBA 死锁）。
- 未新增迁移，未修改 contracts；新增 `make test-w3b`（33 项真实 tmpfs PG），并已加入 CI。

## 协调者亲自核验（b2f1df0）

全部 exit 0：

| 套件 | 结果 |
|---|---|
| lint | 329 files |
| test | 2535 passed / 702 skipped |
| test-gateway | 2018 passed / 612 skipped |
| gateway-db | 17 |
| shared-runtime | 79 |
| w1 | 1688 |
| w2 | 246 |
| w3a | 45 |
| w4b1 | 57 |
| w4b2b | 58 |
| w4b2c | 56 |
| w3b | 33 |
| obligations | 30 |
| agent-db | 31 |
| contracts | 通过 |
| actionlint | 通过 |

红绿核验：

| 核验项 | 临时改动 | 结果 |
|---|---|---|
| B5 端点锁序 | 分别对调 G6、G7、G10 端点内的两次加锁 | AST 守卫各 1 failed |
| B5 死锁语义 | 辅助函数按旧顺序调用 | 与 create_for_evaluation 产生 DeadlockDetected |
| B1 冷静期原子性 | `_persist_cooldowns` 改为独立事务提交 | 回滚测试 1 failed |
| P1 policy_version | 改回 `"sha256:test"` | test-w3b 1 failed |
| P2-3 端点测试有效性 | 端点测试改用错误 HMAC 密钥 | 2/3 failed（G10 不校验 MAC，见已知问题） |

## 协调者驳回过的情况（过程透明）

- 多轮中断后，改动长期没有提交。之后改为每次续接先提交检查点，再逐项提交。
- 两次锁序测试在旧代码上依然通过，属于无效测试：一次是测试自己按顺序调用辅助函数；一次是在生产代码中加了测试并未使用的钩子。最终改用 AST 守卫配合辅助函数级的死锁证明。
- worker 自报「全部 green」，但 test-w1 实际有 1 条失败（W1 的占位测试需要适配真实 G4）。
- 首轮实现把 policy_version 硬编码为 `sha256:test`。

## 已知问题 / 未覆盖

- G10 发送 OTP 时不校验审批的 binding MAC（P2，纵深防御问题）。OTP 唯一的消费方 G6 会先校验 MAC，因此无法借此铸造出可以消费的 OTP。
- 非 B2 profile 下，`_policy_version()` 未包含 profile 名（PRD §11.1 的要求），只影响 EVAL 模式的非 B2 profile，归 W6 处理。
- 不包含 G8 执行（属于 W4c）；端到端的 make up / test-integration 由协调者在 main-baseline 执行。
