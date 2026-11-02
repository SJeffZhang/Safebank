# 客户事件通知与代扣提醒开发验收

日期：2026-10-31。基线：`72d0a05625e1f20147b7a1728daacd1c7d9524cf`。独立分支 `fix/customer-event-notifications`；回执独审修复保留在原分支，不混入本提交。

## 输入输出和边界

输入：L3不可变事件定义与实际终态；有效账单/代扣指令、当前模拟时钟和推进目标。
输出：同producer事务内固定模板通知；每天09:00扫描未来3天有效代扣账单生成rank5 REMINDER，同一账单/具体扣费时刻仅一次。
本单元只改notify、新event_notifications、scheduler、新reminders及测试/本记录。S1/L3事件路由、demo和L3客户端由协调者串行接线。本工作树未改这些共享接线文件。

## 通知组件

`async notify_event_result(conn, *, event: dict, status: str) -> None` 接收由L3权威定义获得的event_id/customer_id/kind/source_id，传入既有producer事务的同一conn；不自建事务、不commit、写失败抛出给producer回滚。

- SCHEDULED_TRANSFER各终态 → SCHEDULE_RESULT；MERCHANT_CHARGE非SUCCESS终态 → CHARGE_BLOCKED；REMINDER/SUCCESS → CHARGE_UPCOMING。
- FUND_CONFIRM/SETTLE/EXPIRY各终态 → FUND_RESULT。失败固定提示未完成；成功且权威order_status分别为SETTLED/SETTLED/EXPIRED才明确确认/到账/过期；缺字段使用“确认/结算/到期处理已完成，请查看订单记录”的保守模板。
- 仅SUCCESS且权威result.no_op=True的基金事件不重复通知。失败即使无effects也必须提示失败，避免把无effects的失败当成功no-op静默。
- 对execution中已给出的event_id/customer_id/status作一致性校验。可选关联plan仅来自顶层source_plan_id，绝不从source_id或payload猜。payload里的商户、memo、客户、plan或其他自由文本不进入通知。
- 六类通知均由notify.py内固定模板生成，沿用UNIQUE(event_id,type)和ON CONFLICT DO NOTHING；稳定L3 event_id保证重放/恢复复用同一通知身份。客户行归属只取权威event.customer_id，不取回调或自由文本。

## 提醒组件

`app.reminders`纯helper接口：

- `build_reminder_job(*, billing, mandate, scan_at) -> dict | None`：只接受Asia/Shanghai的09:00扫描时刻；要求有效ACTIVE代扣、同一billing/customer/account/merchant、未到期/金额不超授权、账单和指令在扫描前已存在。未来3天定义为`(scan_at, scan_at + 72h]`。
- `reminder_is_current(payload, billing, mandate, at) -> bool`：执行前重检当前账单/指令身份、ACTIVE、有效期、金额、具体扣费时刻；取消/换单/变更/过期都不能借旧提醒成功。外层仍须验证job.due_at<=clock与L2 grant未撤销。
- `reminder_scan_at(sim_now)`：当日已到达的09:00，08:59返回None；`next_reminder_scan(sim_now)`：严格下一个北京时间09:00。

REMINDER job：

```text
source_id = billing.id
id = digest(["REMINDER", billing.id, charge_due_at_utc_iso])
period_key = charge_due_at_utc_iso + "|reminder"
due_at = 首次成功插入时扫描日的09:00
rank = 5
immutable_payload = {"payload": {
  "billing_id": ...,
  "customer_id": ...,
  "account_id": ...,
  "amount_cents": 整数分,
  "charge_due_at": UTC ISO带偏移,
  "instruction_id": ...
}}
payload_hash = digest(immutable_payload["payload"])
```

id/period_key不包含扫描日期，同一扣费跨日重复扫描仍冲突不插入，保留首次due_at；下一具体扣费周期获得新身份。查询和payload均使用DB权威字段，不读商户/memo自由文本。

