# 第二轮复测报告开发修复

基线：`8e2810e6764e3fd3e37406b31e694605751e0356`；现场代际 `demo_20261101131315_aca4156a`。原始及按PRD修正的报告归档于 `docs/reviews/prd-retest-report-2026-11-01/`，各有文件哈希，原24项CSV SHA256为 `50c8fad6565cc08f5b3cb9ac936a086c727834216f5a740a8d8c9e598e927da3`。

按需求归属报告，R1候选继续、R2建议入口属于定稿v2.1明确功能。R3包装是展示修复，R4名称/表格/日期是体验改进，R5澄清已有字段，R6修正文档冲突。历史8PASS/14PARTIAL/2FAIL混有展示判断，不能作为PRD功能通过率；本轮自动验证不回写历史现场结果。

## R1 / R2 / R3 / R4

三个Codex子agent在干净独立worktree的新分支并行开发，根代理集成及实际PG、最终完整CI。精确提交、专项、独审与集成证据将在各单元完成后追加。

## R5：计划前预留与计算状态

保留原转出路径的 `scheduled_reserve_cents` 为计划前七日义务；新 `reserve_projection_available` 明确区分实际计算的0与未计算默认0。纯取消、赎回、冻结等非转出动作最佳努力展示同一计划前值；投影缺失不增加拒绝保护动作的规则。既有转出路径缺义务仍503失败关闭，B1跳过预留并标false。前端兼容无新标志的旧记录，显示“未计算”；true才显示“计划前未来7天预留”。未改L3、账本规则、审批/OTP或原始资金。

- 新纯测试正确夹具后的旧实现失败：取消、货币赎回实际4500显示0，真实0/未知/B1缺计算标志；前端3红/5既有正控。最初cancel夹具缺SubscriptionView、apply_plan传字典及超契约金额修正不计产品红。
- `UV_CACHE_DIR=/private/tmp/sbg-uv-cache uv run --group poc pytest -q tests/gateway/w3a tests/agent/gateway/test_wire.py`：79通过/8条件跳过，不把跳过当真实PG通过。
- `UV_CACHE_DIR=/private/tmp/sbg-uv-cache make test-w3a`（正式两个DB flags）：**64通过/0跳过**。新实际PG/L3 HTTP快照测试验证取消前4500及计算标志、Agent wire接受、计划影子预演后重新快照完全一致/实际代扣仍ACTIVE，无银行写入。
- `npm test -- --run src/mobile/Approval.test.tsx src/mobile/Approval.material.test.tsx`：16通过。覆盖已算值、false、旧记录缺标志、无写请求及既有审批/OTP行为。
- 受影响Python ruff check/format通过；独立资金/安全只读评审和最终完整head CI仍待。

## R6：异常数量文档一致性

原v2.1 §2.2的三条计数与已发布§8.2的canonical输出冲突。本轮仅将§2.2明确为三个故意预埋场景，且显式注明2026-11-01分类修复后的后续计数口径：九月精确014/015/016/017共4条，九月起至基准时刻另加012共5条。原种子字节、生成器执行逻辑、阈值与检测算法均不改；完整集合及整数基线证据沿用已归档 `nov1-canonical-seed-expectations` 单元。此处是对后续文档冲突的修正，不把4/5描述成10月23日原始要求，也不修改旧head现场报告。

## 环境与未执行边界

发现另一会话演示结束后留下两个整栈运行，已只停止 `safebank-e2e-1029` 七个容器，全部容器、卷与代际保留；`safebank`保留为唯一完整运行栈。未reset、删库卷、清理worktree/WIP、调用真实DeepSeek、执行浏览器演示/录制/断网彩排或tag。用户在另一对话进行新版本演示；本轮不把自动测试或健康检查当现场24/43项通过。
