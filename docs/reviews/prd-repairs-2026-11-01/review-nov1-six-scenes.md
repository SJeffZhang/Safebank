# November 1 六场景 / 29需求独立覆盖审计

结论：当前生产链均有实现；本次没有找到“整个需求未开发”的证据。发现3个可复现残余边界缺陷（2个日期投影、1个定时目标显示），已先通知root并由root认领。原43结果继续为19 PASS /16 PARTIAL /8 FAIL；29项完整验收均未关闭。没有把缺新现场证据当成产品bug，也没有以原生表单补件替换助手原句结果。

## 审查对象与证据等级

- 精确集成head：`3512a24178004529bb942a3a9259ba10b5ef6c69`，只读git archive：`/private/tmp/sbg-nov1-repairs/review-six-scenes`。审查未读取其他WIP作为实现证据。
- 原始source `aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1`，generation `demo_20261101084917_8e1ed563`。报告目录 `/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/` 的requirements-coverage.json、requirements-gaps-final.md、primary-43-results.csv。其原结果/回执不改。
- 本次只读源码与pure：SQL execute边界可用合成行替换，真实load_bank_state/read/共享bankcore仍执行；不是实际DB/网络/API/模型/浏览器/部署证据。
- A正在处理query25/14/31/负环比，B处理T5hint发布读采纳，root处理历史MCCloader；这里只登记，不重复实现。
- 原43及clock/原生/SQL补件是原报告证据；组件acceptance中的作者PG数据另标，不称本次复跑现场。

## 29项矩阵

表内“实现/自动证据”是代码与组件证据，均不自动升级原正式case状态。路径相对精确archive；bankcore/tests等前缀按标准repo目录。

