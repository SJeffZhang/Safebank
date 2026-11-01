# 第二轮复测报告开发修复

基线：`8e2810e6764e3fd3e37406b31e694605751e0356`；现场代际 `demo_20261101131315_aca4156a`。原始及按PRD修正的报告归档于 `docs/reviews/prd-retest-report-2026-11-01/`，各有文件哈希，原24项CSV SHA256为 `50c8fad6565cc08f5b3cb9ac936a086c727834216f5a740a8d8c9e598e927da3`。

按需求归属报告，R1候选继续、R2建议入口属于定稿v2.1明确功能。R3包装是展示修复，R4名称/表格/日期是体验改进，R5澄清已有字段，R6修正文档冲突。历史8PASS/14PARTIAL/2FAIL混有展示判断，不能作为PRD功能通过率；本轮自动验证不回写历史现场结果。

## R1 / R2 / R3 / R4

三个Codex子agent在干净独立worktree的新分支并行开发，根代理集成及实际PG、最终完整CI。

- R1：作者`e0ea17d5a285a43a3304b600c1942af40cdda3a6`，集成`2fe862a`。合法hint/action null在HTTP与G20共用适配，只有此两kind转本地空字符串；typed null/非字符串/未知kind仍拒绝，G20仍权威。实际HTTP/编译图旧6红/11正控→17绿；作者Agent1089过/41DB条件skip。原38完整现场异常未保存，不将复现的兼容冲突写成唯一现场根因。见`retest-nullable-entry-2026-11-01.md`。
- R2：作者`15b9a6ad3507bdd2b2c1272f1f05391df0b062dc`，集成`61fd32c`。真实GatewayDecision/GatewayReplanHint.id映射HINT.ref，标签继续由G22a读取；真实wire/实际SSE旧红→33专项绿。见`retest-hint-card-2026-11-01.md`。
- 根代理追加真实gateway PG组合8通过：`GATEWAY_DB_TESTS=1 UV_CACHE_DIR=/private/tmp/sbg-uv-cache uv run --group poc pytest -q tests/gateway/w3b/db/test_nov1_hint_publication.py`。实际G4输出经Agent wire→generate_cards的action.ref→G22a→G3单次采纳→新G4→G9面板金额；包含原TTL/epoch/根/绑定篡改与并发拒绝负控，零execution_job。该项L3为已有快照替身，不是实际资金执行/浏览器/模型通过。
- R3：作者`d91fddf8bb74c98b91039576aa716d13eec3b93b`，集成`c0830b2`。只在显示副本按source+untrusted_paths精确去一层上下文包装，保留名称，原ReadRecord/污点不变；完整精确定位结果才抑制扫描卡，持仓名从成功产品读取按ID关联。作者旧10红/10正控→55卡片专项绿及Agent非DB1107通过/42deselected。见`retest-read-card-projection-2026-11-01.md`。
- R4：作者`5c86b0ce4e61ede51c6bcfc89ab21fa08c085b43`，集成`269e53f624c8bf726745493b450079598f394665`。客户卡片名称/整数金额/上海timestamp和date-only区分、来源ID不直接展示；纯React窄表格，无HTML/URL/图片/动作解释；无可靠名称只显示待核占位。作者旧10红/2正控、非法日期旧1红/14正控→15专项绿；完整FE491、BFF16、build过，既有source选择器断言失败及纠正保留日志。根代理合并R5后的完整FE **494通过/50文件**、build通过。BFF首轮因沙箱监听127.0.0.1被EPERM阻断，最初记录过早写通过；随后获准本机完整重跑16通过/0失败，实际日志`/private/tmp/sbg-retest-integrated-bff-approved.log`。首轮中断不计通过。B独立FE评审APPROVE：精确作者提交隔离archive下完整499通过/51文件（含8独立负控），tsc过；不把其额外临时负控数算作项目基线数量。
- 已完成独立评审：root预留/文档与B hint由A审，A入口由B审，B只读显示由C审，均APPROVE。独审原文在`docs/reviews/retest-repairs-2026-11-01/`；另有未参与实现的独立整分支/六场景29项需求映射审核，最终固定`c8a881d5af6f46904a5df5367ef91b60c363e2bf`整合/需求审查APPROVE；独立101项Python、76项FE通过，附六场景29ID受影响边界映射，无可操作P0/P1/P2。
- 根代理合并代码组合101项纯测通过；之前4938/1169skip默认套件运行与集成重叠，仅诊断，不是任何精确head完整CI回执。所有单元仍必须通过最终完整40位head全部CI才能发布。

