# 真实浏览器 Demo 已复现问题

代码审查基准：`origin/main` 的完整提交 `22a87799c2b422a6c2186490a5972e11bc05dce4`。以下源码行号按该提交核对；绝对文件链接指向本机同名源码。本文只记录现场已复现现象、静态代码成因与建议验证，不代表修复完成。本诊断子任务未修改产品代码、部署、数据库或 AGENTS；实际浏览器业务操作与最终状态见主验收报告。

## 1. 风险测评提交返回 503，无法进入确认面板

### 已复现现象

真实 3001 页面，代际 `demo_20261101050549_93b880b1`：张三尚无风险测评，六题全部选择最保守选项 `a`，点击“提交测评，查看预演”后提示 `DEPENDENCY_UNAVAILABLE`，没有审批弹窗；数据库中张三风险等级仍为 `null`。

这是测评写流程的真实阻塞。未测评时页面展示“尚未完成有效测评”本身正确。

### 代码成因

前端字段及调用入口没有发现错误：

- [Services.tsx:1200](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/frontend/src/mobile/Services.tsx:1200) 提交 `ASSESSMENT_SUBMIT`，字段为 `questionnaire_id` 和六组 `{question_id, option_id}`。
- [api.ts:58](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/frontend/src/mobile/api.ts:58) 先登记 G19 FORM，再以 `form_action_ids` 调 G4。
- [tools.py:120](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/contracts/src/safebank_contracts/tools.py:120) 的冻结契约正是六组答案，选项允许 `a/b/c/d`；[sessions.py:218](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/api/sessions.py:218) 的 G19 实现会取快照、校验并保存 FORM。

阻塞在 **L2 生成测评确认面板的保留分支**：

- [planview.py:221](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/approval/planview.py:221) 对 `submit_risk_assessment` 明确执行 `raise ValueError("submit_risk_assessment panel requires questionnaire metadata")`。注释说明尚缺权威问卷展示，因此保持拒绝。
- [plans.py:433](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/api/plans.py:433) 在 `REQUIRE_APPROVAL` 分支调用 `build_view`，捕获该 `ValueError` 后转换为 `DEPENDENCY_UNAVAILABLE / HTTP 503`。此时无法生成审批面板，不能将该错误仅解释为外部服务临时故障。

### 既有测试为何没有保证端到端可用

[test_planview.py:343](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/tests/gateway/w4b1/test_planview.py:343) 的 `test_submit_risk_assessment_fails_closed` 直接断言该分支抛出 `ValueError`。而 [test_risk_exec.py:170](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/tests/obligations_data/test_risk_exec.py:170) 通过 L3 内部 prepare/execute 测测评落库，不经过 G19→G4→确认面板。两类测试均不能替代浏览器完整测评流程。

### 最小修复与回归建议

从版本化权威问卷模板读取题目和选项文案，在 L2 面板中显示六题及用户所选答案，并将展示内容与问卷版本纳入已有 `view_hash` 绑定。模板已存在于 [risk_questionnaire.py:34](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/contracts/src/safebank_contracts/risk_questionnaire.py:34)。保持 app_form 专属、黄级确认及现有来源校验；不能直接去掉异常后仅显示裸 ID，也不能绕过确认直接更新风险等级。

最小验证：

1. 六题全 `a` 的合法 FORM 经真实 G19→G4 得到黄级审批，面板显示完整题目和所选答案；预演前后 L3 风险等级仍为 `null`。
2. 拒绝审批不新增测评记录；确认后只新增一份测评，分数为 6、等级为 R1，有效期按模拟时间增加 12 个月，前端权威 profile 刷新一致。
3. 重放确认/执行不重复写；缺题、重复题、非法选项、未知问卷版本及 Agent 冒充提交继续拒绝。
4. 合法问卷替换原“全部失败”测试；保留缺失权威展示元数据等非法情况的拒绝测试。

## 2. 货币基金和股票产品被错误标为“混合产品”

### 已复现现象

真实产品列表中，“安心货币”风险等级 R1 正确，但类型标签显示“混合产品”；“成长股票”也显示“混合产品”。这是产品类型展示错误，不是已证实的风险等级计算错误。

### 代码成因

- 后端冻结类型为 `MMF / BOND / MIXED / EQUITY`，见 [tools.py:11](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/contracts/src/safebank_contracts/tools.py:11)。真实种子中安心货币为 `MMF`，成长股票为 `EQUITY`，见 [l1_seed_v1.json:366](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/fixtures/l1_seed_v1.json:366) 与 [l1_seed_v1.json:422](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/fixtures/l1_seed_v1.json:422)。
- [Services.tsx:936](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/frontend/src/mobile/Services.tsx:936) 仅识别旧值 `MONEY_MARKET` 和 `BOND`，其他所有类型都落入“混合产品”分支。因此 `MMF` 和 `EQUITY` 均被错误归类。
- [types.ts:63](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/frontend/src/mobile/types.ts:63) 将类型声明为宽泛 `string`，编译无法发现枚举遗漏。Preview 和测试仍使用 `MONEY_MARKET`，见 [preview.ts:145](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/frontend/src/mobile/preview.ts:145)、[WealthActions.test.tsx:9](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/frontend/src/mobile/WealthActions.test.tsx:9)。