| 需求 | 原现场已观察/结果 | 当前实现与自动证据 | 未现场覆盖 / 明确残余 |
|---|---|---|---|
| T-1 | 01/03/04姓名、别名、SELF PASS；02仅追问PARTIAL。 | bankcore/banking.apply_transfer；parser当前客户/精确名/alias/phone优先及候选恢复；G21→G19→G3修复已有独审。bankcore/test_banking、gateway/w1/test_phone_transfer、nov1_query_cancel_parser。 | 手机号/近名选择完整助手链、真实红转账、UNKNOWN/重放仍待新现场。 |
| T-2 | 05 PASS仅既有同账号no_op，不是真新增。 | bankcore/banking.apply_payee、new_and_existing_payee_reference_resolves_actual_id为真新增及后续引用纯控；L2 FORM权限保留。 | 新账号手工录入黄确认与值级绑定现场未补；不能由05推出已验新建。 |
| T-3 | 06/36 MONTHLY创建PASS；clock补件各一次真实到期；07 PARTIAL、08 FAIL保持。 | standing.py实现ONCE/WEEKLY/MONTHLY/月底/结束/取消；events.py真实到期/失败/去重；取消按当前客户源+实际payee已修，不能信target_display。test_standing/test_events。 | 周期/月底、取消后真回调及重放等现场未逐项验；F3目标显示缺失见F3。 |
| T-4 | 09四人含自己创建PASS；付款补件同request两次仅一入账一PAID；10原查询PARTIAL。 | split.py分余数/本人/跨客户/进度/分页；L3 D4入账与PAID同事务。test_split及obligations_data/test_split_exec；本次未重跑真实PG。 | 付款后助手进度、三人余数/不含本人/新事件重复付款现场未补。 |
| T-5 | 01/03/04部分预演；40 PARTIAL且旧“最多”错误，未改原结果。 | preflight.py共享bankcore完整模拟；REPLAN取真实hint且绑定计划/会话/失败transfer步/金额收缩/TTL已独审；本次T5发布闭环交B与后续安全评审。 | 发布/读取/采纳单次完整闭环当前单元仍在修；不把无hint最大值算通过。 |
| B-1 | 12结构化消费正确但主回复FAIL；13商户查询FAIL。 | analytics与reads真实筛选/分页；当前query确定性取fresh业务月、完整聚合/商户匹配/安全摘要已实现。 | A负责25/14/31及负环比残余；原12/13无新模型现场，保持FAIL。 |
| B-2 | 14 PARTIAL双月源金额正确但旧结构卡/内嵌错月；16/35核心9月数值PASS。 | 双月UI按各自源month分卡、主月内嵌已独审；analytics八类新旧消费统一口径；历史商户目录root在修。 | A/root正在补月份查询/负环比/MCC目录；新head现场空月/前月零等未做。 |
| B-3 | 15 PARTIAL读出近期两异常；16/35九月三异常正证据。 | analytics四规则与时区/样本/客户过滤全纯控；15完整只读分句NO_WRITE已独审，混合写不吞。 | 每规则全部阈值、多卡候选及新head现场未补；读异常正确不代替X4成功。 |
| B-4 | 16报告核心644800/133190/38412bp及展开PASS。 | 固定报告只用真实汇总和异常，数字校验与月范围；未改为LLM授权。 | 历史分类目录/无数据/依赖失败新现场未补；分类root所有。 |
| C-1 | 17 PARTIAL，原剩余额度漏报/混淆可刷上限保留。 | banking.card_view按account累计，reads返回daily_used/remaining；query回复按余额、单笔、剩余日额收紧。 | 补卡后查询与各状态现场未补；源代码已存在，不属漏开发。 |
| C-2 | 18/35单笔收紧PASS。 | apply_card支持single/daily校验、FROZEN仅收紧、LOST拒绝；test_banking。 | 日限/无操作/过期重放/最大值现场未完整覆盖。 |
| C-3 | 19升单笔800000 RED+OTP确认PASS。 | 同共享apply_card，L2放松升红/审批绑定不变；原状态/计数保留。 | 日限升高、冻结/挂失拒绝和旧审批失效现场未补。 |
| C-4 | 20境外关闭、21开启PASS。 | ChannelFlags四项online/overseas/atm/quickpass均有真实模型、apply_card更新；external.py刷卡渠道检查。 | 另三项、混合开关和实际刷卡渠道拒放未现场覆盖，不属未实现。 |
| C-5 | 22a冻结、22b解冻PASS。 | banking.py FROZEN外转/刷卡拒绝、SELF例外；外部扣费走真实规则；test_banking/test_external。 | 37联动未成功，不能以22a/04拼接当X4通过；重复/LOST现场未补。 |
| C-6 | 23仅挂失PASS，无补卡。 | replace=true真实派生新卡/继承限额/默认渠道/current切换，账号用量不清零；test_cards_tightening_loosening_loss_and_replacement_preserve_counters。 | 明确补卡全链现场未验；不能说缺补卡开发或把23当补卡验收。 |
| S-1 | 24 PARTIAL，四月费候选26500正确；clock有效扣款有补件。 | analytics.detect_subscriptions真实交易识别，3次/间隔/5%/周费/客户隔离；test_analytics。 | 交易消费MCC目录root在修；新扣款后重新识别现场未补。 |
| S-2 | 25 FAIL span异常，24月费/28节省不替代全年查询。 | normalize_period逐项周52/月12/floor；standing取消有效节省；25安全span反馈已独审，query残余A所有。 | 全年318000分自然语言交付现场未补；不关闭25。 |
| S-3 | 26 PARTIAL金额45+200预留245正确，原范围日期/解释错；clock4个站内提醒。 | reserve.upcoming_window与events提醒/唯一作业通知真实链存在；query按fresh模拟时间范围并列代扣/定时。 | F3外行定时target_display空为明确显示缺陷；空日扫描/事件重投递/系统外推送不宣称通过。 |
| S-4 | 28/36取消PASS；实际音乐/健身各一次BLOCKED零扣款；旧grant仍ACTIVE是原缺陷。 | COMMITTED绑定身份后同事务REVOKED/tombstone；S1冲突重读真实row，DELETED+REVOKED仅deny记录不执行；本轮两单元已独审。 | root真实PG证据在nov1-cancellation-grants，S1当前归档pure72/36PGskip；未新做原43现场。 |
| S-5 | 27 PARTIAL唯一闲置健身正确但旧日期错；36取消集合正确。 | analytics真实usage60/120天、expected、培训/merchant规则与客户过滤；确定性摘要保留due_at。 | 完整闲置边界及取消后新助手查询现场未补。 |
| W-1 | 29现R3 PARTIAL；30直接R5零写PARTIAL，无原六题正路径。 | rq_v1六题由本人填写，L3计分/落库；固定FORM引导已独审；test_risk及test_risk_valid_until。 | F1真实R13期显示差一天；硬适当性用正确customer日期不受此显示缺陷影响；六题现场未补。 |
| W-2 | 31列表PARTIAL；32自然语言FAIL；原生比较补件PASS独立保留。 | products.py详情/2–4比较/NAV/揭示/30日序列真实实现；query当前目录完整分页及比较已修，A残余31。 | F2合法午夜基准日UTC截断引发NAV偏移；固定seed12点未触发；原生成功不改32。 |
| W-3 | 33非MMF红确认200000受理及T+1 SETTLED PASS；38b R2→R4硬DENY PASS。 | funds.py MMF即刻/非MMF冻结确认/适当性/15点周末/EXPIRED释放；test_funds。 | MMF申购等分支未现场；合法午夜NAV锚偏差见F2。38选择故障属X3，不以33覆盖。 |
| W-4 | 34助手缺holdings FAIL；原生MMF1000份即时100000、非MMF1000份后123110补件。 | funds.py真实预留/防双卖/成本尾差/部分及全部赎回/结算；gather现补get_holdings；test_funds。 | 助手赎回新head未live；两原生成功不能替代34。F2影响合法午夜NAV投影。 |
| W-5 | 34b持仓/订单核心PARTIAL；三SETTLED补件有SQL与原生UI。 | holdings_view真实NAV价值/预留/待确认；trade_date loader业务日已修，目录/订单名称摘要已修。 | FAILED/EXPIRED/跨客户助手刷新现场未全部覆盖；未重新现场就不转PASS。 |
| X-1 | 35同计划fresh月分析+800000→100000收紧黄确认PASS。 | 共享纯计划原子模拟，B2/C2真实chain保留；本轮月UI保留单月16/35正控。 | 分类root修、完整失败/拒绝/并发场景未现场，不以现有成功概括所有分支。 |
| X-2 | 36同计划取消+derived19900 MONTHLY SELF PASS，clock实际一次SELF及健身拦扣。 | standing共享有效月费派生、七日预留；grant归档/S1拒扣已补，保护WEEKLY未知费率不擅自换算。 | 旧grant ACTIVE源报告仍保留；新head回调重投递/旧根/原子失败现场未补。 |
| X-3 | 38正确三候选后G19 CONTRACT_INVALID FAIL，未995500预演/订单。 | UI真实parent→G19/仅action_id G3、显式typedSLOTS与singleflight已独审；gather取得risk/upcoming/product；L2原授权收缩。 | 完整选产品→派生→确认→结算无新模型现场；不能由UI绿或单申购成功说X3 PASS。 |
| X-4 | 37读取异常/卡/账号后tagged目标shape FAIL，零冻结/互转。 | 单次现有重生成仅安全field/shape反馈，不反射值、不猜目标；test_nov1_x4_plan_retry与cross_scene_x4_gather；后续原L2/预演。 | 冻结+200000SELF完整单计划、多卡/中途失败现场未验；22a/04分开PASS不能替代37。 |

