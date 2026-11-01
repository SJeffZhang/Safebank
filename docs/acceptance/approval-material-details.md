# G9确认面板重要预演明细

日期：2026-10-31。基线：`8625f09d42ef8955cf077c5ba967233c104adde9`。分支：`fix/approval-material-details`。

## 输入、输出和边界

输入：当前权威snapshot产品/业务时钟、原始账单与代扣指令，以及G9已有余额/当日限额占用。
输出：确认面板展示当日限额占用前后、预计每月节省、基金预计确认/到账时间；新增业务字段纳入既有view_hash。基金实际下单与面板共用纯日期helper，不另写日历、不改授权/风险/OTP/hash算法或冻结schema。

## 实现与事实绑定

- Approval.tsx在每个预演账户的可用余额行之后显示当日限额占用before→after，直接使用G9的daily_used_before_cents/daily_used_after_cents；原有7天预留保持。两个daily_used字段原本已纳入语义hash，新测试确认任一字段变化都会改变hash。
- cancel_subscription新增monthly_saving字段。只有同一raw instruction/billing关系、客户/账户/商户匹配、指令ACTIVE且billing.active时，才用已集成normalize_period的真实账单金额/周期计算；非ACTIVE/无有效账单节省0。原始关系缺失或错配fail closed。ACTIVE但没有raw obligations时显示“暂无法确定”，不从展示view的金额推断正节省。
- funds.py新增fund_event_at(ProductView,now,SUB|RED)：MMF返回原now；其他产品复用已有trade_date、_plus_trading_days和09:00计算，分别使用confirm_days/settle_days。非法side、naive时间和非非负整数的日期元数据拒绝。合法15点截点、周末和时区规则不变。
- apply_subscribe/apply_redeem也调用同一helper，包括MMF分支。非MMF申购expiry仍为原确认日后两个交易日09:00；金额、净值、份额、冻结、赎回成本和其他算法未改。
- planview根据当前snapshot产品及sim_now计算expected_confirm_at/expected_settle_at，unit=datetime；B0/无preflight results同样可使用权威产品与业务clock。字段由既有_steps_payload/content_dict自动纳入view_hash，产品未知继续fail closed。
- 前端datetime字段用既有dateTime以北京时间格式化；非字符串或无效日期显示“时间待核实”，不崩溃或渲染HTML。节省未知时不追加货币单位。原审批按钮、延迟、risk_ack、OTP和确认hash提交逻辑未动。

## 红绿证据

所有测试均本地合成数据/纯函数或jsdom，无DB/API/真实模型/Docker/浏览器。

1. 新后端最初32条：**30 failed, 2 passed**。两个保留负控为未知产品拒绝、daily_used既有hash绑定。
2. inactive raw账单但展示view ACTIVE负控：**1 failed, 32 passed** → 修绿；缺raw不能正节省负控：**1 failed, 33 passed** → 改“暂无法确定”。合法199元/月与30元/周=130元/月夹具均补齐真实raw obligations，未为旧夹具放宽事实来源。
3. 非法日期元数据None/-1/string/bool负控：**8 failed, 35 passed** → 修绿。
4. 最终43条新后端回归，以精确基线替换funds.py/planview.py并在finally恢复：**41 failed, 2 passed**。包含真实apply_plan产生订单日期与面板一致（SUB/RED、BOND/MMF、15点前后、周末、UTC共20例）、fee/date/hash变化、B0/无preflight、raw关系/有效状态及未知产品/日期元数据拒绝。
5. `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/bankcore tests/gateway/w4b1 --ignore=tests/gateway/w4b1/db -q --tb=short -p no:cacheprovider` → **543 passed in 0.43s**，包含43新专项。一次初始命令误写不存在的test_calendar.py，pytest未运行任何测试；纠正为现有funds/period套件后验证，再运行本项广泛纯测。
6. 前端新7条最初全失败；最终补未知节省负控后8条，以精确基线Approval.tsx运行：**7 failed, 1 passed**，finally恢复后通过。
7. `npm test -- --no-cache --maxWorkers=2 --minWorkers=2` → **47 files, 453 passed**。此前仅新增7条时的452通过已被本最终回执取代，不相加。
8. `npm run build` → **tsc --noEmit及vite build通过**，55 modules，JS286.20kB。前端依赖从本机既有node_modules复制到本WT忽略目录，不新增/下载依赖、不改其他工作树；未采用原拟符号链接，避免测试缓存写共享目录。
9. 本次Python文件Ruff check/format --no-cache、git diff --check：通过。

## 未覆盖

未运行DB、API、模型、Docker、浏览器、部署或完整CI；未删除/修改另一单元的资金累计PG红测。实际三层确认/复演、最终精确集成SHA的本机CI与独审由协调者执行。未改AGENTS/PRD，待协调者汇总本记录。
