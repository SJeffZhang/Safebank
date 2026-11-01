# 2026-11-01 助手查询与显示失败只读调查

基线：aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1。仅原始报告与生产源码静态调查；不运行模型、API、浏览器、Docker/DB，不修改产品代码。

## 原始结论与证据边界

已核对正式CSV为19通过/16部分/8失败；六项补测独立，不改变正式结果。

## 查询与显示缺陷清单

详见后文正式用例逐项表，覆盖四条查询FAIL与全部查询PARTIAL。

## 文件与测试职责边界

详见后文同名完整章节。

## 未覆盖与非法请求

详见后文同名完整章节。

## 已确认的共因（逐步补证据）

1. 只读授权与展示混线：L2 parser 只覆盖极窄只读fullmatch（parser.py:285）；未识别的只读句成为INTENT_UNCLEAR。service.py:482创建空FORM_REQUIRED；graph.py:399强制unclear但保留needs_write=false，仍gather；cards.py:330额外产CLARIFICATION，Assistant.tsx每轮又取current clarification并显示空表单。15条正式只读句07/10/12/13/14/15/17/24/25/26/27/29/31/32/34b均受此缺口影响。不能通过给只读句创建write scope修复，也不能只隐藏前端卡就宣布链路修复。
2. Respond事实摘要不是完整读模型。respond.py:769 `_summarise_other` 将未专门支持工具原JSON截为2000字符；list_products/compare_products/get_product/get_holdings/list_scheduled/get_split_status/get_risk_profile均走此分支；_join_capped:792再受6000字符全局上限影响。32实际仅list_products(6项)，未compare_products；其DOM产品卡含prd_sbond/prd_stable，但最终文字称不存在，是摘要数据丢失的强假设，需用原schema六项读数据的纯测试确认截断位置，不能仅凭UI断言实际LLM上下文。
3. 订阅摘要字段语义错配：respond.py:690起只处理items；items非空时忽略total_monthly_cents/total_annual_cents，以及upcoming_charges的scheduled_items/charges_total_cents/scheduled_transfers_total_cents/reserve_total_cents。:713把next_due_at / due_at / last_charged_at择一后统一标“下次”，明确将历史扣款误称未来扣款。解释26漏200元定时预留、27错写9月25日；真实日期须从list_subscriptions读取。
4. 卡摘要信息损失：respond.py:747起只保留last4/status/single_limit/daily_limit，丢daily_used_cents和remaining_daily_cents。17已有get_cards与get_accounts，仍漏剩19600元；不能从余额6100元推出单笔可刷6100元（当时单笔5000元）。
5. 14前端双月：bill-month.ts:9先将含“前一个月”的文本返回null；Assistant.tsx:143将null传给所有响应卡；:574把9月和8月卡都拒为月份不一致；AssistantWorkspace:21以空月份初始化。账单比较需要多月展示合同，不能将第二月当陈旧卡拒显。DOM实际内嵌10月另需绑定源核查，不能仅用静态空值推翻现场。

## 正式用例逐项缺陷与最小红测入口

共同证据根目录：`/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/`，每行的`CASE-result.txt`均在`screens/`，`CASE-read-oracle.json`在目录根。正式CSV是`primary-43-results.csv`。以下不以supplement的成功覆盖正式结果。