## 确认残余（root已认领，不在此审计改代码）

**F1 [P2] 风险有效期在R13早显示一天。** `services/bank-backend/app/state.py:398` 直接对DB返回的awareUTC valid_until取.date()。persist InsertAssessment（:689）把正确业务日期与评估时分/时区组合成timestamptz；上海00:30评估对应UTC前日16:30。真实loader+R13 get_risk_profile（reads.py:425）会公开前一天，而R18 get_profile同一客户公开正确日期。独立probe期望两者2027-10-15，实际R13为2027-10-14。真实R13消费可达；这不是仅pure UTC输入的假象。`funds._assessed_level`及is_valid取customer.risk_valid_until，故本证据不支持“提前拒绝申购”或资金漏洞。

**F2 [P2] 合法午夜NAV锚点被UTC截断，非MMF净值偏移一个交易日。** `state.py:495` ProductDef.base_date=r[base_date].date()；seedlib.py:382明确此timestamptz的上海civil date为NAV anchor且输入接受aware时间。合法2026-10-15T00:00+08落DB UTC10/14T16:00；当天read(get_product)的NAV应base1.0000，实际为1.0001，nav_history同偏移。真实loader用该ProductView供持仓/基金计算，属于可达边界，但固定demo seed均12:00，不声称原43已触发或已造成错误结算。

