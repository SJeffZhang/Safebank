# 11-01 PRD演示报告后的开发修复

以远端main `aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1` 为基线，已读取另一已结束会话的正式报告，按独立worktree并行实现、交叉只读评审、协调者串行集成。原43结果仍是19 PASS /16 PARTIAL /8 FAIL，CSV SHA256 `6c0d1ff29a08d0f16d5847b290e83d8938fb5e0482f2254ed492b9681dca5abb`。本文件记录开发与自动验证，不改写原现场结果。

## 原报告对应修复

| 原ID | 已修开发链路 | 核验入口 |
|---|---|---|
| 02 | 已登记近名前缀仅候选，明确选择才恢复原金额/源账户；父链不猜 | nov1-l2-query-cancel / nov1-clarification-ui |
| 07/08 | 只读不出空FORM；取消按真实payee_id与自有源账户，多条选择确切指令 | nov1-l2-query-cancel；真实G3/G19 PG |
| 10/15/24/29/31 | 正式只读NO_WRITE；保留实际读取数据、去页面协议包装，产品不因NAV历史挤掉后排 | nov1-l2-query-cancel / nov1-query-completion |
| 12 | 业务月流水另读权威消费汇总，55050分消费与40000分转账分列，编译图经校验不泛化金额 | test_nov1_query_compiled；原现场fallback唯一触发未留证，不做因果过度声明 |
| 13 | 最近180天范围明示，完整分页后以真实merchant_id读取filtered_all；不完整/歧义不说0或不存在 | test_query_gather / nov1-query-completion |
| 14 | 双月卡按各自真实月份，主月内嵌一致；历史分类统一权威MCC/类别 | nov1-clarification-ui / nov1-historical-categories |
| 17 | 当前卡单笔、日余限及可用余额分开；非ACTIVE不承诺可刷 | test_query_gather / nov1-query-completion |
| 25 | 非法span固定约束反馈、既有一次预算；耗尽只返安全SSE，provider限额立即停 | nov1-understanding-errors；真实Agent PG/SSE |
| 26 | fresh业务时钟和实际窗口，商户费、定时及reserve分列，不能漏200元定时 | nov1-query-completion / nov1-business-dates |
| 27 | 历史last_charge不冒称下次；未来日期仅取权威账单 | test_query_summaries |
| 30/39/41 | 固定六题测评/手工收款人登记/转账咨询页面引导；仍空写scope，不直接改风险/新增目标/转账 | nov1-l2-query-cancel / nov1-clarification-ui |
| 32 | 从实际完整目录唯一匹配两产品，真正调用compare_products | test_query_gather / nov1-query-completion |
| 34 | 失败或校验拒绝的持仓尝试不再假记成功，必需get_holdings仍补读；不猜持仓/来源 | test_query_gather |
| 34b | 持仓总估值名称正确，真实产品名/订单状态，上海trade_date；集合有界且按完整行省略并明示条数 | nov1-query-completion / nov1-historical-categories |
| 37 | X4严格tagged实体格式反馈，仅一次重提；不自动改ID/源/金额，不放宽来源 | nov1-x4-plan-shape-feedback |
| 38 | G21真实parent→G19→G3 action，仅typed SLOTS携clarification_id，重复/跨会话/卸载停止 | nov1-clarification-ui |
| 40 | 当前计划/会话/实际失败transfer步/墙钟TTL的真实收缩上限；无hint不编最大值，发布→读取→单次采纳仍需新预演及审批 | nov1-replan-hint-binding / nov1-hint-publication / ADR-013 |
| S4/X2 | COMMITTED取消同事务REVOKED，恢复一致；缺grant拒绝墓碑，懒回填不复活；正确已取消商户NOT_ACTIVE且通知一次 | nov1-cancellation-grants / nov1-s1-revocation-boundary |

## 补查六场景29需求

逐29项生产路径和已有测试审查见独立review-nov1-six-scenes。未现场覆盖的手机号/新收款人、各周期、其他卡渠道/补卡、问卷/基金生命周期及恢复分支，不能用原生补件或静态实现记作新的现场PASS。未发现的故障不作全覆盖保证。

