# P2 审批实例密钥（G4 / STATE_CHANGED 创建审批）验收

日期：2026-10-27　分支：`codex/p2-approval-runtime`

## 缺陷
生产 `GatewayRuntime` 只把审批 HMAC 密钥装在 `app.state`，从不调用 `approval.service.configure()`；
G4 与 G8 的 STATE_CHANGED 屏障创建审批时读全局 `_config`，生产返回 503。旧 w3b 夹具手工 `configure()` 掩盖了该缺陷（E0b T5 真实测试暴露）。

## 修复
`create_for_evaluation` 接收实例 `key/profile/wall_now`；两处生产调用（`plans.py` evaluate、`execution.py` `_handle_state_changed`）传入 `app.state` 配置。
非 bytes / 短于 32 字节 / 无效 profile / 非 callable 或无时区 wall_now / 部分传参 → `GatewayError DEPENDENCY_UNAVAILABLE 503`，不回退全局。HMAC 消息、TTL、锁序、状态机未改。

## 测试（tests/gateway/w3b/db/test_g4_instance_keys.py，8 条真实 tmpfs PG）
- 无全局配置时 G4 用实例密钥成功并 MAC 可验证；双 app 不同密钥交叉验证失败；错误全局配置不影响实例；
  全局有效但实例缺 key → 503 且无审批行；缺 / 短 / 非 bytes key → 503；autouse fixture 恢复全局。
- 红绿：三处生产文件换回 00aca15 后 3 failed，还原后全过（协调者实测）。

## 合并 main 62e83ac 后协调者复跑（全部 exit 0）
lint 369；test 2772/755 skip；test-gateway 2018/657 skip；gateway-db 17；shared-runtime 79；wave1-state 231；
w1 1688；w3a 45；w3b 40；w4b1 57；w4b2b 58；w4b2c 56；w4c 38；agent-db 39；contracts PASS；actionlint。

## 评审
`docs/reviews/P2-approval-runtime-review.md`：APPROVE，无 P0/P1；两条 P2（显式测试、autouse 隔离）已在 f232066 补齐。

## 未覆盖
长期运行栈真实三层黄级转账（P2 主验收）尚未进行。
