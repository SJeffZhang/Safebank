# 前端演示一致性修复（2026-10-31）

基线：22a87799c2b422a6c2186490a5972e11bc05dce4。范围为 mobile 前端与自动化验证；没有运行浏览器、模型、真实业务 API、部署或数据库。

## 产品类型

真实 ProductView 枚举 MMF/BOND/MIXED/EQUITY 显示货币基金/债券产品/混合产品/股票产品；未知 wire 值显示未知类型。前端 ProductType 与 Preview fixture 对齐，不改变 risk_level、测评或申购约束。

红测：`npm test -- --run src/mobile/WealthActions.test.tsx`，1 failed / 4 passed，MMF 期待货币基金、实际混合产品。
绿测：`npm test -- --run src/mobile/WealthActions.test.tsx src/mobile/preview.test.ts`，16 passed；`npm run typecheck` 通过。新增测试同时覆盖四种类型、未知值和R4超出R2仍禁止申购。

## 尚待闭合

月报请求范围和汇总卡、历史计划终态与确认面板、六场景静态契约检查；最终全量测试、BFF、build结果后续追加。根AGENTS/PRD由协调者更新。

## 月报与真实读结果

修复请求“9月/上月/2026年8月月报”和“分析上月账单并冻结可疑卡”的默认月份错误。仅使用用户原话决定只读参数；相对月以银行模拟时间的上海时区解析，汇总必须返回同月且为整数分，异常读取使用同一[月初,下月月初)范围。月份不明、缺数据或返回月份错配时不显示默认金额。展开账单明细继承所选月。结构化月汇总按其真实month/total_cents/categories渲染，错月结果不显示金额；不从模型自由回复提取事实。

红测：AssistantTopics新增6项在旧实现全部失败（旧7项通过）：错误默认10月/未识别月报/汇总误当items空列表。绿测：AssistantTopics与BillsCategories共16 passed，覆盖混合X1请求、9月/上月/明确年份、上海跨月边界、错误月份汇总和结构化汇总；typecheck通过。未改变写操作授权或澄清边界。

## 计划终态与列表刷新

历史PLAN_STATUS卡不再永久保留SSE时的等待确认快照。它只查询自身plan_id，按同站身份读取G5，检查返回plan_id一致，非终态只读轮询，终态停止；查询错误显示不可用，不能显示其他计划的成功。原会话切换/组件卸载会取消显示更新。已完成、失败、拒绝、过期及UNKNOWN使用统一文案，终态不再显示确认按钮。Approval重开已使用/已确认面板时读取原计划，弹窗顶部和结果区统一反映实际终态；UNKNOWN只轮询，不调用确认/执行，只有EXECUTED触发业务刷新。G5的decision实际可空，查询视图显式处理。确认停留、可信手势、view_hash、OTP和风险揭示保持原约束。

静查同时发现agent自动ALLOW执行没有触发顶层数据刷新：新增onExecuted只在已核实同计划EXECUTED后触发，每会话每plan一次，后续revision不重复刷新。所有业务页既有sid/revision依赖据此重读列表。

红测：PlanLifecycle初始11项全部失败；终态实现后新增列表刷新负控1 failed / 11 passed（回调0次）。绿测：PlanLifecycle 12、Approval 5、live G9状态边界13共30 passed，typecheck通过。live G9旧测试扩展只读G5响应，仍检查无自动POST和无可用确认；并未放宽安全条件。真实手势确认执行与真实三层状态由协调者后续验收。