补查与独审实际发现的问题已分别闭合：历史普通商户目录遗漏、估值标签、长集合显示预算、typed重复提交、REPLAN失败step/过期绑定、下降环比符号、测评公开有效期/合法午夜NAV基准与未来外行目标。最初两个直接给纯曲线/scorer UTC时钟的红，经生产loader正控证伪，已撤回；不改正确生产防线。

取消后的L3实际零扣费与网关grant状态不一致分开记录，不将原不一致虚构成已发生资金漏洞。旧库/卷/代际/WIP/worktree保留，仅CI safebank完整栈运行。此轮不做浏览器演示、生产模型请求、录制/断网、攻击/对比、tag或公开进度更新。

## 交付门禁

各组件旧失败/新通过、独审精确提交、fixture纠正及实际PG证据在关联acceptance；独审原REQUEST_CHANGES和后续复审同时保存。默认pytest的PG/provider条件skip不算通过，完整本机CI须另运行所有真实PG/并发/一致性/网络/重启门禁。

最终40位head、本机CI完整结果、合并SHA及合并后检查以本PR真实回执及本文件后续记录为准。所有已确认开发单元和独审追加项已通过专项，当前进入最终CI，不提前宣称发布或原43已通过；用户在另一会话重新跑原场景。

独审原报告与后续复审归档在docs/reviews/prd-repairs-2026-11-01，含完整29项审计、manifest及本机CI适配脚本。此目录不包含原始令牌/数据库口令或完整未脱敏请求。

## 完整CI追加修复与复验差异

b913首轮完整CI停于旧SSE.type断言；2b51下一轮停于旧hint fixture、近名fixture与新查询全表断言。两者都不是完整PASSED。修订保留服务端真实堆栈、未决执行/授权/used_by和零金融写断言，使用真实已绑定publisher来源及当前sid/全库不变双重隔离，不清空旧数据。

姓名带中文数字的真实新增边界已修：“赵六九百”不能切成赵+90600。唯一完整已登记目标与金额才使用，多合法切分/不规范连续数字保持空scope；root真实G3/PG旧3红→新3绿，另候选选择正向PG通过，金额90000和显式储蓄来源持久化核对，零执行。详见nov1-w1-correction-gates；新PG首轮重复固定request_id触发幂等保护已纠正夹具，未修改保护。

**复验期望差异**：原43句/顺序/旧CSV不改。统一八类后，原不变种子在9月实际异常为014/015/016/017四条；全历史另012，共五条，全部同crd_zs_1。三个故意预埋014/016/017仍在；新增为真实CATEGORY_OUTLIER，数据、金额/余额、阈值和生产信号均未改。PRD §8.2/§14已同步；重新问9月账单/异常（包括16/35、保护谓词）须依当前窗口与实际四/五条核对，不能用旧head三条期望截断。数学、数据字节/执行AST不变与精确SQL证据见nov1-canonical-seed-expectations。

正式make目标完整W2 328、L3 334专项通过；缺少其他PG flags的手工子集skip不作完整通过。最终全部CI必须在包含以上修订的新40位head从头运行。复验应fetch最新远端main并核对唯一运行栈源码；原Downloads checkout有他人AGENTS/output WIP，保持原样，不reset覆盖。

## 全部本机CI回执（完整代码）

`5139208740fe9fcd57e7ab2b2e7d695c704e8484` 全部35步骤 `LOCAL CI PASSED` / exit0。unit4918通过/1168条件skip；前端476、BFF16及build；真实PG授权/原话W1 2042、来源W2 328、义务334、Agent42、状态252；预演/审批/执行恢复、P1资金33/并发5/一致性17、真实网络隔离与L3进程重启恢复、S1 113/时钟6/通知52及冻结契约均通过。套件重叠不相加，skip不代表现场通过。

[完整聚合日志](prd-report-ci/5139208/local-ci.log)、[逐步骤原日志哈希与结构化回执](prd-report-ci/5139208/receipt.json)。聚合SHA256 `727d43fd651fb819cd05e0c8e1a5367d58df22f1b338a52a0f77ab5bbda52cbb`。未归档含连接口令的原始错误堆栈。保留库prd01–04的只读元数据一并归档。

该真实回执加入文档后，还须对最终含记录的完整40位head从头运行同一全部CI；最终head/合并SHA/恢复main与合并后检查由PR实际回执登记。这里不提前称最终head已通过或已发布；原43现场及冻结门禁由用户另会话复验。
