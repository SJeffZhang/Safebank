# W2b：生产预留和真实义务派生验收

2026-10-24。业务最终4c84019（前252455c），原DeepSeek reviewer 08b9fe3a-e764-4f9b-8ac6-1b3bffaf9b78最终APPROVE。报告 docs/reviews/L2-W2b-reserve-review.md。

默认ProvenanceStage使用唯一bankcore.projected_shortfall(days=7)。缺义务/coverage/账户明确503，不猜0；负max_outflow不clamp。多步骤或同step参数循环内已有DENY时不让后续依赖503遮盖，非DENY依赖错误仍保持503。cancelled_monthly_fees校验真实订阅、同计划取消集合去重/周期/账户/本人储蓄。

协调者在4c84019亲验八退出全部exit0：make lint（246 formatted）；test-gateway 1890 passed / 471 skipped（7.95s）；test-gateway-db17（2.07s）；test2141 / 531 skipped（8.24s）；test-w2 **246 passed、零skip/xfail（10.84s）**；test-obligations30零skip（4.64s）；contracts-check PASS；actionlint。日志/private/tmp/sbg-codex-w2b-r1-20261024。

真实临时bank PG+实际L3 snapshot HTTP+gateway PG：295500派生匹配、295501不匹配，版本/模拟时刻变化拒绝旧读来源；真实持久化merchant billing/mandate→snapshot订阅视图→取消月费4500匹配，重复/缺订阅来源/跨账户/非本人储蓄/WEEKLY均拒绝。无fake reserve；来源测试不宣称未实现取消领域写、W3 Decision或W4资金执行。临时库TRUNCATE只在fixture新建tmpfs独立测试库内部，未碰长期业务数据/卷。

worker先红测循环内DENY两例503遮盖→修复后绿；原reviewer亲跑10定点与188非DB通过。既有FK环SAWarning保留。CI的p1-postgres追加make test-w2，Makefile启用OBLIGATIONS_DB_TESTS=1，确保真实专项实际执行，job名未改。

边界：本组件完成W2生产预留接线；完整G4/审批/execute/六场景与demo/replay仍待W3/W4/C1后续，make demo当前未实现。本地通过不代表远端CI，发布后当前head四检查需另核。
