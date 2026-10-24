# C1c2 验收：L1 完整图路由（2026-10-24）

- 分支 `codex/c1c2-gateway-routing`；任务书 `docs/plans/briefs/C1c2-gateway-routing.md`；进度 `docs/progress/C1c2-gateway-routing.md`。
- 实现：本机 Claude Code CLI 的 DeepSeek worker（SID 1562cb21），共 478b673 实现提交和 5 轮修复：1e303e7、786949d、a19fdf7、1287437、21cb904。
- 独立评审：DeepSeek 只读 worker（SID cc60f313），报告 `docs/reviews/C1c2-gateway-routing-review.md`。首轮 REQUEST_CHANGES（P0×2、P1×4），修复轮 1 之后 APPROVE，修复轮 2 之后 APPROVE，修复轮 3–5 之后 APPROVE；遗留 P2 不阻塞。

## 范围

- G20/G21 权威 ingest；PRD §6.1.1 返回码路由已在编译图中连线。
  - 422 回到 gather；STATE_CHANGED 直接用 `details.decision` 有界重入；202 UNKNOWN 固定模板，不重新提交；FAILED 固定模板。
  - 新增 clarify 节点。
- REPLAN 定向只读白名单；自动修订与 422 共享最多 2 次预算。
- read 桥接接入 gather，保留 source_ref、untrusted_paths、versions、observed_at；每次调用经 `GatherAdapter.with_read_executor` 使用独立实例，凭据只取 runtime context。
- **fake 边界**：G4/G8 真实服务尚未完成，相关测试使用 fake gateway HTTP 边界，测试中有标注，不算真实联调。

## 协调者亲自核验

| 命令 | 结果（21cb904） |
|---|---|
| make lint | 297 files |
| .venv/bin/pytest tests/agent -q | 287 passed，零 skip |
| make test | 2478 passed / 561 skipped |
| make contracts-check | PASS |
| actionlint | exit 0 |
| make demo | exit 2（未实现，如实登记） |

红绿核验：
- 编译图测试：把 `services/agent` 换回修复前 478b673 后，15 failed / 1 passed；还原后 16 passed。
- 并发凭据隔离测试：换回 1e303e7（全局 monkey-patch 版本）后，连跑 3 次均 1 failed；还原到 HEAD 后，3 次均 2 passed。
- 修复轮 3、4 的测试在旧代码上不失败，被协调者驳回，直到修复轮 5 才成立。

合并 main 45b9b45 之后的最终复跑见 PR 描述。

## 未覆盖

C1d（真实 PG checkpoint、服务、重启恢复）、C1e（输出校验、卡片、回放），以及与真实 L2 G4/G8 的联调。
