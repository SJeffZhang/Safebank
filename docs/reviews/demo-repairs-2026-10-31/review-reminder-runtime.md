# REMINDER / runtime 通知独立安全复核

目标：`ed87fdb1f28592ae69562375a4932c1806396014`，parent `d37b850f35c204df66fb00b24c48e3827aee4757`。只复核该提交八文件的增量；源码来自精确 `git archive`，目录 `/private/tmp/sbg-demo-repairs-20261031/runtime-review-ed87fdb`。未编辑作者或根协调者工作树。评审者未实现本 runtime；本人所写的金额折算/交易分类不在本审查范围。

## 结论

**APPROVE**。本次未发现有代码或纯复现支持、需要修改的 P1/P2 问题。此结论只覆盖该 runtime 增量及现有 helper 的接线，不代表最终集成 head、全部 scheduler 回归或六场景现场验收已通过。

## 核验要点

- REMINDER 读取和执行都从真实 billing / MERCHANT_MANDATE 建立客户、账户、商户、指令关系；只接受完整六字段 immutable payload，严格整数金额、带时区日期及正确 hash。客户/账户不能由回调或展示文本补齐。
- S1 专用提醒路径要求当前 ACTIVE、未撤销的同客户 grant；精确绑定 instruction、source_plan_id、scope_hash 与完整当前 instruction_scope。无 grant 不 backfill，不走 DEMO 证据。当前金额、具体扣费时间、创建/结束时间、job due 和未来三日窗口均重验，异常走真实 L3 BLOCKED。
- L3 专用路径在 job 行锁后锁 clock / 当前客户 version，并在 fence 后重读来源；严格要求 SYSTEM_REMINDER 的 billing_id / instruction_id，拒绝额外证据字段。成功或 BLOCKED 只写 event_executions 与 event_jobs，不写余额、ledger、扣费 cursor、业务版本。追加独立负控确认在初读和 version fence 间取消仍得到 BLOCKED。
- 终态 event_decisions CAS、audit、固定通知共用同一连接/事务。通知失败抛错并回滚终态/审计；外层释放 claim，重试从 L3 已提交 execution 恢复。通知 helper 校验 event/customer/status，按 UNIQUE(event_id,type) 去重，不从自由文本寻找 plan；BLOCKED 提醒不发 CHARGE_UPCOMING，实际商户拒扣发 CHARGE_BLOCKED。
- 基金 source_plan_id/account 来自真实 fund_order；通知得到实际 bankcore 返回的 order_status。仅无错误且无 effects 时标 no_op，失败不会因无 effects 被抑制；已结算订单的 expiry sweep 不误报过期。
- D4 仅复制公开响应并将 BLOCKED 映射为 REJECTED，保留 code；RETRYING 不变，内部 S1 终态与授权未改。

## 本人实际验证

1. 精确 archive 上运行作者 acceptance 所列六个纯测试文件：

   ```text
   tests/gateway/s1/test_notification_runtime_unit.py
   tests/obligations_data/test_reminder_runtime_unit.py
   tests/gateway/w5/test_event_notifications.py
   tests/obligations_data/test_reminders.py
   tests/gateway/w5/test_notify_imports.py
   tests/obligations_data/test_scheduler_wiring.py
   ```

   命令使用 `PYTHONDONTWRITEBYTECODE=1 .../.venv/bin/pytest ... -q --tb=short -p no:cacheprovider`；结果 **123 passed in 2.38s**。日志：`/private/tmp/sbg-demo-repairs-20261031/runtime-pure-review.log`。
2. 自写只读外部负控 `/private/tmp/sbg-demo-repairs-20261031/test_runtime_review_boundaries.py`：**8 passed in 0.76s**。覆盖重算正确 hash 后的 bool 金额、额外 payload 字段、无时区日期、额外 evidence 字段、fence 间取消、超三日提前提醒及 BLOCKED 的提醒/商户通知区别。日志：`/private/tmp/sbg-demo-repairs-20261031/runtime-review-boundaries.log`。
3. 该提交七个 Python 文件 `ruff check --no-cache` 通过；精确提交 `git diff --check` 通过。

未运行真实 DB、API、HTTP、LLM、Docker、浏览器或部署。内存 SQL 断言不替代真实事务/并发验证。

## 协调者证据及历史更正

- 协调者已报告 root 映射 `ae8040c` 的新增真实 PG **9 passed in 6.28s**；本评审未独立重跑，将其标为协调者证据。
- 保留作者 acceptance 的历史口径：初始日历 fixture 问题已由协调者修正，不计为业务红测；之后精确旧码真实红测为八条失败。
- 协调者后续广受影响 PG 的旧 scheduler 四条失败仍在诊断：一条旧“nothing due” fixture 未计新 09:00 REMINDER，三条两秒墙钟等待跨每日 scan 边界超时。本评审既未改这些测试，也不把它们提前定性为产品死循环或已排除。由协调者完成条件式等待、真实 scheduler 回归与最终精确 head CI。
