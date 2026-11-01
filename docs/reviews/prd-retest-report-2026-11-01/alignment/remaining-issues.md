---
template: doc
theme: paper
lang: zh
title: SafeBank 剩余问题检查报告
subtitle: 2026-11-01 · 修复后复测证据汇总 · 仅报告，不实施修复
---
按原定稿PRD核对，当前证据有2类功能路径缺陷，影响02、38及40的建议交互；另列展示质量、体验建议、字段语义和文档冲突。不能将六类都称为“原需求功能未完成”。

需求依据与勘误见[逐项归属核对](/Users/jet/Downloads/WeBank/output/requirement-provenance-2026-11-01/REPORT.md)。原报告快照已保留。

## A 结论与报告范围

| 项目 | 结论 |
|---|---|
| 复测版本 | main 8e2810e6764e3fd3e37406b31e694605751e0356 |
| 演示代际 | demo_20261101131315_aca4156a |
| 复测范围 | 上轮8条FAIL及16条PARTIAL，共24个问题点 |
| 历史观察标签 | 8 PASS、14 PARTIAL、2 FAIL；FAIL为02、38；标签混有功能及展示判断，不是PRD功能通过率 |
| 核心恢复 | 原8条失败中的7条核心业务已恢复，38仍失败 |
| 本报告工作 | 整理既有现场、SQL回执和只读源码审查；没有新增演示或代码修改 |

14条PARTIAL包含业务结果正确但仍有展示问题的项目。40原句的超额拦截已正确；同一现场补充核验的PRD建议采纳入口未通。历史标签保留，不能据此说40安全拦截失败。

本报告针对上述固定版本，未重新测试后续代码。它不能替代完整43条复跑、全部PRD分支或冻结验收。

## B R1：候选选择后无法继续（高优先级）

**影响用例：02近名收款人、38余款理财。**

**需求来源：** 原定稿PRD [T-1](/Users/jet/Downloads/WeBank/PRD.md:561)、[X-3](/Users/jet/Downloads/WeBank/PRD.md:650)、[§7.4澄清流程](/Users/jet/Downloads/WeBank/PRD.md:1021)。归属为原功能路径缺陷；优先级是报告评估。

| 用例 | 触发步骤 | 现场结果 | 业务影响 |
|---|---|---|---|
| 02 | 输入“给王转200块”，点击“王五” | 候选正常，点击后提示“未能生成有效回复，请刷新查看实际业务状态” | 未生成200元预演或新计划 |
| 38 | 输入“把下周要扣的会员费留够，剩下的钱买稳健型理财”，点击“稳盈短债” | 产品候选正常，选择后出现同一公共错误 | 未生成新的申购预演或订单 |

**已确认事实：** 两次选择均已在网关登记为SCOPED，收款人或产品范围正确；银行已采业务投影没有变化。38的10955元是本次可用余额减45元扣费的独立算值，未进入真实预演。

**源码定位：** 合法action的text为null；BFF原样传入Agent。Agent入口使用 `u.get("text", "")` 构造要求字符串的UtteranceRef，不能处理“字段存在但值为null”。后置适配虽能转空串，但入口校验先于它执行。

这是确定的静态兼容冲突，与现场失败阶段吻合。02保留了ValidationError及main.py:500的脱敏栈；38未保存完整异常栈和SSE，不能认定两例均已证实唯一根因。

**复验标准：** 用真实浏览器action请求经过BFF和Agent入口，选择后能进入正常读取及预演；原金额、目标、父链与单次使用约束保持正确，最终确认前资金不变。不能仅用前端成功SSE模拟证明全链路通过。

证据：[02现场与定位](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/02-choice-error-review.md)、[38现场与定位](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/38-choice-error-review.md)、[38错误页面](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/screens/38-choice-error.jpg)。

## C R2：有效建议没有采纳按钮（高优先级）

**影响用例：40“给王五转50000元”。**

**需求来源：** 10月23日最早入库的定稿PRD已有该功能。原文见[§3.4提示按钮及点击后新一轮](/Users/jet/Downloads/WeBank/PRD.md:294)、[§6.1 REPLAN建议按钮](/Users/jet/Downloads/WeBank/PRD.md:714)、[§9.5 HINT动作](/Users/jet/Downloads/WeBank/PRD.md:1429)。PRD不要求固定“采纳”二字或特定样式；高优先级是报告评估。

现场已正确REPLAN，并给出当前最多5000元的解释；对应5000元收缩建议也已持久化。但页面只有“核实后显示为按钮”的说明，没有可操作的建议入口，建议采纳路径未闭合。用户仍可手动输入5000元作为新指令。

**源码定位：** 真实ReplanHint字段为id，Agent卡片生成器却读取ref。合法建议无法形成前端需要的HINT action；前端按action读取标签和显示按钮，段落中的金额不会自动变成按钮。

未保存完整SSE或标签请求网络轨迹，因此不把“现场actions为空”或“标签接口未调用”当作已捕获事实。字段错配由实际schema和生产代码核实，现场DOM确实没有按钮。

**复验标准：** 按PRD已有HINT路径验证“建议→按钮→用户选择→新预演”，保留正常审批、OTP和授权绑定。手工重新输入是合法新指令，但不能证明HINT路径通过。正式40原句未单列点击步骤，此项属于同一现场的PRD建议交互补充核验。

**前置注意：** 本例独立复核发生在37冻结卡之前，当时单笔限额5000元、活期6000元。当前卡已冻结，直接拿当前状态重试会命中不同规则。首轮同会话记录与40-isolated独立记录均已保留，24项统计只计一次。