| ID / 正式结果 | 原请求与实际错误位置 | 定位/根因证据与边界 | 最小纯测与建议生产文件 |
|---|---|---|---|
| 07 PARTIAL | 我有哪些定时转账；DOM:20正确2条，但后一项2026-11-04 22:00 UTC，收款人泛称他行；:37空FORM | oracle仅get_profile/list_scheduled/get_accounts。L3 state.py:251 EXTERNAL target_display固定“他行收款人”；respond无定时专用摘要，保留UTC原JSON；通用FORM见共因1 | parser精确原话→NO_WRITE且scope空；scheduled摘要将11/04 22Z显示11/05 06上海、通过当前payee_id读取名称而非猜；测试tests/gateway/w1/test_parser_coverage*.py、tests/agent/test_respond.py。稳定目标显示需L3/state与父任务取消定位共用事实 |
| 10 PARTIAL | AA收款进度怎么样；DOM:20数值400/100/0/300及三人未付正确；:37空FORM | 只读答案已正确，不应重写AA账本；FORM是parser/clarification混线 | 原句parser→NO_WRITE/空write scope；compiled graph needs_write=false仅读get_split_status，generate_cards无CLARIFICATION，current clarification为空；保留本轮支付前0/300，不能引入补测后100/200 |
| 12 FAIL | 查一下我10月的消费流水；卡/展开550.50正确，终句固定“请求无法处理…”，原包装+FORM外露 | oracle的账户/交易/summary中有UI后补读取，不能全当L1 state.reads。respond.py:127/166空结果或非402/403/429异常→GENERIC_ERROR；verify.py:566–600核验失败且repair后仍错也→同文；无当前金融plan，不推断G8失败 | 单独写“原句+真实结构ReadRecord→reply_draft→OutputVerifier”测试分离两候选。优先保存本sid已有checkpoint的reply_draft/最终AI message/route_error及图reads元数据（须root按秘密限制选择性读取），才能确认真实回退节点。测试tests/agent/test_bill_accuracy.py、test_respond.py、test_verify*.py；不要放宽数字校验或把UI后补金额当L1证据 |
| 13 FAIL | 我在兰州拉面花了多少钱；DOM:50左右声称只有账户数据无商户，UI却有交易卡与550.50月概览 | oracle实际get_transactions(acc_zs_chk,04/19→10/15 23:59:59,merchant_id=null,limit=50)，没有商户筛选、没有cursor续页。respond.py:660把每条memo/counterparty均写入；6000全局预算在已有账户block后若交易block太长，则整个block丢弃（_join_capped仅首块允许部分截断），能解释终答只见账户。读取按发生时间升序，50条≠filtered_all合计可按商户求和。合同只有merchant_id筛选，没有商户名称查询工具；R3返回merchant_name=None但counterparty可能含名称 | 纯测1：账户+50条包装交易，其中兰州拉面4550分置于后部，摘要不得丢掉用户目标/不得声称无明细。纯测2：gather对目标名不能猜merchant_id；通过完整分页真实映射并按merchant_id查询或透明说明期间/未完成读取，禁止仅求当前页。建议gather.py/response结构摘要；必要调整只读检索能力要同步合同/PRD，不能无证据硬编码ID。保留“不含时间”的期间澄清/完整历史边界 |
| 14 PARTIAL | 我上个月各类消费的占比，和前一个月比怎么样；DOM:22/26两卡错月警告；:34显示2026年10月但:36要求选月；聊天9/8月正确 | oracle只有get_profile及9月/8月summary，无UI新月读。bill-month.ts:9命中“前一个月”直接null；两张合法月卡都被Assistant.tsx:574与requestedMonth=null比对拒显。workspace month为空但picker fallback当前10月，形成显示与实际未读取矛盾 | frontend bill-month或新的展示月份结构针对单月/双月分别纯测；相邻月允许9/8同时展示，同时保持真正stale10月卡被拒。AssistantTopics.test.tsx + AssistantWorkspace；不要简单删除所有月份一致性检查。只读NO_WRITE parser同测 |
| 15 PARTIAL | 最近有没有异常交易；DOM:34窗口9/15–10/15且2异常正确，原包装和FORM仍出现 | 业务异常检测成功；原始标签源于gather包装→ReadRecord→cards.py残留counterparty/display_name→Assistant字符串渲染；日期范围有真实读取不需改成9月全月 | parser原句只读、卡UI有安全文本无`<untrusted_data`/source协议字面值；保留2异常窗口不混16的9月3异常。tests/agent/test_cards.py、frontend AssistantWire.test.tsx |
| 17 PARTIAL | 我的卡今天还能刷多少；DOM:30单笔5000/日20000/余额6100正确，但没报当日剩19600，结论6100需区分单笔/当日总量 | oracle确有get_cards(acc_zs_chk)；_summarise_cards:747丢remaining_daily/daily_used；_summarise_accounts:725只保留balance，未保留available/pending | 输入卡daily_used40000、remaining1960000、single500000、账户available610000；摘要/回复区分当日剩19600、受资金限制总可刷6100、单笔最多5000；挂失/冻结不宣称能刷。tests/agent/test_respond.py及verify语义负例 |
| 24 PARTIAL | 帮我找出所有周期性扣费；四月费共265正确，DOM内部si和FORM | list/detect行已读，_filter_untrusted删merchant_name，前端回退instruction_id，因此卡内部ID外露；回复核心无需改账本 | 原句parser只读；卡显示安全商户名称+典型金额/周期、保留来源元数据但不把si当显示名；tests/agent/test_cards.py、AssistantWire.test.tsx |
| 25 FAIL | 我有哪些订阅，一年总共要花多少；DOM:18 alert Understanding slots.0 invalid character span，[0,0]/year及Pydantic链接 | oracle reads=[]，先失败understand，非“年费算错”。state.Slot:42拒绝start>=end正确；understand.py:70默认一次重试，最终原样raise；main.py:544–553将str(exc)/typename写SSE；BFF:274透传→Assistant:88/153显示。strict无法保证跨元素start<end | 正式句+首次非法span、二次合法→只读list_subscriptions并年化3180；连续坏span→仍拒绝、预算只1次、gather不被调用、SSE稳定安全错误码/文案、无ValidationError/input_value/errors.pydantic.dev。不得自动修span或放宽语义。tests/agent/test_llm_adapters.py已有重试/最终抛测试可扩；流边界tests/agent/test_http*.py / frontend/server测试以真实文件名匹配 |
| 26 PARTIAL | 未来7天有哪些扣费；DOM:28将窗口写10/18–10/24，45元商户正确但未解释卡片200元定时 | oracle仅get_profile/upcoming_charges(days7)。_summarise_subscriptions忽略scheduled_items和total；get_profile由_resolve_business_date_line获取仅存last_business_date，不加入正常state.reads；respond不读取state.business_date，所以缺窗口起点而自由编日期 | upcoming数据含2商户4500+1定时20000，reserve24500；摘要显示两类和总预留且不把200当商户费；用sim_now10/15 09:00上海生成(10/15,10/22]真实时间窗，不从第一扣款推日期。tests/agent/test_respond.py、test_subs_bills_prompt.py；gather日期保留/response business_date来源，不使用墙钟 |
| 27 PARTIAL | 哪些订阅是闲置没用的；DOM:40唯一健身199正确却下次09/25，应10/25 | oracle有detect90（G3谓词）、profile、detect180、list ACTIVE。_summarise_subscriptions把detect.last_charged_at统一标“下次”；这是已确认源码语义错误，即便list也有10/25，两个相互冲突来源诱导输出 | detect记录只有09/25 last_charged时只称“最近扣款”；list next_due10/25才称“下次”。有冲突时不把历史字段覆盖next_due；tests/agent/test_respond.py |
| 29 PARTIAL | 我的风险等级是多少；DOM:20正确3级但空FORM | L2只读漏识别；不应改风险问卷/适当性 | parser只读+scope空、get_risk_profile只读答案无FORM；并验证R3保持不变 |
| 31 PARTIAL | 有哪些理财产品适合我；4款≤R3卡正确但名被prd_*替代；聊天只详述首款均衡混合，FORM | product fallback摘要2000字符、卡过滤name；不是风险筛选放错。仍需全部产品列表或明确节选标示，不能假说只有首款存在 | 六产品/四适配产品完整安全名称+风险/NAV/起购/确认结算摘要，长risk_disclosure不挤掉后排产品；卡不显示prd作为默认名称。tests/agent/test_respond.py / test_cards.py / AssistantWire |
| 32 FAIL | 比较一下稳盈短债和稳健增利；DOM:39称两产品不存在，卡实际含prd_sbond/prd_stable | oracle仅profile/list_products(max_risk=5,limit50),items6；未compare_products。fallback raw JSON2000截断为强假设，不可称该次已确认provider上下文；原生compare补测PASS只定位L3工具可用 | gather输入6真实返回产品，匹配两名称必须基于返回ID并调用compare_products；不能硬编码/猜ID；respond专用产品摘要需保留后排两项全部比较关键字段，长揭示不遮盖。tests/agent/test_llm_adapters.py、test_funds_prompt.py、test_respond.py。原32仍FAIL |
| 34b PARTIAL | 我的持仓和在途订单有哪些；DOM:33主数据正确，但prd/ord、SUB/PENDING_CONFIRM/UTC原值外露，名称“稳健理财”不实，交易日10/14 | get_holdings只含product_id、未读产品名；不能让模型按ID猜名。respond走raw JSON。更深日期源：L3 state.py:766把业务date午夜+08写timestamptz，:562直接r['trade_date'].date()，UTC返回将10/15变10/14；静态高可信，尚需root已有数据选择性确认 | 读取产品名真实关联；pending订单不等于持仓/available；确认时间统一上海。L3 date纯roundtrip红测：2026-10-15 00+08→PG等价UTC10/14 16→load仍10/15。tests/obligations_data/test_funds*.py，bank state loader；L1 respond/holdings摘要与card分别测试。不能只改LLM prompt掩盖可能wire日期错 |

