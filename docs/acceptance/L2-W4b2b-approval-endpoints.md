# W4b2b 验收：审批实例适配 + G9 / G6（黄级）/ G7 / G17（2026-10-24）

- 分支 `codex/w4b2b-approval-endpoints`，基线 main 45b9b45，之后同步 main 1ce15f0。
- 任务书：`docs/plans/briefs/W4b2b-approval-endpoints.md`，末尾的协调者裁定 D0–D6 优先于正文；设计笔记：`W4b2b-design-notes.md`。
- 实现：本机 Claude Code CLI 的 DeepSeek worker。首次实现 783a864 / f7c8b6e / 6acabb4，之后 5 轮修复：cea1d45、4aa690e、fdaf576、ee4628e、ef83931。
- 独立评审：DeepSeek 只读 worker（SID e79cabb1），报告 `docs/reviews/W4b2b-approval-endpoints-review.md`。首轮 APPROVE，含 P1×1、P2×3；复审 APPROVE，上述问题全部关闭，新增 P2-4（见下）。

## 范围

- **D0 实例适配**：端点从 `request.app.state` 读取该 app 的 runtime 密钥快照，不调用全局 `configure`。
- **G9** 返回持久化的 PlanView，并交叉校验 approval_id。
- **G6** 黄级确认：
  - view_hash 必须精确匹配；
  - 仅当 `risk_ack_required` 时才要求 risk_ack；
  - 红级审批在 W4b2c 完成之前一律返回 503 DEPENDENCY_UNAVAILABLE，DB 不变；
  - 确认后不执行，执行属于 W4c / G8。
- **G7** 拒绝。
- **G17** 列表：按 ULID 倒序分页，按 status 过滤。
- **G6/G7 的状态与事务**：
  - approvals 与 plans 在同一短事务内经 `state.transition` 迁移；
  - 使用 request_idempotent；审计用 append_tx；
  - sessions 行加 FOR UPDATE，与 W4b1 的加锁顺序一致。
- 无新迁移，未改 contracts。新增 `make test-w4b2b`，并已加入 CI 的 p1-postgres job。

## 协调者亲自核验（ef83931）

| 命令 | 结果 |
|---|---|
| make lint | 302 files |
| make test | 2428 passed / 613 skipped |
| make test-gateway | 1987 passed / 553 skipped |
| make test-gateway-db | 17 passed |
| make test-shared-runtime | 79 passed |
| make test-w1 | 1688 passed |
| make test-w4b1 | 57 passed |
| make test-w4b2b | 57 passed，零 skip（真实 tmpfs PG，连跑两次） |
| make contracts-check | PASS |
| actionlint | exit 0 |

红绿核验：
- **D0**：adapter 改为返回固定全局密钥后，test-w4b2b 10 failed；还原后通过。
- **P1 锁**：精确删除 sessions 行的 `.with_for_update()` 后，test_race 连跑 2 次均失败；还原后 2 次均通过。

过程中被协调者驳回的情况：
- 首轮实现声称没有 Docker、DB 测试未跑。协调者实测发现 45 errors、lint 57 处问题，CI 也没有接入。
- 修复轮 3 把一条回归说成原有失败。
- 修复轮 4 的锁测试，去掉锁后仍然通过，与 worker 自报不符。

## 已知问题 / 未覆盖

- **P2-4**：`_supersede_session`（W4b1）与 G6/G7 存在 ABBA 死锁的可能。PostgreSQL 会检测死锁并中止其中一方，可安全重试，不产生不一致状态。在 W3b 接通 supersede 路径时统一加锁顺序。
- 以下留给 W4b2c：G10 OTP、红级确认、`ConfirmRequest.otp` 字段。
- 未运行 make up / test-integration。合并后由协调者在 main-baseline 上跑组合回归。