Scheduler.scan_due只按当前read_clock扫描提醒，绝不按未来target提前生成；run_once扫描当前到期事项后再claim，S1调用仍在事务外。ClockAdvancer把每一个09:00作为时钟推进边界；到达该时刻先回到扫描阶段，再按(due_at,rank,event_id)派发。空事件队列也经过每日09:00，多日推进不会直接跳过提醒；同刻基金rank0先于提醒rank5。

## 红绿命令与结果

均为合成状态/内存连接和SQL编译验证，不访问DB/网络/provider。

1. `.venv/bin/pytest tests/gateway/w5/test_event_notifications.py -q --tb=no`：新helper不存在时 **31 failed**；实现后 **31 passed**。这是新增入口缺失红测，非生产端到端重现。
2. `.venv/bin/pytest tests/obligations_data/test_reminders.py -q --tb=no`：新增提醒定义/扫描入口缺失及旧ClockAdvancer跳过每日09:00时 **31 failed**；实现后 **31 passed**。
3. 基金FAILED/no_op=True真负控：**1 failed, 33 passed**，修正为只抑制SUCCESS/no_op后通过。
4. 最终命令：`.venv/bin/pytest tests/gateway/w5/test_event_notifications.py tests/gateway/w5/test_notify_imports.py tests/obligations_data/test_reminders.py tests/obligations_data/test_scheduler_wiring.py -q` → **74 passed**（通知34、提醒34、既有导入/部署接线纯测6；未与此前套件数字相加）。
5. 本次6个Python文件ruff check/format --no-cache、git diff --check：通过。

覆盖：四类新增通知的实际终态/未知状态、固定模板、不复制自由文本、客户隔离、幂等SQL键、producer写失败传播、基金已结算expiry sweep保守提示和成功no-op静默；08:59、09:00、午夜/UTC换算、72h开闭边界、重复跨日扫描/恢复、撤销/过期/客户账户商户错配、变更金额/扣费时刻、整数金额、同刻rank顺序和连续多日空队列推进。

## 协调者必须完成的接线

本提交独立helper与producer就绪，尚未单独启用完整REMINDER消费链；必须与下列接线组合后部署，不能把本单元纯测称为实际通知到达：

1. L3 EventService._customer_for/get_event识别REMINDER，按source_id=billing与不可变payload双重校验客户归属；get_event返回权威billing/customer/account/amount/charge_due_at/instruction信息及payload_hash。读取其他客户事件返回404。
2. L3 execute_event支持REMINDER：校验job.due_at<=当前sim_now、payload/hash与版本/时钟屏障，重读当前账单和同一指令，用reminder_is_current校验。仅记录事件终态，不写账本/余额；取消或已过扣费时刻不能SUCCESS。若需要event_block，请同样允许REMINDER记录阻止终态而不发通知。
3. S1支持REMINDER：对当前同一mandate的有效grant/撤销状态做确定性校验，不当作DEMO_EVENT或用户新授权；无权或已取消返回BLOCKED，REMINDER非SUCCESS不发通知。
4. S1最终producer事务（含从L3已提交结果恢复的分支）在保存终态和审计的同一conn调用notify_event_result。回调重试/最终化失败后重新读取不可变定义/执行结果，不能仅凭回调字段生成通知；通知异常需使producer事务一起回滚。
5. Fund result补权威`execution.result.order_status`与`no_op`，S1传完整execution（get_event中execution或当前event_execute响应均须含result字典）。`no_op`只表示没有成功状态变化；本helper即使失败无effects也保留失败通知。source_plan_id若需要深链，必须由L3关联真实指令/基金订单行提供。

## 未覆盖

未运行真实API/模型、数据库、Docker、浏览器、部署或完整CI。内存连接的幂等/回滚验证不能代替真实PostgreSQL冲突/事务/租约恢复；最终接线、真实PG跨日/重复/取消后提醒/失败代扣通知以及客户JWT读取隔离由协调者串行核验。未编辑共享AGENTS/PRD，待协调者汇总。
