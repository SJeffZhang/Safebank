# W2a：真实 W1 来源链与 memo 裸符号组件验收

2026-10-24。范围：真实 W1 PIN/JWT/session/G3/load_for_plan/全局撤销与来源阶段联测；单独 ¥/￥ 且没有数字不升黄，含数字维持敏感提示，与 DENY/REPLAN 并存。L3 在该来源链专项仍是契约 fake，P1 实际仅实现 5/18 读和 7/14 写子集，不代表完整 L3 联调。

实现提交 `118603f`（基于 adeb4b5）；原 DeepSeek reviewer `08b9fe3a-e764-4f9b-8ac6-1b3bffaf9b78` 最终 APPROVE，P1 越界 trust gate 已撤回，P2 边界文案已改。报告 `docs/reviews/L2-W2a-real-integration-review.md`。

协调者本机六退出全部 exit 0：make lint；make test-gateway 1880 passed / 469 skipped；make test-gateway-db 17 passed；make test 2115 passed / 517 skipped；make test-w2 **234 passed、零 skip/xfail（8.52s）**；actionlint。实际日志 `/private/tmp/sbg-codex-w2a-r1-20261024`。独立 reviewer 定点运行 memo/provenance 106 passed。

原实现先恢复黄级并存断言，实际 12 failed / 87 passed 暴露额外 gate，再删除 gate 转绿。真实 W1 集成包含跨 sid/customer、登出失效、旧原话 superseded/根 claim、hint used_by、parent 追溯与澄清合槽；不再 monkeypatch fixture_revocation。

W2b 尚未实施：默认 reserve=None 继续显式阻塞，待 S3a 合并后接实际义务数据。真实 G4/W3、W4、演示/replay 尚未集成。组件 PR 不宣称完整 W2 完成，精确发布 head 的四 CI 需另核。
