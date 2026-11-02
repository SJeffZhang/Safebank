# 独立审查：审批材料、节省与基金日期

目标源提交74e8c196ab7f07f4623707a2f10312a299250b0e；根集成映射7b1e57a。审查者未实现本单元，不修改被审实现。仅只读源码与纯测试/前端验证，无DB/provider/Docker/浏览器。

## 结论

**APPROVE（修复后精确源03435f00e4da236a85fb425ceac404931cc6c5a5）**。首轮P1及REQUEST_CHANGES证据保留下文；最终纯测与独立负控已核实闭合。此结论不代替三层或真实PG验收。

## 核查项

- 月/周节省：raw授权关系、状态/归属、失效和未知。
- 基金日期：真实订单与面板同源，交易日/15点截点/时区/非法元数据。
- hash与前端：日期和daily used绑定、展示/未知值、审批/OTP约束。

## 发现与证据

待逐项追加。

## 运行验证与未覆盖

尚未运行；不把静态推断记为实测。

### [P1] 正节省未绑定实际可用的授权上限（74e8c19）

位置：services/gateway/app/approval/planview.py:272–273。

当前active仅合并展示view状态、raw instruction.status与billing.active，随后直接按billing.amount_cents折算。构造合法MerchantMandate（经冻结模型完整model_validate）：bill为19900分/月，raw ACTIVE授权max_amount_cents=100分，归属与billing关系均正确。build_view仍显示monthly_saving=199.00元；同一state的真实bankcore apply_merchant_charge返回BLOCKED/MANDATE_AMOUNT_EXCEEDED，不会扣掉这笔账单。取消已不能授权当前账单的mandate不应承诺节省该金额。

独立命令在A精确源head只读执行：复用cancel_ctx，校验修改后的MerchantMandate，build_view并对同一billing执行纯apply_merchant_charge。输出：

`{'billing_amount_cents': 19900, 'authorized_cap_cents': 100, 'panel_monthly_saving': '199.00', 'actual_charge_status': 'BLOCKED', 'actual_charge_code': 'MANDATE_AMOUNT_EXCEEDED'}`

建议：正节省先证明当前raw授权能覆盖实际下一次账单（金额cap、有效时间/到期、当前/下一次扣费），失效0或无法确定，保留客户/账户/商户关系检查。该发现只涉及新增确认材料事实准确性，不表明执行越权或实际多扣款。协调者已派作者修复；审查者不改实现，修复后需独立复验。

### 已核对的非阻塞项

- 周费30元按既有normalize_period折算为每月130元，纯整数分、52周/年后floor_div_12；不是自行新增金融算法。
- 新基金helper抽取的非MMF交易日/15点/09:00逻辑与旧算法等价，apply_subscribe/apply_redeem和面板同源；MMF仍即时。新增日期通过原steps fields进入content_dict→view_hash，daily_used原来已在preflight语义hash内。
- 前端日期按既有Asia/Shanghai格式化，坏日期显示“时间待核实”；daily_used直接Money animate=false展示before/after。确认可信手势、停留、risk_ack、OTP、view_hash提交代码未改。

### 已运行（首轮，尚未包含P1的新修复）

`PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/bankcore tests/gateway/w4b1 --ignore=tests/gateway/w4b1/db -q --tb=short -p no:cacheprovider` → 543 passed in 0.74s。这个绿测不覆盖上述新负控，也不授最终APPROVE。前端独验正在运行。

首轮前端独验：`npm test -- --no-cache --maxWorkers=2 --minWorkers=2` → 47 files / 453 passed（53.05秒）；`npm run build` → TypeScript与Vite成功，55 modules。运行后被审工作树git status干净。无真实服务调用或浏览器。

首轮结论：**REQUEST_CHANGES**。已确认的P1需作者补实际有效授权负控并修复，现有测试/构建通过不能抵消该错误。基金日期同源、hash和daily used UI部分未发现新增阻塞；生产三层/真实确认/OTP/订单落库仍未在本审查运行。后续修复SHA与复核追加于本文件。


## 修复后独立复核：03435f00e4da236a85fb425ceac404931cc6c5a5

作者在74e8c19后新增窄修复。新subscription_savings仅作只读报告，复用raw关系/日历校验，先验证snapshot覆盖与versions、当前customer/account、实际merchant、唯一mandate和billing绑定；已知不可扣（cap、ACTIVE、created、实际next_due/end_at、已处理due）返回0，缺失/冲突/未知返回None。G9将None显示“暂无法确定”，取消结果在执行前态计算，未知按冻结结果字段保守返回0。未改扣费授权、L2规则、审批提交或前端。

原P1的合法完整MerchantMandate重现已独立复验：19900分账单/cap100分，实际apply_merchant_charge仍BLOCKED/MANDATE_AMOUNT_EXCEEDED且无资金effects，G9现0.00、取消结果monthly_saving_cents为0，hash随确认材料变化。end_at比实际next_due早1微秒为0；同一瞬间的UTC end_at等于Shanghai due仍允许正值。versions覆盖未知、merchant缺失、双mandate均显示未知而非正节省。该6项probe调用实际G9、helper及取消消费方，保留在/private/tmp/sbg-g9-review-03435f0/test_review_g9_independent.py。

独立运行：

- 精确修复源：tests/bankcore + tests/gateway/w4b1（排除db）→ **571 passed / 0.33s**，日志g9-review-final-python.log。
- git archive固定03435f0快照中新增独立probe → **6 passed / 0.45s**，日志g9-review-final-independent.log。首次probe自身把dict传给apply_plan而报AttributeError（1失败/5通过），经冻结PlanBody校验步骤后重跑6绿；这是审查脚本形状修正，不是产品红绿。
- 首轮前端453项与TypeScript/Vite build已通过；该follow-up没有前端或fund_event_at改动，因此未无故重复FE全量。

最终结论：原新增确认材料P1已闭合，**APPROVE**本次范围。金额/周月率不会由未知原始来源得出正节省；基金面板日期与实际订单共用函数，hash包含日期/每日已用量，相关前端未知展示已首轮验证。未做真实生产LLM、三层确认/OTP、基金订单SQL落库、并发或DB/provider测试；由协调者最终完整CI/真实PG验收核定。