**F3 [P3] R11外行定时目标显示丢失。** `reads.py:377` 仅从InstructionTarget的target_account_id/target_customer_id组display；EXTERNAL合法两者为null（state.py:_load_obligations明确如此），因此scheduled_items.target_display为空。独立真实read probe金额1000/总额1000仍正确，仅目标空。当前respond upcoming只能显示“已登记目标”；与泛型无目标相比不足以识别收款人。非授权/资金错，不能据此改变原26资金预留核对结果。

最小私有自动probe：`/private/tmp/sbg-nov1-repairs/test_review_six_scene_production.py`；当前**3失败/2正控通过**，日志`review-six-scenes-production.log`。上层clock规范化两正控证明R1曲线日与新评估计分经过生产loader正确。首轮probe拼错scheduled_transfers_total_cents导致AttributeError的harness失败已修正，当前3失败均业务断言，不混入结论。

建议最小文件边界：F1/F2仅state.py两个业务日期字段+定向loader测试；F3仅reads.py真实目标显示投影+定向read测试。保留customer/date硬适当性、真实scope/source/ID、无自由display授权。root已明确认领三处。

## 已撤回的候选与避免误判

- 直接构造BankState.sim_now UTC并调用R1、直接把UTC assessed_at给risk scorer可重现早一天（私有test_review_six_scene_dates.py 2红），但生产load_bank_state首行已转上海，read/execution/events均经过此loader；新production probe的2正控均过。故这2个pure差异**不是本报告生产bug**，已及时通知root撤回，不建议据此改共享纯规则。
- list_payees若给跨客户混合BankState会读全map，但生产payees SQL先按当前客户过滤；不据此报告泄露。
- 补卡后泛型“我的卡”出现新旧多候选是安全澄清，未证明取错current卡或重置用量；不能当未实现。
- 不运行某场景不等于该功能未开发；实际14个write dispatch、六题、补卡/四渠道、周期/结束/去重、AA余数、MMF/非MMF生命周期代码均存在，并有对应pure。

## 本次验证与其限度

从精确archive运行：`PYTHONDONTWRITEBYTECODE=1 GATEWAY_DB_TESTS=0 AGENT_DB_TESTS=0 <clean-own-WT>/.venv/bin/python -m pytest tests/bankcore tests/contracts tests/agent/test_cross_scene_gather.py tests/agent/test_cross_scene_x3_gather.py tests/agent/test_cross_scene_x4_gather.py tests/agent/test_nov1_x4_plan_retry.py tests/gateway/w3a -q -p no:cacheprovider` → **735 passed /7 skipped**。日志`/private/tmp/sbg-nov1-repairs/review-six-scenes-pure.log`。未运行任何真实PG/provider/service，跳过不能计通过。

先前独立组件报告（各自精确SHA与证据保留）：review-nov1-parser.md、review-nov1-plan-replan.md、review-nov1-errors.md、review-nov1-grants.md、review-nov1-s1-boundary.md。其APPROVE仅覆盖对应代码和已跑自动边界；原case15/25/38/37等需新授权现场才能重判。

作者既有PG分支证据可从archive docs/acceptance/nov1-cancellation-grants.md、nov1-l2-query-cancel.md等追溯；本次没有替作者复跑PG，也没有把socket/DB skip或mocked-port SSE叫真实资金验收。

## 来源文件校验

- `requirements-coverage.json` SHA256 `64963d74944c2d3c34ab0351ae8d40b691e5793c2c9c4b3e4ba1b622f2f20e63`
- `requirements-gaps-final.md` SHA256 `5e9180e2789174c02889e55139b3d5615d94a86157aa9f8aebfc3878135ff738`
- `primary-43-results.csv` SHA256 `6c0d1ff29a08d0f16d5847b290e83d8938fb5e0482f2254ed492b9681dca5abb`
