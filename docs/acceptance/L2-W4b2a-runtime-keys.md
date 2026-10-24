# W4b2a 验收：审批 / OTP 密钥运行时快照（2026-10-24）

- 分支 `codex/w4b2a-runtime-keys`，基线 main 8ad47d0；任务书 `docs/plans/briefs/W4b2a-runtime-keys.md`。
- 实现：本机 Claude Code CLI DeepSeek worker（SID 3718d6b6）。4decdfd 为实现，6db2090 为评审 P2-1 修复轮，只改测试。
- 独立评审：DeepSeek 只读 worker，报告 `docs/reviews/W4b2a-runtime-keys-review.md`，APPROVE，P0/P1 无，P2-1 已修。

## 范围

显式 Settings 提供两项独立的 HMAC 密钥（`APPROVAL_HMAC_KEY`、`OTP_HMAC_KEY`），由单实例 GatewayRuntime 持有并安装到两个 app。以下配置在分配 DB/L3/锁资源之前拒绝（503 DEPENDENCY_UNAVAILABLE，消息不含原值）：缺失、空白、少于 32 字节、两项相同、与任一服务令牌相同。两个 runtime 并存时互不串用，也不调用全局 `approval.service.configure`。

**不包括**：G6 确认、OTP 挑战/验证、查询端点、审批库的实例适配（下一组件）。

## 协调者亲自复跑（6db2090）

| 命令 | 结果 |
|---|---|
| make setup | exit 0 |
| make lint | 291 files |
| make test | 2423 passed / 561 skipped |
| make test-gateway | 1982 passed / 501 skipped |
| make test-gateway-db | 17 passed（真实临时 PG） |
| make test-shared-runtime | 79 passed，零 skip |
| make test-w1 | 1688 passed |
| make test-w4b1 | 57 passed |
| make contracts-check | PASS |
| actionlint | exit 0 |

红绿核验：临时删除 runtime.py 中的服务令牌复用守卫后，`test_approval_keys.py` 4 failed / 16 passed；还原后 20 passed。

只核对了变量名，没有读值：本机长期密钥 `gateway.env` 已包含两项变量，与 `scripts/keys.py` 生成、Settings 读取的名称一致。

## 未覆盖

- 未运行 make up / test-integration；本组件未改 compose、镜像或 L3。合并后由协调者在 main-baseline 跑组合回归。
- 远端 CI 以 PR 实际运行为准。
