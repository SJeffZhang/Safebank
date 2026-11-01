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