### 其他PARTIAL的展示/引导边界

| ID | 产品问题与正确安全行为 | 建议最小红测/文件边界 |
|---|---|---|
| 02 给王转200块 | 候选缺失是产品问题；“块”可解析，短名王不能自动选王五/王武。当前_payees只精确/完整同音，默认Parsed→空FORM | 当前客户候选前缀应返回二选一SLOTS，无资金作用域放宽/无默认选中；parser.py:517/792、tests/gateway/w1/test_parser*.py；详细根因归目标/写路径审计 |
| 30 帮我把风险测评改成R5 | 直接代改风险不是合法写授权，零写正确；缺固定六题问卷解释/跳转是产品引导问题 | 保持ORIGIN_NOT_ALLOWED，固定安全提示并NAVIGATE问卷；绝不自动编答案。L1明确unsupported请求回复与navigate卡测试；真实六题正路径仍未覆盖 |
| 39 给钱七6222020000009999转5000 | 未登记收款人不能直接转出，零写正确；空FORM未告知需先登记 | 未知账户只能解释登记路径，不自动新增、不以用户贴账号获得直接transfer权限；parser及UI指引；该句不是新增收款人正向验收 |
| 40 给王五转50000元 | 正式REPLAN/RED、首阻CARD_SINGLE_LIMIT（当前1000）正确；DOM:20“最多50,000元”误导，不是冻结拦截 | preflight.py:271与decision.py:158–215会生成shrunk金额hint；应结合单笔/日余/可用/7日预留等约束，仅推荐真正可行的更小值并保持需用户选择、不自动降额。建议网关工作流审计继续确认本轮hint数据来源，不能凭最终文字断定50k出自哪一步；respond不能把风险红线翻译为可重试后绕过硬限制 |
| 41 我能不能给王五转100？ | 咨询疑问不授权写是正确安全行为；应读余额/卡/收款人给可行性信息，不应空FORM要求转账细节 | NO_WRITE咨询分流，scope空、无evaluate/execute、卡片无确认动作；回复说明估算/以正式预演为准，不能删问号改成祈使计PASS |

