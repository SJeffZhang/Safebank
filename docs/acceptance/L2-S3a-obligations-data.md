# S3a 义务存储与真实快照 — 验收证据

2026-10-24。实现 worker（DeepSeek V4 Pro，无头 Claude Code）。工作树 `feat/l3-obligations-data`。
含独立评审（`docs/reviews/L2-S3a-implementation-review.md`）REQUEST_CHANGES 的修复轮：2 项 P1 + 7 项 P2。

## 实际运行（评审修复轮后）

| 命令 | 结果 |
|---|---|
| make lint | ruff check：All checks passed；ruff format --check：183 files already formatted |
| make test | 402 passed, 314 skipped，4.29s |
| make test-obligations | **30 passed, 0 skipped**，4.38s（真实 tmpfs PG16 + 真实 TCP HTTP） |
| make contracts-check | PASS: contracts v1 match frozen schemas |
| make test-gateway | 173 passed, 254 skipped，3.79s |

## 关键证据

- 独立临时 Postgres（唯一容器名/随机端口/显式 tmpfs），`upgrade head` 两次成功，容器退出即清理；
  未操作任何数据卷。
- 最新 metadata 与迁移后 PG `compare_metadata`（补 accounts_current_card_fk 后）零差异；
  历史 `0001_core.py`/`0002_identity.py`/`schema_v1.json` SHA256 与提案一致。
- **P1-2 真实执行链**：真实 TCP `/internal/plans/prepare` + `/execute`（正确 InteractiveEvidence）提交
  300000 转账 → `/internal/snapshot` JSON → `BankState` → S2：`max_outflow=295500`、`reserve=4500`；
  双方 state_version 各增一次、intent COMMITTED、plan_execution/operation/outbox 真实落盘。
- **P1-1 0002 升级保留**：独立 tmpfs PG 先 `upgrade 0002_identity`，用真实 ledger/persist 生成
  posted/pending/transactions，并以 P1 ExecutionService 行形（raw SQL）落 PREPARED/COMMITTED/CANCELLED
  intents、evidence_history、plan_execution、operation/outbox、客户/clock 版本；采集基线 →
  `upgrade head` 两次 → 逐表行/序列/权限逐字段相等；再真实 prepare/query/reconcile/replay 全通过。
- OWN/OTHER 所有权与第三客户非递归、RR 并发一致性、多 offset 混用、billing inactive 不预留、
  F1 DELETED/DISABLED 历史保留、instruction_runs 只追加与 bank_ro 只读均已在真实 PG/HTTP 覆盖。

## 未覆盖（留后续组件，不属本组件范围）

R7/R10/R11 读取、1–30 天展示、signed max_outflow 公开契约、scheduler 与三项定时写、
完整 baseline_v1 数据安装。完整细节见 `docs/progress/S3a-obligations-data.md`。