## R5：计划前预留与计算状态

保留原转出路径的 `scheduled_reserve_cents` 为计划前七日义务；新 `reserve_projection_available` 明确区分实际计算的0与未计算默认0。纯取消、赎回、冻结等非转出动作最佳努力展示同一计划前值；投影缺失不增加拒绝保护动作的规则。既有转出路径缺义务仍503失败关闭，B1跳过预留并标false。前端兼容无新标志的旧记录，显示“未计算”；true才显示“计划前未来7天预留”。未改L3、账本规则、审批/OTP或原始资金。

- 新纯测试正确夹具后的旧实现失败：取消、货币赎回实际4500显示0，真实0/未知/B1缺计算标志；前端3红/5既有正控。最初cancel夹具缺SubscriptionView、apply_plan传字典及超契约金额修正不计产品红。
- `UV_CACHE_DIR=/private/tmp/sbg-uv-cache uv run --group poc pytest -q tests/gateway/w3a tests/agent/gateway/test_wire.py`：79通过/8条件跳过，不把跳过当真实PG通过。
- `UV_CACHE_DIR=/private/tmp/sbg-uv-cache make test-w3a`（正式两个DB flags）：**64通过/0跳过**。新实际PG/L3 HTTP快照测试验证取消前4500及计算标志、Agent wire接受、计划影子预演后重新快照完全一致/实际代扣仍ACTIVE，无银行写入。
- `npm test -- --run src/mobile/Approval.test.tsx src/mobile/Approval.material.test.tsx`：16通过。覆盖已算值、false、旧记录缺标志、无写请求及既有审批/OTP行为。
- 受影响Python ruff check/format通过；独立资金/安全只读评审APPROVE；最终完整head CI仍待。

## R6：异常数量文档一致性

原v2.1 §2.2的三条计数与已发布§8.2的canonical输出冲突。本轮仅将§2.2明确为三个故意预埋场景，且显式注明2026-11-01分类修复后的后续计数口径：九月精确014/015/016/017共4条，九月起至基准时刻另加012共5条。原种子字节、生成器执行逻辑、阈值与检测算法均不改；完整集合及整数基线证据沿用已归档 `nov1-canonical-seed-expectations` 单元。此处是对后续文档冲突的修正，不把4/5描述成10月23日原始要求，也不修改旧head现场报告。

## 环境与未执行边界

发现另一会话演示结束后留下两个整栈运行，已只停止 `safebank-e2e-1029` 七个容器，全部容器、卷与代际保留；`safebank`保留为唯一完整运行栈。未reset、删库卷、清理worktree/WIP、调用真实DeepSeek、执行浏览器演示/录制/断网彩排或tag。用户在另一对话进行新版本演示；本轮不把自动测试或健康检查当现场24/43项通过。

## 首轮完整CI未通过（保留失败）

完整`1c601881590256af1b8d1251915f9dd52b66375c`从头运行，unit4977/1169条件skip、FE494/BFF16/build及GW17/锁63/状态252/共享79/AgentDB42/W1 2042/义务334/W2 328/W3a64均通过；W3b在旧并发夹具失败，立即停止，不称全CI通过。实际不是死锁，`create_for_evaluation`严格比较持久evaluation.preflight与view.model_dump时，旧手写夹具漏新增false计算标志而被正确拒绝。

