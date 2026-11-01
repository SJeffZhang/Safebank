# 前端演示一致性修复（2026-10-31）

基线：22a87799c2b422a6c2186490a5972e11bc05dce4。范围为 mobile 前端与自动化验证；没有运行浏览器、模型、真实业务 API、部署或数据库。

## 产品类型

真实 ProductView 枚举 MMF/BOND/MIXED/EQUITY 显示货币基金/债券产品/混合产品/股票产品；未知 wire 值显示未知类型。前端 ProductType 与 Preview fixture 对齐，不改变 risk_level、测评或申购约束。

红测：`npm test -- --run src/mobile/WealthActions.test.tsx`，1 failed / 4 passed，MMF 期待货币基金、实际混合产品。
绿测：`npm test -- --run src/mobile/WealthActions.test.tsx src/mobile/preview.test.ts`，16 passed；`npm run typecheck` 通过。新增测试同时覆盖四种类型、未知值和R4超出R2仍禁止申购。

## 门禁说明

下文记录各独立修复及最终自动化结果。根AGENTS/PRD由协调者更新；实际浏览器与三层演示未运行。

## 月报与真实读结果

修复请求“9月/上月/2026年8月月报”和“分析上月账单并冻结可疑卡”的默认月份错误。仅使用用户原话决定只读参数；相对月以银行模拟时间的上海时区解析，汇总必须返回同月且为整数分，异常读取使用同一[月初,下月月初)范围。月份不明、缺数据或返回月份错配时不显示默认金额。展开账单明细继承所选月。结构化月汇总按其真实month/total_cents/categories渲染，错月结果不显示金额；不从模型自由回复提取事实。

红测：AssistantTopics新增6项在旧实现全部失败（旧7项通过）：错误默认10月/未识别月报/汇总误当items空列表。绿测：AssistantTopics与BillsCategories共16 passed，覆盖混合X1请求、9月/上月/明确年份、上海跨月边界、错误月份汇总和结构化汇总；typecheck通过。未改变写操作授权或澄清边界。

## 计划终态与列表刷新

历史PLAN_STATUS卡不再永久保留SSE时的等待确认快照。它只查询自身plan_id，按同站身份读取G5，检查返回plan_id一致，非终态只读轮询，终态停止；查询错误显示不可用，不能显示其他计划的成功。原会话切换/组件卸载会取消显示更新。已完成、失败、拒绝、过期及UNKNOWN使用统一文案，终态不再显示确认按钮。Approval重开已使用/已确认面板时读取原计划，弹窗顶部和结果区统一反映实际终态；UNKNOWN只轮询，不调用确认/执行，只有EXECUTED触发业务刷新。G5的decision实际可空，查询视图显式处理。确认停留、可信手势、view_hash、OTP和风险揭示保持原约束。

静查同时发现agent自动ALLOW执行没有触发顶层数据刷新：新增onExecuted只在已核实同计划EXECUTED后触发，每会话每plan一次，后续revision不重复刷新。所有业务页既有sid/revision依赖据此重读列表。

红测：PlanLifecycle初始11项全部失败；终态实现后新增列表刷新负控1 failed / 11 passed（回调0次）。绿测：PlanLifecycle 12、Approval 5、live G9状态边界13共30 passed，typecheck通过。live G9旧测试扩展只读G5响应，仍检查无自动POST和无可用确认；并未放宽安全条件。真实手势确认执行与真实三层状态由协调者后续验收。


## 六场景前端wire与刷新核对

基于冻结`packages/contracts/src/safebank_contracts/{tools,views}.py`、L1 `llm/cards.py`与当前mobile代码逐项静查；以下是静态对应关系与前端自动化验证，不能替代真实六场景执行。

| 场景 | 读取与动作对应 | 状态/边界结论 |
| --- | --- | --- |
| 转账 | get_accounts/list_payees/list_scheduled/get_split_status；TRANSFER_EXTERNAL/TRANSFER_SELF/SCHEDULE_CREATE/SCHEDULE_CANCEL/SPLIT_CREATE/PAYEE_ADD | sid/revision重读列表；新增收款人需刷新列表出现权威ID才选中；整数分/份额和既有确认路径保持。TransferAccounts原测试覆盖账户选择与新增后刷新。 |
| 账单 | get_spending_summary/get_transactions/detect_anomalies | 月报与异常统一月份，嵌入明细继承月份；真实汇总不是交易items，真实异常金额在transaction内。新增范围与嵌套形状回归。 |
| 卡片 | get_cards；限额/渠道收紧与放宽、冻结/解冻、挂失 | 读items，按card_id绑定，revision重读；开户和完整PAN仍限Preview，不接新真实能力。原CardNumber及preview安全测试保留。 |
| 订阅 | list_subscriptions/detect_subscriptions/upcoming_charges；MANDATE_CANCEL | 明细/识别/待扣分别取实际返回；识别金额来自typical_amount_cents并标典型扣费；待扣卡合并scheduled_items，不遗漏只有定时安排的情况。取消经原提案路径，revision重读。 |
| 理财 | list_products/get_product/compare_products/get_holdings/get_risk_questionnaire；FUND_SUBSCRIBE/FUND_REDEEM/ASSESSMENT_SUBMIT | 产品详情直接ProductView；真实四枚举统一标签；持仓与在途订单分开，只有订单时仍显示状态和真实金额，待结算不作可用资金；风险与测评门槛保持。 |
| 跨场景 | 原话经stream，L2独立确认，计划G5查询 | 显式“跨场景联动：分析上月账单…”也优先展示对应月真实summary和异常时间/金额/规则；不从助手自由文本造图，不因展示触发写提案。完成按原plan_id核实后刷新，UNKNOWN只读轮询。 |

静查补充的5项卡片形状红测全部失败，修复后AssistantWire 5项全部通过。随后补充跨场景X1前缀/实际异常明细和无法解析月份负控，旧实现2 failed / 14 passed；修复后增加结构化异常越月负控，旧实现1 failed / 16 passed。最终AssistantTopics 17项覆盖这些边界，任何返回异常超出所选月时不显示该金额；unsupported月份也不会默认当前月。产品类型名称在列表和助手卡共用同一映射。

## 最终自动化结果

在最终源码上运行（2026-10-31；日志临时文件时间可能由本机时区显示）：

- `npm test`：46 files，441 passed，0 failed；包含旧实现保留测试，新mobile为其子集，不另行相加。
- `npm run test:bff`：16 passed，0 failed/skip；假上游加本地HTTP，未调用真实L1/L2/L3。
- `npm run build`：TypeScript与Vite成功，55 modules。
- `git diff --check`：通过。无BFF产品代码、后端、共享契约、PRD或AGENTS修改；未推送、未合并。

未覆盖：真实可信用户手势的浏览器确认/OTP、真实模型SSE与实际余额/持仓/测评落库、六场景两个独立reset、完整43条生产用例、生产录制/断网回放、定时事件/主动通知/AA外部付款事件。前端测试不能证明这些已通过，也不构成demo-v1冻结。
