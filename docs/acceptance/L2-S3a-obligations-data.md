# S3a 义务存储与真实快照 — 验收证据

2026-10-24。实现 worker（DeepSeek V4 Pro，无头 Claude Code）。工作树 `feat/l3-obligations-data`。

## 实际运行

| 命令 | 结果 |
|---|---|
| make lint | ruff check：All checks passed；ruff format --check：182 files already formatted |
| make test | 391 passed, 309 skipped，5.34s |
| make test-obligations | 25 passed, 0 skipped，6.07s（真实 tmpfs PG16 + 真实 TCP HTTP） |
| make contracts-check | PASS: contracts v1 match frozen schemas |
| make test-gateway | 173 passed, 254 skipped，4.39s |

## 关键证据

- 独立临时 Postgres（唯一容器名/随机端口/显式 tmpfs），`upgrade head` 两次成功，容器退出即清理；
  未操作任何数据卷。
- 最新 metadata 与迁移后 PG `compare_metadata`（补 accounts_current_card_fk 后）零差异；
  历史 `0001_core.py`/`0002_identity.py`/`schema_v1.json` SHA256 与提案一致。
- 真实 TCP：张三→王五 300000 后 `max_outflow=295500`、`reserve=4500`；295500/295501 → 缺口 0/1。
- F1 矩阵模型+DB 双层：`SUCCESS ⇒ instruction_id 非空` 均拒绝无授权成功，BLOCKED 可存。

## 未覆盖

R7/R10/R11、1–30 天、signed max_outflow、scheduler/定时写、完整 baseline_v1 数据安装留后续组件。
完整细节见 `docs/progress/S3a-obligations-data.md`。
