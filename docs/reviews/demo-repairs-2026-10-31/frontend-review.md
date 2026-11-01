# 前端一致性独立只读复核

作者：子agent fix_frontend_demo_consistency；复核：协调者（未实现这些前端改动）。
范围：原始22a8779→f4af1ba，四单元在root映射为b2b070f/311c994/71b26fa/7cd5ce6。

结论：APPROVE。

- 四个冻结产品枚举与未知类型分开，风险等级、适当性和确认调用不变。
- 月份仅选择只读查询范围；模拟时间按上海日历解析，缺数据/错范围不绘制伪造汇总，异常读取使用实际嵌套transaction。
- G5当前plan_id复核后才显示终态；UNKNOWN/错误/过期/取消不显示成功，不自动确认/执行，1秒及风险勾选/OTP条件保留。
- 仅EXECUTED触发一次业务刷新，历史卡不继续使用旧等待确认状态；失败获取不沿用成功猜测。
- 产品详情、订阅候选、即将扣费及持仓订单按真实只读wire投影；文本节点与导航白名单保留。

复核源码：Approval.tsx、Assistant.tsx、AssistantWorkspace.tsx、api.ts、types.ts、plan-status.ts、bill-month.ts、SafeBankApp.tsx及新增负控。
协调者命令：`npm test -- --run src/mobile/PlanLifecycle.test.tsx src/mobile/Approval.test.tsx src/mobile/AssistantWire.test.tsx src/mobile/BillReport.test.tsx src/mobile/WealthActions.test.tsx`，5文件30通过。
作者全量441/BFF16/build见acceptance；最终组合全量CI仍待，不将本复核记成浏览器验收。