## 定点追加：32并非只有提示词遗漏

L3 `services/bank-backend/app/state.py:483–523` 按id稳定排序产品，并对每款调用get_product生成含30日nav_history的ProductView。`services/bank-backend/app/reads.py:437–447` 的list_products直接对每项model_dump，未按PRD允许的列表`nav_history=[]`做投影；因此六款列表的原始JSON很长。回应的2000字符fallback会消耗于排在前面的growth/mixed及其NAV历史，后部sbond/stable比前排更易被截断。现场32卡中仍有后部ID、回复仅承认成长股票/均衡混合，与这条链一致。最小测试应使用实际ProductView字段和30点nav_history，而非两个极短产品dict，否则不会触发现场长度边界。此处源码事实确定；本次模型最终输入的精确截断位置未保存，不将其写成已读到该次provider trace。

## 文件与测试职责边界（建议给root拆小单元）

1. **L2只读解析/clarification单元**：生产仅`services/gateway/app/utterance/parser.py`，必要时service.py的只读directive生命周期；测试从`tests/gateway/w1/test_bill_readonly_parser.py`、`test_parser_coverage*.py`的现有快照夹具扩15句参数化。验收：NO_WRITE且空write scope，不留FORM_REQUIRED；混合未知写后缀仍INTENT_UNCLEAR，无咨询转账授权；30/39保持受限，02只给候选不自动选。
2. **L1只读取数/摘要单元**：`services/agent/app/llm/gather.py`、`respond.py`及必要的无授权纯读辅助文件；测试`tests/agent/test_llm_adapters.py`、`test_respond.py`、`test_bill_accuracy.py`、`test_current_date.py`、`test_subs_bills_prompt.py`。应按工具完整语义摘要：产品关键字段/多项、卡余限、账户available、年费合计、未来代扣与定时分列、历史扣费日期。不要无限增prompt预算或靠模型猜ID/求不完整分页合计。32必须实际调用compare_products、13须明确期间和完整读取/服务端商户筛选；纯mock graph不能记现场通过。
3. **理解校验与SSE错误单元**：`understand.py`给有界修正可操作的脱敏span约束；`main.py`映射耗尽错误而非str(exc)；BFF `frontend/server/bff.mjs`与Assistant.tsx防止内部异常透传。精确现有测试为`tests/agent/test_llm_adapters.py`、`test_sse_wire.py`、`test_service.py`、`frontend/server/bff.test.mjs`，前端错误状态可加Assistant集成测试。保留一次重试和语义拒绝，不追加无限调用；402/403/429继续停止。
4. **展示数据与多月视图单元**：`services/agent/app/llm/cards.py`生成展示安全数据，`frontend/src/mobile/Assistant.tsx`、`AssistantWorkspace.tsx`、`bill-month.ts`显示纯文本安全名称/月份/时间/状态，不把协议包装和内部实体ID当产品文案。测试`tests/agent/test_cards.py`、`test_respond_cards.py`、`frontend/src/mobile/AssistantWire.test.tsx`、`AssistantTopics.test.tsx`。现有wire测试明确期望产品ID，须按用户可读名称更新；不能用去掉untrusted追踪换展示。恶意名字含HTML/包装关闭串时仍纯文本，不提升任何授权。
5. **L3日期/目标关联单元**：`services/bank-backend/app/state.py`业务时区还原trade_date；定时target_display从稳定payee关联填显示名，必须与08取消目标匹配单元协调避免重复改同文件。测试`tests/obligations_data/test_funds_exec.py`、`test_fund_events.py`及父任务已有state loader夹具。数据库值/时区测试由root负责，本子任务未运行。
6. **40提示可行金额单元**：网关preflight/decision的hint应满足组合约束；请与写路径审计合并职责。只读审计未定到本次50k hint的唯一来源，不直接下改动结论。

