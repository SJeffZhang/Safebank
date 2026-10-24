# W4b2c 验收：G10 OTP 发送与红级 G6 OTP 校验（2026-10-25）

- 分支 `codex/w4b2c-otp`，基线 main 72a4161，之后同步 main 0472689。
- 任务书 `docs/plans/briefs/W4b2c-otp.md`，以末尾的协调者裁定 R0–R6 为准；设计笔记 `W4b2c-design-notes.md`。
- 实现：本机 Claude Code CLI 的 DeepSeek worker（SID db370e6e）。实现提交为 e99d60a、64d8f09、a99995a，修复轮为 0c4ed23。
- 独立评审：DeepSeek 只读 worker（SID e3c3877b），报告 `docs/reviews/W4b2c-otp-review.md`。首轮 REQUEST_CHANGES（P1×1、P2×4），修复后复审 APPROVE。

## 范围

- **G10 发送 OTP**：
  - 6 位验证码，由 `secrets` 生成；2 分钟有效；绑定 approval_id 与 view_hash。
  - 同一审批最多发 3 次，间隔 30 秒，不足间隔返回 429 RATE_LIMITED，并带 retry_after_seconds。
  - 重发后前一个码作废。
  - 验证码以 AEAD 密文持久化：HKDF 按用途派生两把子密钥，AAD 绑定 approval_id、plan_id、view_hash 与 challenge id。明文不进入响应、日志、审计、异常或 repr。
- **红级 G6 确认**：
  - 码不对返回 OTP_INVALID，并累计错误次数；缺码、码已作废或已过期返回 OTP_EXPIRED。
  - **错误次数跨重发累计，满 5 次锁定**为 OTP_LOCKED。
  - 校验用 `hmac.compare_digest`；otp_challenges 与 approvals 均加行锁。
  - W4b2b 原先「红级一律 503」的处理已替换，对应拒绝路径的测试覆盖保留。
- **OTP 密钥**：通过 `adapter.otp_hmac_key` 取 runtime 实例快照，不用全局状态。
- 无新迁移，未改 contracts；新增 `make test-w4b2c`，并已加入 CI。

## 协调者亲自核验（0c4ed23）

| 命令 | 结果 |
|---|---|
| make lint | 316 files |
| make test | 2533 passed / 640 skipped |
| make test-gateway | 2017 passed / 580 skipped |
| make test-gateway-db | 17 passed |
| make test-shared-runtime | 79 passed |
| make test-w1 / w4b1 / w4b2b | 1688 / 57 / 58 passed |
| make test-w4b2c | 56 passed（30 纯函数 + 26 真实 tmpfs PG），零 skip |
| make contracts-check / actionlint | PASS / exit 0 |

红绿核验：

| 临时改动 | 结果 |
|---|---|
| 重发时把继承的错误次数改为 0 | 1 failed |
| adapter 的 OTP 密钥改为固定全局值 | 4 failed |
| G10 审计的 plan_id 改回 approval_id | 1 failed |

每项改动均已还原，还原后全部通过。

过程记录：worker 首轮自行修改了 AGENTS.md，已由协调者 revert；它还尝试过 git push，被启动器拦截，没有推送出去。

## 已知问题 / 未覆盖

- **继承 W4b2b 的 P2-4**：approvals→sessions 的锁序与 supersede 路径之间可能出现 ABBA 死锁。PG 会检测并中止其中一方，可以安全重试。待 W3b 统一锁序时处理。
- **演示/D 接口读取验证码**不在本组件范围内，属于 W5/D 接口。
- **未运行** make up / test-integration；合并后由协调者在 main-baseline 跑组合回归。