证据：[建议按钮链路审查](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/40-hint-ui-review.md)、[独立现场DOM](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/screens/40-isolated-result.txt)。

## D 附录R3、R4：展示问题与体验建议

**需求归属：** R3为展示实现问题，11月1日修复计划有去包装目标；原PRD未逐字要求前端隐藏包装。R4大部分为体验建议，原PRD未禁止所有ID或锁定Markdown、日期的显示格式。这些不计作银行功能失败；若实际名称或业务日错误，须单独提供事实证据。

| 问题 | 已观察表现 | 影响用例举例 | 展示检查建议 |
|---|---|---|---|
| R3 不可信数据包装直接上屏 | 账户或商户名称显示为untrusted_data及source包装文本 | 12、13、15、17、31、34b | 卡片显示可读名称；后台污点和来源校验继续保留 |
| R4 内部编号与原始格式外露 | src来源、si订阅、prd产品编号替代友好名称；部分表格仍是Markdown文本，日期仍为原始UTC字符串 | 14、24、25、26、27、31、32、34b等 | 卡片与正文名称一致，表格可读，业务日期与时区明确 |

聊天摘要已做显示清理，read-card路径仍保留部分包装字符串。部分name字段被过滤后，卡片又回退到内部ID；顶部来源标识还由前端固定渲染。这些问题需要分别核对，不能只检查聊天正文。

范围是助手的read卡片；现有证据不能扩展为所有原生业务页面都有同样问题。核心查询、金额或产品比较恢复，不代表卡片展示已经完成。

证据：[卡片显示链路定位](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/residual-ui-findings.md)、[12现场DOM](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/screens/12-result.txt)、[14现场DOM](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/screens/14-result.txt)、[34b现场DOM](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/screens/34b-result.txt)。

## E 附录R5：“未来7天预留0”字段语义待澄清

**需求归属：** 原PRD [§7.7](/Users/jet/Downloads/WeBank/PRD.md:1121)及[§9.5](/Users/jet/Downloads/WeBank/PRD.md:1440)有预留算法和字段。纯取消、纯赎回的适用范围未写明，也未约定展示操作前或后的数值。本项不计作取消或赎回功能失败。

| 操作 | 面板值 | 实际仍有效的7日义务 |
|---|---:|---:|
| 28取消张三音乐代扣 | 0元 | 操作前45元；取消后视频扣费30元 |
| 34李四赎回货币基金 | 0元 | 操作前后均45元 |

真实preflight字段本身存0，前端只是照值显示。源码只在转账、申购、定时转账等操作计算reserve；纯取消或赎回未计算，默认0直接进入面板。

因此，这个0不能作为“未来没有扣费”的证明。现有证据支持展示语义缺陷，没有发现因此发生错误扣款、越权或赎回失败。

**澄清边界：** 45元、30元是已采实际义务，不是原PRD对本面板的强制验收值。需先明确标签表示操作前、操作后，还是未计算；“不适用/未计算”也可准确表达。本报告不新增必须计算此项的产品要求，不将该默认值推断为资金或授权故障。

证据：[28独审及预留来源](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/28-grant-evidence-review.md)、[34独审及资金结果](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/34-evidence-review.md)。

## F 附录R6：异常数量文档口径冲突

执行版本PRD §2.2要求“三条异常”，§8.2后续补充为9月四条、基准全窗五条，两处冲突。下表是当前已采结果及规则口径，不能自动作为原PRD通过标准。

| 当前窗口 | 精确集合 |
|---|---|
| 2026年9月 | txn_zs_014、txn_zs_015、txn_zs_016、txn_zs_017 |
| 9月1日00:00至10月15日09:00（上海） | 上述四条，另加txn_zs_012 |
| 用例15：9月15日00:00至10月15日09:00（上海） | txn_zs_015、txn_zs_016、txn_zs_017、txn_zs_012 |

**判定边界：** 对照原定稿和经确认的需求变更厘清适用口径。未厘清前标为文档冲突，不直接判算法失败或PRD通过。测试方不自行决定保留哪套规则或调整种子。旧head结果仍按历史记录保留。

证据：[新版本口径与整数分预期](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/read-oracles.md)、[窗口与数据独审](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/read-evidence-review-2.md)。

## G 已恢复事项与尚未验证范围

原失败08取消赵六、12流水、13商户消费、25订阅年费已有业务恢复证据。32产品比较、34助手赎回、37应急联动也有证据支持。
取消代扣的已有grant已正确REVOKED，原scope、source和risk保留；助手赎回1000份已到账1000元、剩9000份。它们不再列作旧业务阻塞。

这些恢复项仍可能涉及上述展示残留。尚未验证完整43条新版本重跑、全部29项需求分支、到期事件与重投恢复。两次独立reset、断网回放和冻结门禁也未验证。本轮33仅创建待确认订单，未推进T+1确认。

这些是证据缺口，不能直接列作已发现故障，也不能当作已经通过。

## H 交接索引

- [24项逐条结果CSV](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/results.csv)
- [上一轮完整复测报告](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/REPORT.md)
- [复测终态统计](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/completion.json)
- [最后已核业务状态](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/final-state.json)
- [复测证据哈希清单](/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/final-verification-manifest.json)

报告基于已保存的DOM、数据库只读投影及源码审查。银行与网关不是跨库原子快照；响应hash不等于保存完整响应；源码定位与现场事实分别表述。旧43结果和旧报告均未改写。