错误夹具位于W4b2b共享seed及W4b1创建夹具；将按当前PreflightView构建默认值，保留显式金额/来源/哈希不匹配负控与生产严格比较。还要将旧死锁RED只认SQLSTATE40P01/55P03，排除把任意结构异常算死锁的假阳性。该测试修复由独立worktree实现，root实际PG核验后仍须新完整head从头全部CI。原失败聚合与receipt在`retest-report-ci/1c60188-failed`，敏感raw trace只留私有临时日志。P1尚未执行，当前bank_p1_test已无损保存在prd06，尚未执行fresh P1；其后核对发现make up已重新建零表测试库，详见下方保留纠正。

## CI夹具修复及实际复核

独立测试作者`9a68ddfc0f0cf78124c098b6bdbc4ea049c40217`（集成4cf3e5d）和`8ea8fb0eade5c1e9101f80dd1829ef2459042204`（集成ea78ed1）仅更新两处Schema默认夹具和两处并发测试的异常观测：旧approval-first RED只认Postgres40P01/55P03；done任务非锁异常必须传播，endpoint也必须等待成功后台任务。未改任何生产文件、锁顺序、超时、金额/哈希/审批或负控；旧失败日志保留。

- root实际 `GATEWAY_DB_TESTS=1 UV_CACHE_DIR=/private/tmp/sbg-uv-cache uv run --group poc pytest -q tests/gateway/w3b tests/gateway/w4b1 tests/gateway/w4b2b tests/gateway/w4b2c`：**322通过/0skip**，不是收集数。
- 集成endpoint补丁后root实际整个W3b：**88通过/0skip**；旧失败GREEN转绿且RED确有锁SQLSTATE，endpoint后台任务异常不再被忽略。
- 非作者D独审两提交APPROVE，97项纯hash/binding/panel测试过，确认显式不匹配负控与业务状态/OTP断言保留。报告归档retest-ci-fixture-review.md。
- 之前1c完整CI仍是FAILED，以上专项不能补成PASSED；接下来必须对新完整40位head从头重跑全部本机CI。

## 完整代码CI PASSED及发布回执

完整`9545148a12fff197beea350dbadbd2abb89fe4d7` **全部35步骤PASSED/exit0**，回执`retest-report-ci/9545148`。聚合SHA256 `9a6ee5500b272790a56bfd4df58ac6805527e0c77f454ca93e984d4ec84b3c97`；runner SHA256 `ea34d079200889de14516cd389c69f8ee9b09e8d1543855f9bf6263b0254818c`。

实际：unit4977/1169条件skip、FE494/BFF16/build；GW17/客户锁63/状态252/共享79/AgentDB42；W1 2042/义务334/W2 328/W3a64/W3b88/审批120/确认58/屏障56/SMS8/执行恢复48；E0b框架119；P1资金33/并发5/一致性17；实际网络隔离、L3进程重启后的结果恢复/幂等、冻结契约；S1 113/clock6/notify52全部通过。重叠套件不相加，条件skip/替身不算生产模型或现场通过。

保留过程纠正：第一次954重跑前发现bank_p1_test已被make up的provision重新建立（OID101382/零表/零连接），先前“未新建”的推断撤回。协调者在前置检查失败后误启动CI，已精准停止该runner及2个子进程于unit阶段，未进入PG；中止日志单独保留，不算通过。该零表库也无损改名prd07，原prd06 OID94369与prd07 OID101382均重核不变。只有确认源库缺席及两保留OID一致后，才再次从头运行并取得上述954完整PASSED。全部既有应用库、卷、其他worktree/WIP保留。

PR：https://github.com/SJeffZhang/Safebank/pull/114。本文及回执入库后的最终完整head仍须从头全部本机CI，通过才match-head merge commit并恢复main。**最终head、完整CI及合并/运行栈回执以该PR实际评论为准**，不由本文提前宣布。发布后的生产代码与上述已验954保持相同；原24/43现场结果不改，用户在另一对话按原PRD/后续明确口径，以新独立数据集复验。当前单栈项目safebank；若另一对话另起演示栈，先停止此栈并保留数据，避免并行两套整栈。
