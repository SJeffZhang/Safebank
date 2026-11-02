---
template: doc
theme: paper
lang: zh
title: SafeBank 报告与原PRD归属核对
subtitle: 2026-11-01 · 文档溯源与报告勘误 · 未新增演示
---
建议采纳功能已写入10月23日定稿PRD；报告的六类问题却不能全部称为“原需求功能未完成”。本说明纠正归属和验收边界，不改变现场事实。

## A 验收依据与版本

原始依据是最早入库的定稿PRD v2.1，提交 `ffcfe23cdec8d5567240951f3423a9872c734d07`，时间为2026-10-23。已逐项对比当前PRD和11月1日修复文档。

更早归档的v1.2只描述解释原因和提供替代方案，未定义hint按钮。不能将“v2.1原有规划”说成“所有早期版本已有”。

后续演示逐条列明：PRD章节或需求ID、前置状态、用户动作、预期结果、现场结果及证据。后续变更单列来源；文档冲突未厘清时标为待判定，测试方不自行增加或修改需求。

体验建议单独列出，不计入功能失败。没有对应条款的具体文案、布局和展示偏好，不作为强制验收条件。

来源：[初始PRD原文摘录](/Users/jet/Downloads/WeBank/output/requirement-provenance-2026-11-01/initial-prd-excerpts.txt)。

## B 建议采纳按钮原本就有规划

| 原定稿条款 | 已规定的内容 | 当前文档定位 |
|---|---|---|
| §3.4完整链路 | 给出“赵六改为2,955元”“只转王五”按钮；点击后登记hint并开始新一轮 | [PRD](/Users/jet/Downloads/WeBank/PRD.md:294) |
| §6.1路由表 | 需用户决定的REPLAN返回原因及replan_hints按钮 | [PRD](/Users/jet/Downloads/WeBank/PRD.md:714) |
| §7.9决策聚合 | 建议只能收缩授权，不能自动采用；按钮文字从L2读取 | [PRD](/Users/jet/Downloads/WeBank/PRD.md:1151) |
| §9.5卡片契约 | 已定义HINT动作及点击后F3的kind=hint链路 | [PRD](/Users/jet/Downloads/WeBank/PRD.md:1429) |
| §9.10安全约束 | 明确提及“确认、采纳、选择类按钮” | [PRD](/Users/jet/Downloads/WeBank/PRD.md:1572) |

PRD没有要求按钮必须显示固定二字“采纳”，也没有锁定其样式。准确要求是提供可操作、由L2签发的建议入口，点击后重新预演并保留审批。

11月1日ADR-013补充服务端绑定、持久化和单次采纳细则，并非首次提出这项功能。它只能作为后续实现约定的来源，不能代替原PRD溯源。

## C 六类问题的需求归属

| 原报告项 | 归属 | 对齐后的结论与边界 |
|---|---|---|
| R1 候选选择后报错 | 原PRD明确功能 | T-1近名追问、X-3产品选择及§7.4澄清流程已规定。02、38选择后未进入预演，是功能路径缺陷。 |
| R2 有效建议无采纳入口 | 原PRD明确功能 | §3.4、§6.1、§7.9、§9.5已规定。40超额拦截正确，补充核验发现建议交互未闭合。 |
| R3 不可信包装直接上屏 | 展示实现问题 | §6.1将包装用于gather上下文；原文未逐字规定前端隐藏包装。11月1日修复计划另有去包装目标，不混称原始硬要求或安全失败。 |
| R4 内部ID、Markdown、UTC格式 | 体验建议，须与事实错误分开 | 原PRD未禁止所有内部ID，也未锁定表格和本地化格式。UTC本身不等于日期错误；产品误称或业务日错误须有独立事实证据。 |
| R5 非转出预演显示预留0 | 字段和标签语义待澄清 | §7.7、§9.5有预留算法及字段，但未明确纯取消、纯赎回必须计算总预留，也未确定before/after。不能强加30元或45元为原文验收值。 |
| R6 异常数3与4/5冲突 | PRD文档一致性问题 | 当前§2.2与§8.2口径冲突。不能直接判算法失败，也不能擅自选4/5作为原需求通过标准。 |

R1依据：[T-1](/Users/jet/Downloads/WeBank/PRD.md:561)、[X-3](/Users/jet/Downloads/WeBank/PRD.md:650)、[澄清流程](/Users/jet/Downloads/WeBank/PRD.md:1021)。

R3、R4的后续修复目标来源：[修复计划](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/superpowers/plans/2026-11-01-prd-report-repairs.md:23)。这份计划不是原始PRD条款。

R5依据：[预留规则](/Users/jet/Downloads/WeBank/PRD.md:1121)、[preflight字段示例](/Users/jet/Downloads/WeBank/PRD.md:1440)。未计算值可明确为不适用或未计算；本次核对不指定新增算法。

R6依据：[旧三条口径](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:215)、[后续四条及五条口径](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:1221)。本次不调整种子、检测规则或通过标准。

## D 对截图中R2的表述勘误

“流程无法继续”应收窄为“建议采纳路径未闭合”。用户可以重新手动输入5000元，作为新的合法指令。

“不能用手工输入替代采纳成功”只适用于验证HINT按钮链路，不是禁止手动输入的新产品要求。

正式43句中的40只有“给王五转50000元”，没有单独列点击按钮的步骤。报告应拆分记录：原句的超额拦截已正确；同一现场补充核验的PRD建议交互仍有缺陷。

“高优先级”是报告对演示影响的评估，不是PRD给定的强制级别。按钮文字和视觉样式不作为通过条件。

## E 既有报告和后续演示怎么使用

24项的8 PASS、14 PARTIAL、2 FAIL是历史观察标签，其中混有功能和展示判断；不能把它直接当作PRD功能通过率。本次不重算历史记录，也不以文档核对冒充新演示。

六类汇总已补充归属并修正措辞。原报告快照、原43条结果、复测CSV、截图及SQL证据均保留。未测试的PRD分支继续标未验证，不能列作已发现故障。

- [已对齐的剩余问题报告](/Users/jet/Downloads/WeBank/output/remaining-issues-2026-11-01/REPORT.md)
- [修改前报告快照](/Users/jet/Downloads/WeBank/output/remaining-issues-2026-11-01/before-prd-alignment/REPORT.md)
- [初始PRD及存档哈希](/Users/jet/Downloads/WeBank/output/requirement-provenance-2026-11-01/evidence.json)

本次仅核对文档和修正报告，未运行新演示、修改产品代码或改变业务数据。
