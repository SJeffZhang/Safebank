# 第二轮复测报告开发修复

基线：`8e2810e6764e3fd3e37406b31e694605751e0356`；现场代际 `demo_20261101131315_aca4156a`。原始及按PRD修正的报告归档于 `docs/reviews/prd-retest-report-2026-11-01/`，各有文件哈希，原24项CSV SHA256为 `50c8fad6565cc08f5b3cb9ac936a086c727834216f5a740a8d8c9e598e927da3`。

按需求归属报告，R1候选继续、R2建议入口属于定稿v2.1明确功能。R3包装是展示修复，R4名称/表格/日期是体验改进，R5澄清已有字段，R6修正文档冲突。历史8PASS/14PARTIAL/2FAIL混有展示判断，不能作为PRD功能通过率；本轮自动验证不回写历史现场结果。

## R1 / R2 / R3 / R4

三个Codex子agent在干净独立worktree的新分支并行开发，根代理集成及实际PG、最终完整CI。

- R1：作者`e0ea17d5a285a43a3304b600c1942af40cdda3a6`，集成`2fe862a`。合法hint/action null在HTTP与G20共用适配，只有此两kind转本地空字符串；typed null/非字符串/未知kind仍拒绝，G20仍权威。实际HTTP/编译图旧6红/11正控→17绿；作者Agent1089过/41DB条件skip。原38完整现场异常未保存，不将复现的兼容冲突写成唯一现场根因。见`retest-nullable-entry-2026-11-01.md`。
- R2：作者`15b9a6ad3507bdd2b2c1272f1f05391df0b062dc`，集成`61fd32c`。真实GatewayDecision/GatewayReplanHint.id映射HINT.ref，标签继续由G22a读取；真实wire/实际SSE旧红→33专项绿。见`retest-hint-card-2026-11-01.md`。
- 根代理追加真实gateway PG组合8通过：`GATEWAY_DB_TESTS=1 UV_CACHE_DIR=/private/tmp/sbg-uv-cache uv run --group poc pytest -q tests/gateway/w3b/db/test_nov1_hint_publication.py`。实际G4输出经Agent wire→generate_cards的action.ref→G22a→G3单次采纳→新G4→G9面板金额；包含原TTL/epoch/根/绑定篡改与并发拒绝负控，零execution_job。该项L3为已有快照替身，不是实际资金执行/浏览器/模型通过。
- R3：作者`d91fddf8bb74c98b91039576aa716d13eec3b93b`，集成`c0830b2`。只在显示副本按source+untrusted_paths精确去一层上下文包装，保留名称，原ReadRecord/污点不变；完整精确定位结果才抑制扫描卡，持仓名从成功产品读取按ID关联。作者旧10红/10正控→55卡片专项绿及Agent非DB1107通过/42deselected。见`retest-read-card-projection-2026-11-01.md`。
- R4：作者`5c86b0ce4e61ede51c6bcfc89ab21fa08c085b43`，集成`269e53f624c8bf726745493b450079598f394665`。客户卡片名称/整数金额/上海timestamp和date-only区分、来源ID不直接展示；纯React窄表格，无HTML/URL/图片/动作解释；无可靠名称只显示待核占位。作者旧10红/2正控、非法日期旧1红/14正控→15专项绿；完整FE491、BFF16、build过，既有source选择器断言失败及纠正保留日志。根代理合并R5后的完整FE **494通过/50文件**、BFF16通过、build通过；交叉FE独审待。
- 已完成独立评审：root预留/文档与B hint由A审，A入口由B审，B只读显示由C审，均APPROVE。独审原文在`docs/reviews/retest-repairs-2026-11-01/`；另有未参与实现的独立整分支/六场景29项需求映射审核，最终结论待精确提交固化。
- 根代理合并代码组合101项纯测通过；之前4938/1169skip默认套件运行与集成重叠，仅诊断，不是任何精确head完整CI回执。所有单元仍必须通过最终完整40位head全部CI才能发布。

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