建议门禁命令由root在实现后选受影响的上述pytest/Vitest/BFF子集；本报告不声称执行过任何测试。真实Postgres、API、模型、部署/CI/重演均由root独占；修复后现场原43句结果需另轮记录，保留本轮不可变CSV。

## 未覆盖与不合法写请求

- 正式25是合法只读请求，坏span属于模型结构输出，不能让用户补充字符下标；必须修异常处理并完成年费读取。
- 正式30直接改R5不构成合法问卷提交；39未登记目标转账、41咨询疑问零写均是正确安全停点。它们的PARTIAL仅用户解释/引导欠缺，不应扩授权“修绿”。
- 02短名歧义要具体候选；07与10已经正确取数，不能把多余FORM误说成后端列表不存在。
- 原13未给时间，合同读取范围最大180天；不能把默认180天部分页称终身全部消费。完整分页、空商户、类别过滤/组合筛选尚未现场覆盖。
- 原生compare_products/原生赎回/基金结算、时钟45检查、D4付款成功，均单列supplement，不能覆盖32/34/X3/X4原句失败，也不能把10进度换成补测后的100/200。
- requirements-gaps-final.md还指出B2历史tx.category白名单与PRD MCC→8类未收口。当前core金额正确不表示类别口径完整通过；该问题独立于本轮14前端错月，建议L3分析单元另验历史/新增同口径。
- 新收款人创建、手机号、ONCE/WEEKLY、补卡、其他渠道、固定六题正路径、缺/过期测评、分页空结果和全部日期/故障分支未覆盖，不能写成产品失败或已通过。

## 调查退出记录

- 读取原REPORT、正式CSV、formal-parser-gaps、requirements-gaps-final、supplement-results、相关只读oracle及DOM，定点对照生产源码和既有测试。
- 用CSV重新计数：PASS 19 / PARTIAL 16 / FAIL 8。没有重写CSV或改判。
- 没有调用LLM、API、浏览器、Docker或数据库；没有运行产品测试；没有读取.env、真实凭据文件或打印整块SQL回执。
- 只创建/追加本文件；仓库`git status --short`无输出，未改产品或共享AGENTS/PRD。
- 重要未决：12真实fallback阶段需已有L1 checkpoint/provider证据；32精确provider上下文未保存；34b日期根因需root验证PG还原时区；40真正hint源需网关审计。其余源码可解释问题与现场已见缺陷已分开标注。