### 最小修复与回归建议

按冻结枚举建立统一类型名称映射，明确区分货币、债券、混合、股票；未知值显示明确的未知类型，不能默认伪装成混合。前端类型和合成 fixture 同步采用真实枚举。

最小验证：用真实返回形状分别提供 `MMF / BOND / MIXED / EQUITY`，断言各自中文标签；断言未知值不会显示成“混合产品”。核查修复不改变风险等级、风险匹配和申购门槛。

## 3. 实际转账 100 元，助手成功回复却称转账 5000 元

### 已复现现象与资金边界

计划 `pln_01M6AR2D7WCR0H12JKXD90B11M`：用户亲自确认转账 100 元，确认面板和计划显示操作完成；SQL 显示 `transfers.amount_cents=10000`、张三活期余额 `590000` 分，符合实际转账 100 元。助手却回复“已从活期向王五转账5000元，交易已执行。”

**这是已复现的执行结果解释错误。实际资金执行金额正确，不能称为误转 5000 元、未授权转账或网关放行错误。**

### 已核实的代码成因方向

成功后的回复仍由模型生成：

- [routing.py:145](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/agent/app/graph/routing.py:145) 将 EXECUTED 正常结果送往普通 respond。
- [respond.py:112](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/agent/app/llm/respond.py:112) 进入 `_respond_executed`；[respond.py:142](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/agent/app/llm/respond.py:142) 调模型生成成功摘要。

输出校验没有把数字绑定到其业务含义：

- [verify.py:251](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/agent/app/llm/verify.py:251) 遍历 reads 与 execution 结果；[verify.py:303](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/agent/app/llm/verify.py:303) 将各处 `*_cents` 数字汇总为金额集合。
- [verify.py:352](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/agent/app/llm/verify.py:352) 仅检查回复金额是否存在于该集合；[verify.py:393](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/agent/app/llm/verify.py:393) 只比较数值。没有区分“本次转账金额”“卡限额”“账户余额”，也没有将回复中的转账断言绑定至当前 plan/step 的已执行金额。
- `build_fact_set` 未建立当前已执行计划 `steps[].args.amount_cents` 的专用事实。往同一数值集合补一个正确金额仍不能解决字段互换问题。
- [verify.py:565](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/agent/app/llm/verify.py:565) 的修复流程失败时可继续使用原文；之后只有卡尾号清理和长号码掩码，没有对错金额作确定性兜底。修复文本也没有重新执行完整语义一致性检查。

以上定位说明当前代码为何允许错误的转账金额解释到达用户；本报告不对这次回复是否经过模型 repair 作推断，也不认定 5000 元具体来自本次哪条读取字段。

### 既有测试的缺口

- [test_respond.py:106](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/tests/agent/test_respond.py:106) 明确要求 EXECUTED 调模型，主要断言回复非空与调用次数，没有验证本次交易金额。
- [test_verify.py:525](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/tests/agent/test_verify.py:525) 用 `result.balance_cents=80000` 认可“转账800元”，实际固化了余额与转账金额混用的校验方式。
- [test_verify.py:553](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/tests/agent/test_verify.py:553) 允许模型修复失败后返回原文，没有要求已知错金额被删除或替换。

### 最小修复与回归建议

转账成功回执应由与实际 execution 同一 `plan_id/step_id` 绑定的、已验证执行计划金额确定性生成；余额取实际 execution 结果。关键事实缺失时使用保守成功提示并引导查看交易记录，不从其他数字推断金额。不修改 L2 授权与 L3 资金执行来修复回复问题。

最小验证：

1. 已执行金额为 `10000` 分、余额 `590000` 分，读取事实同时含 `500000` 分的限额；即使模型回复和修复文本都声称“转账5000元”，最终输出也只能给出本次正确的 100 元或不含金额的保守提示。
2. 正确 100 元回执及余额 5900 元分别匹配对应字段；新增其他读取数字不会改变本次交易金额。多笔步骤的金额、收款人不能互换。
3. 模型修复报错、空回复或继续输出错金额时不能透传已知错误；保留现有 402/403/429 中止规则。
4. 编译图验证回复生成不会再次执行交易；真实浏览器复测时，面板、助手回复、同一计划的 SQL 金额和余额一致，并确认只有一次转账。

## 验证范围

本文依据协调者本轮真实浏览器/SQL观测，以及对指定 origin/main 提交的只读源码检查编写。上述回归建议均尚未由本次文档任务执行；未将静态定位或既有组件测试写成修复通过。
