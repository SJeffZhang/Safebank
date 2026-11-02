# REMINDER运行接线与事务通知验收

基线：`d37b850f35c204df66fb00b24c48e3827aee4757`。日期：2026-10-31。分支：`fix/reminder-runtime-wiring`。

## 输入输出与组件边界

输入：L3不可变事件定义、真实执行结果、当前账单/代扣指令、L2已保存grant及新快照版本/时钟。
输出：REMINDER经S1确定性授权和L3版本/时间/身份重验后只记录事件终态；提醒不写资金、余额、扣费游标或业务版本。Gateway终态、审计和固定通知在同一producer事务提交，故障重试从L3实际终态恢复。D4仅在公开返回边界将BLOCKED映射REJECTED，S1原状态不变。

只修改授权文件：L3 events.py、gateway internal/scheduled.py、api/demo.py的小范围返回映射、两个协调者新增PG测试、两个新增纯测试及本记录；未改已评审helper、AGENTS、PRD或其他组件。

## L3接线

- `_customer_for/get_event`识别REMINDER，source_id必须是immutable payload中的billing_id；payload六个字段完整且类型正确，hash必须等于digest(payload)。客户/账户由真实billing行确认，指令ID必须指向同客户、账户、商户和billing的MERCHANT_MANDATE。不可从回调或自由文本补归属。
- 读取取消/过期/金额或扣费日期已变化的旧提醒仍保留不可变定义，使后续处理能记录BLOCKED，而不是误发或永远等待。
- 专用`_execute_reminder_event`：已有event_executions直接重放；否则job行锁、clock/state_versions屏障，要求job.due_at<=真实sim_now，锁后重读来源；严格绑定`{type:SYSTEM_REMINDER,billing_id,instruction_id}`证据。使用既有reminder_is_current重验当前授权/金额/具体扣费时刻。失效为BLOCKED，绝不落入DEMO或商户扣费applier。
- 成功/拦截只INSERT event_executions并将event_jobs置DONE；不调用persist、余额函数、扣费游标更新或业务version bump。block_event同样支持REMINDER专用终态。
- 基金get_event的source_plan_id/account_id读取真实fund_orders行。基金生命周期结果从实际bankcore返回state.fund_orders读取order_status；仅无errors且无effects才no_op=True。失败即使没有effects也no_op=False，避免漏报失败；已结算订单expiry sweep实际为SETTLED/no_op，不误称过期。

## S1和通知事务

- REMINDER走专用grant路径，读取真实scheduled_grants；拒绝缺失、非ACTIVE、revoked_at已设置、跨客户、instruction/scope_hash/source_plan_id/完整scope不一致。不会使用DEMO授权，不调用schedule_transfer backfill假造代扣grant。
- 对fresh BankState再次核对当前同一ACTIVE mandate、billing/客户/账户/商户、未过期/未扣费、真实金额/扣费时刻、immutable payload hash、job due与当前时钟、完整grant scope。失败通过L3 event_block真实记录BLOCKED；成功用新snapshot的versions/clock/hash及SYSTEM_REMINDER证据调用event_execute。
- `_finalize_system_result`所有调用点（基金、定时、商户、提醒、D4、阻止和已提交结果恢复）都传完整L3 event definition和当前完整result。legacy double的EXECUTED/FAILED先规范化SUCCESS/FAILED_INSUFFICIENT，再放入event.execution给已评审helper。
- `_finalize_claim`保存终态、append audit、notify_event_result使用同一conn/事务。通知或审计失败抛503并回滚，outer释放IN_PROGRESS claim供重试；恢复用L3已提交结果而不再次执行。终态幂等条件与通知UNIQUE(event_id,type)共同避免重复。
- D4 `_demo_result`仅复制并把BLOCKED改REJECTED，保留code；RETRYING仍为非成功，内部S1状态不改。

## 实际红绿证据

本子agent只运行纯测试与collect，无DB、Docker、HTTP/API、LLM或部署。

1. 根协调者提供的真实PG红测日志 `/private/tmp/sbg-notification-wire-red3.log`：原基线**8 failed**，两条REMINDER无handler、六条已提交S1结果无通知。本人只读其摘要；首次日历fixture问题已由协调者修正，不计为本次业务红测。
2. 新producer/D4纯测：**12 failed**后修绿。覆盖同conn通知、写失败整体回滚/恢复一次、legacy状态规范化、跨event/customer结果拒绝和基金no-op。
3. 新L3专用提醒纯测：**18 failed, 1 passed**后**19 passed**；原有错误hash拒绝是保留负控。新增S1授权纯测：**14 failed, 12 passed**后修绿。
4. 最终两个新增纯文件49条（含基金实际状态/no-op/来源plan）在将三个生产文件临时换为精确基线并于finally恢复的红绿核验中：**48 failed, 1 passed**；恢复修复代码后**49 passed**。
5. 最终相关纯测命令：
   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/gateway/s1/test_notification_runtime_unit.py tests/obligations_data/test_reminder_runtime_unit.py tests/gateway/w5/test_event_notifications.py tests/obligations_data/test_reminders.py tests/gateway/w5/test_notify_imports.py tests/obligations_data/test_scheduler_wiring.py -q --tb=short -p no:cacheprovider`
   → **123 passed in 2.32s**。包含上述49，不相加。
6. `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/obligations_data/test_reminder_flow.py tests/gateway/s1/test_event_notifications.py --collect-only -q -p no:cacheprovider` → **9 tests collected**，未执行PG。协调者原8条加一条通知插入后失败→终态/审计/通知回滚→L3恢复仅一次的新PG回归。原提醒PG回归另补真实业务version和账本不变断言。
7. 授权Python文件ruff check/format --no-cache、git diff --check：通过。

两次批量命令被自动审批拒绝，均未执行：首次混合写PG回归/纯测/collect，第二次格式化PG文件与纯测在同命令被判可能运行PG。随后按审查要求拆开文件写入、格式化、纯测和仅collect；先核读目标测试签名与无autouse DB fixture证明纯测边界后，独立纯测/collect成功。未绕过拒绝或启动数据库。

## 未覆盖与协调者后续门禁

真实PG事务冲突/租约恢复、完整三层提醒/撤销后不通知、实际代扣拦截与基金通知、D4完整链路及完整本机CI仍由协调者执行。内存SQL连接验证调用顺序和事务传播，不能代替真实PG。建议协调者先运行新增9条PG回归，再覆盖既有S1、scheduler/event APIs与D4的真实PG套件；最终按集成精确SHA独审及完整CI发布。
