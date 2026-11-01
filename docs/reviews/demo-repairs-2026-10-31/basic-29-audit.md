# 六场景 29 项基础开发覆盖核查

核查基线：`d37b850f35c204df66fb00b24c48e3827aee4757`。读取方式：该精确提交的 `git archive`，目录 `/private/tmp/sbg-demo-repairs-20261031/basic-29-d37b850`；不把之后并行工作树的 WIP 当成基线事实。输入为 PRD §5 / §9.2 与归档 `docs/reviews/demo-report-2026-10-31/requirements-audit.md`。

本报告核对生产入口、实际实现与自动化测试文件。**有实现/有测试不等于本轮现场通过**；本核查没有运行真实 API、模型、数据库、Docker、浏览器或部署。以下新发现均有代码或生产纯函数复现，不根据 LLM 回复授绿。已分配的 REMINDER/通知 runtime、X1/X3/X4 必要 gather 继续由原 owner 收口，不重复报成未实现。

## 口径与共用生产入口

- App 读路径：`frontend/src/mobile/api.ts:read` → 同站 BFF `frontend/server/bff.mjs` 白名单 `/api/tools/read/{tool}` → G2 `services/gateway/app/api/tools.py:fetch_read` → L3 `services/bank-backend/app/reads.py:read`。
- App 写路径：`api.ts:propose` → G19 保存 FORM → G4 服务端构造计划/预演 → G9 权威面板 → 确认/OTP → G8。BFF 不提交自造 steps，仍使用现有表单来源/永久绑定/epoch/审批规则。
- 助手路径：App messages → G3 当前 typed 原话/JWT → `services/agent/app/graph/graph.py` → L1 gather/plan → 同一 G4/G8。L2 才决定许可；成功回复只解释绑定的执行事实。
- 共享实际规则：`packages/bankcore` 的 apply_plan、ledger、standing、funds；预演使用同一纯规则，L3 在事务中持久化 effects。定时/商户/基金/D4 外部事件经现有 S1 事件引擎，不由 L1 直接写银行。
- 下表的测试列仅表示读到相应测试路径/断言。除后述明确列出的新纯函数复现与各组件交接中的实际红绿，不声称在本轮重新运行全部测试或 PG。与本人实现相关的问卷/当前根/手机号/split/math 候选只映射事实，**不作自审自批**。

## 有实证的新问题与修复候选

| 编号 | 影响需求 | 基线的实质问题 / 证据 | 已交修复或 owner；当前边界 |
|---|---|---|---|
| F1 | T-4 | G2 `api/tools.py:96–99` 因旧“无SplitView”guard，对任何非空 request_id 无条件404；如今 BankState.splits 与 L3 by-ID 已存在。新纯回归旧码1失败/5通过，列表/null ID正控原本可用。 | `cbc744b9d4f30b5f9478d043f1c71f08c2f834c2`：精确 split ID + 当前客户所属 account 才放行，W2相关209通过；独审/真实G2 PG与最终集成门禁以协调者结果为准。不能把整个AA进度说成未实现。 |
| F2 | B-1/B-2/B-4、S-1/S-5 | 实际 transfer producer 写 P2P_INTERNAL/P2P_EXTERNAL，analytics 原只认 TRANSFER_EXTERNAL；实际 merchant producer 写 MERCHANT_CHARGE，analytics 原只认 MANDATE。生产纯执行10000分转账后 transfer_out=0；实际3000分代扣后消费=0。merchant effects还缺 merchant_id/类别，导致后续周期识别与商户过滤丢事实，canonical charge 还可能误计使用。 | analytics `6b05bf1d634a48eed53b9f5bed995dea64acf012`、producer `382d9ba34e3f093370affefbcb34df982c5c8e3d`、D4类别 `4011d5d4bf8c83ca8b4420002fc6f5ff6cdb2189`。实际producer→aggregate、真实三期扣费→识别/usage、effects一致性有新纯测；PG与独审仍以回执为准。 |
| F3 | S-4 | WEEKLY取消结果把单次周费当 monthly_saving。生产纯取消3000分周费返回月省3000/年省156000，而生产 list_subscriptions 月均13000；金额口径不一致。 | `97d2b7d0df47d4f4fd586def6543632a38abae59`：共享严格整数 normalize_period，WEEKLY月均=floor(年/12)；与实际L3读结果比对，新/相关123纯测通过，待最终门禁。 |
| F4（P1） | T-3、C-1及日/24h/卡限额约束 | scheduled producer 原写通用 SCHEDULED_TRANSFER，fresh loader 原仅认P2P，纯模拟累加在fresh snapshot丢失。协调者对真实EventService提交→fresh load亲跑PG：3失败/2本人正控通过，两个外转累计0应100000，另merchant元数据缺失。 | `382d9ba`：新写P2P/SELF，历史generic按真实ledger credit是否属于当前客户区分，shared classifier供analytics和fresh counters；不把本人定时全计外转。协调者PG旧红日志 `/tmp/sbg-transaction-reload-red.log`。首次候选green为3通过/2个legacy夹具失败：旧夹具UPDATE不可变历史被append-only guard正确拒绝，不记业务失败；fixture-only `47267a26d9ae3998a95c000682892504327e1455` 改为旧producer首次正常append，根映射6e8f327后协调者亲跑本文件**5条全绿**。独审/最终集成CI仍另核。 |
| F5 | T-5、X-2、W-3/W-4预演呈现 | G9已有daily_used字段但App只显示available与reserve；取消步骤字段只有ID/商户，缺X2明确月节省；基金面板只有产品/金额/份额，预演steps只保留public effects，缺确认/到账日期，日期也未进入可见语义hash。 | 协调者已明确独占planview/Approval及共享fund日历helper修复。此处按约定明细缺失列P2，不否定已有执行/揭示/余额/结算逻辑，避免与已修的前端枚举/确认状态重复。 |
| F6（P1） | W-3、X-3资金预留 | `PreflightStage.run` 的 has_transfer 只认 tool==transfer，单独 subscribe_product 跳过7日reserve比较。精确基线真实EntityStage→PreflightStage：全额2000000分MMF申购，实际bankcore模拟shortfall从0增至3000，但preflight executable=True、仅IRREVERSIBLE_STEP、reserve展示0、无SCHEDULED_SHORTFALL。非货币冻结同样减少available，未由普通F/app来源规则兜住。 | 已即时告知协调者，未擅改preflight。建议至少所有申购都做同一reserve比较/归因，保留派生模板与原金额授权；以其后续修复、负控、PG/独审回执为准。 |

另有 T-3 OTHER 本行他人定时的**静态高风险候选**已交独立 owner：EventService源客户scope可能不含peer账户，且只锁/增源客户version；需要真实OTHER定时及双客户version测试确认。该候选未由本核查运行数据库，不写成已确认现场失败。协调者已分配它，避免重复实现。

## 29 项矩阵

状态“有实现”均仍需完整现场验收；“部分/候选”只描述明确分支问题。文件路径相对于上述精确快照/仓库。

| ID | 生产入口 | 关键实际实现 | 自动化测试路径（代表证据） | 核查结论 / 尚待门禁 |
|---|---|---|---|---|
| T-1 姓名/别名/手机号、歧义、本人互转 | Banking转账表单；助手G3；R1/R2/W1 | `utterance/parser.py` 精确/近名/phone目标与澄清；`utterance/phone.py`；`taint/text.py`窄span；`banking.py:apply_transfer`区分P2P与SELF | `tests/gateway/w1/test_parser.py`、`test_phone_transfer.py`；`tests/gateway/w2/test_phone_source.py`；`tests/bankcore/test_banking.py` | 有实现。手机号/原话根是本人实现候选，独审与最终CI另核；seed payee.phone均null，新测试明确是合成已登记phone，不冒称现有联系人现场可用。 |
| T-2 新增收款人 | Banking新增收款人→PAYEE_ADD FORM；助手新增/开户行/账号语法 | `banking.py:apply_add_payee`目录指纹、重复no-op、自有账户拒绝；计划内EntityRef | `tests/bankcore/test_banking.py:test_new_and_existing_payee_reference_resolves_actual_id`；`tests/gateway/w1/test_parser_coverage_a.py`；`tests/integration/test_state.py` | 有实现，非stub。App表单可录姓名/账号/银行；phone/alias当前传null，不能用预览推定已录这些联系人字段。正常/重复/错误归属现场待验收。 |
| T-3 ONCE/WEEKLY/MONTHLY、列表、取消 | Banking定时页三周期/取消；助手具体周/月锚点；R7/W3/W4；S1到期 | `standing.py:apply_schedule_transfer/apply_cancel_scheduled`、`events.py:apply_scheduled_transfer`、L3 reads:list_scheduled/scheduler | `tests/bankcore/test_standing.py` ONCE/WEEKLY/MONTHLY/取消/月底；`tests/obligations_data/test_reads.py` R7；`test_standing_exec.py`；`test_transaction_fact_reload.py` | 主路径有实现；F4 fresh累计P1已有候选，OTHER scope/version由独立owner核。日期含糊转FORM是既定保守行为，不把不支持任意自然语句记成没有ONCE。 |
| T-4 AA分摊、请求、进度 | Banking AA页→SPLIT_CREATE；R8；D4模拟付款 | `split.py:allocate/apply_create_split_request`整数余数；L3 split表/读取；D4共享事件引擎与原子PAID/账本 | `tests/bankcore/test_split.py`；`tests/obligations_data/test_split_exec.py`；`test_demo_event_flow.py`；新`tests/gateway/w2/test_split_read_ownership.py` | **部分已实现**：列表/分摊/付款进度有代码，指定ID的G2旧guard是F1，候选待门禁。D4已独审，不再重复列为stub。 |
| T-5 转前预演余额/累计/限额占用 | G4/G9统一面板；Approval.tsx | `pipeline/preflight.py`累计apply_plan/余额/日用量；`approval/planview.py`固定语义hash | `tests/gateway/w3a/test_preflight.py`；`w3a/db/test_real_preflight.py`；`w4b1/test_planview.py`；`frontend/src/mobile/Approval.test.tsx` | 实际预演与绑定有实现；F5中正常面板的累计/限额占用展示尚由协调者补齐。 |
| B-1 时间/类别/商户流水 | Bills页/助手；R3 get_transactions | `reads.py`整范围过滤、cursor、filtered_all收入支出；G2所有权与untrusted包装 | `tests/obligations_data/test_reads.py`；`tests/agent/test_bill_accuracy.py`；`tests/agent/gateway/test_client_reads.py` | 有实现。新商户事件metadata缺失会影响merchant筛选，纳入F2候选，不把种子筛选通过等同新事件正确。 |
| B-2 月类别/占比/环比 | Bills页；R4 get_spending_summary | `analytics.py:spending_summary`业务月、分类、share_bp、环比与transfer_out | `tests/bankcore/test_analytics.py`；`test_analytics_live_kinds.py`；`tests/obligations_data/test_reads.py` R4 | **部分已实现**：聚合算法存在，基线canonical新增交易漏计已实证F2；修复候选待门禁。 |
| B-3 异常识别 | Bills异常/助手；R5 | `analytics.py:detect_anomalies`四规则、证据、此前90天、卡/商户去重 | `tests/bankcore/test_analytics.py` ANOMALY边界；`tests/agent/test_bill_accuracy.py` | 有实际确定性规则。D4真实CARD_SPEND分类修复4011属F2后续，未知商户不会按自由名称猜类别；现场异常全矩阵待验收。 |
| B-4 月度报告 | BillReport/App；助手完整typed月报 | `llm/gather.py/respond.py`业务时钟/完整聚合/范围异常/固定报告；`BillReport.tsx`组合读取 | `tests/agent/test_bill_accuracy.py`；`tests/try_agent/test_bill_readonly.py`；`frontend/src/mobile/BillReport.test.tsx` | 生产月报实现存在，过去误报失败模板已修；但报告依赖R4，F2真实新交易漏计须随候选关闭。未新跑现场。 |
| C-1 卡状态、限额、当日已用 | Cards页；R6 get_cards | `banking.py:card_view`；L3 `state.py`累计重载；current/replacement映射 | `tests/bankcore/test_banking.py`；`tests/agent/test_card_map.py`；`tests/obligations_data/test_transaction_fact_reload.py` | 查询/投影有实现；F4历史定时外转漏当日用量影响此字段，已有候选待PG/独审。 |
| C-2 调低限额黄 | Cards限额FORM；助手；W6 | `banking.py:apply_card`新旧值比较、TIGHTEN；policy registry动态ActionKind | `tests/bankcore/test_banking.py:test_cards_tightening_loosening_loss_and_replacement_preserve_counters`；`tests/gateway/w3a/test_workflow.py` | 有实际写/黄级规则。正常与混合编辑现场待验收，不能仅据现有组件记录授绿。 |
| C-3 调高限额红 | Cards“申请提额”；W6+确认/OTP | 同一apply_card的LOOSEN、gateway红级/风险确认/OTP | 同上；`tests/gateway/w4b2b/db/test_confirm.py`；`tests/gateway/w4c/db/test_form_execution.py` | 有实现；冻结时提额拒绝。真实提额OTP及最终限额值仍需现场对账。 |
| C-4 四渠道开关 | Cards online/overseas/atm/quickpass四项；W7 | `apply_card`逐字段新旧比较，任一开启LOOSEN；D4卡扣费按当前卡/渠道控制 | `tests/gateway/w1/test_parser_coverage.py`；`tests/bankcore/test_banking.py`；`tests/test_demo_event_bridge.py` | 四项入口和规则均存在；不能用用途标签localStorage代替真实controls。四项开关/混合/生效现场矩阵待验收。 |
| C-5 冻结/解冻 | Cards FORM、助手；W8/W9 | `apply_card` FROZEN/ACTIVE及no-op；冻结/丢失限制P2P与刷卡，SELF不受限 | `tests/bankcore/test_banking.py`；`tests/integration/test_concurrency.py`；`w4c/db/test_form_execution.py` | 有实现，绿冻结/红解冻。执行失败/UNKNOWN回复已有独立回执修复，不能混作卡状态测试。 |
| C-6 不可逆挂失/可选补卡 | Cards挂失/补卡选项；助手；W10 | `apply_card` LOST不可恢复、新ID/尾号派生、限额继承、默认渠道、current_card切换 | `tests/bankcore/test_banking.py`；`tests/gateway/w2/db/test_lost_card_default.py`；`tests/agent/test_lost_card_sources.py` | 有实现，补卡不是Preview开户。replace=true完整OTP/SQL现场待验收。 |
| S-1 周期扣费识别 | Subscriptions页；R9 | `analytics.detect_subscriptions` ≥3、7/28–31天、5%金额边界 | `tests/bankcore/test_analytics.py` R9；`test_event_transaction_facts.py`真实三期producer | 基线新MERCHANT_CHARGE和metadata问题F2，已交候选；不能只靠旧MANDATE合成样本称实际新扣费全过。 |
| S-2 列表/下次/年化 | Subscriptions页；R10 | L3读取实际SubscriptionView、MONTHLY×12/WEEKLY×52、逐项月均floor再求和 | `tests/obligations_data/test_reads.py:test_r10_annualization_weekly_monthly`及分页/状态/客户负控 | 有实现、列表折算本身正确。F3是取消结果口径而非列表没实现；本轮现场列表未复验。 |
| S-3 未来扣费/主动提醒 | Subscriptions未来7天列表；R11；G12/S1/REMINDER | `reserve.upcoming_window`、reads:upcoming_charges；新reminders/scheduler每日09:00扫描与稳定身份 | `tests/obligations_data/test_reads.py` R11；`test_reminders.py`；`tests/gateway/w5/test_event_notifications.py` | 查询有实现。通知helper已独审a576，runtime ed87已独立APPROVE，123相关纯测+8自写负控通过，协调者报9真实PG通过；最终集成CI/现场另核，不重复报未实现。 |
| S-4 取消、后续拒扣并通知 | Subscriptions取消FORM/助手；W11；商户每期继续发起 | `standing.apply_cancel_subscription`仅删授权、保留账单；events消费本期/推进cursor；S1拒扣/固定通知 | `tests/bankcore/test_standing.py`；`tests/obligations_data/test_events_api.py`；`test_period_savings.py`；新通知测试 | 基本取消/拒扣存在，F3周费月均误报已有候选；CHARGE_BLOCKED通知接线由A门禁，不再重复列为未实现。 |
| S-5 闲置识别/建议 | R9+助手；L2 idle_subscriptions谓词 | `analytics.detect_subscriptions` usage_expected、60/120天usage；L2重算精确目标集合 | `tests/bankcore/test_analytics.py`闲置边界；`tests/agent/test_idle_subscription_plan.py`；`tests/gateway/w2/test_derived_cancelled.py` | 规则存在；F2新扣费不能误充usage，候选覆盖。建议不是写许可，取消集合仍由L2校验。 |
| W-1 六题/等级/有效期/App专属 | Wealth问卷→FORM ASSESSMENT_SUBMIT；R12/R13/W12 | 版本化risk_questionnaire、risk计分/日历12个月、apply_risk_assessment、G9权威文案/hash | `tests/obligations_data/test_risk_exec.py/test_risk_profile.py`；`tests/gateway/w4b1/test_planview.py`；新增`w3b/db/test_assessment_panel.py` | 问卷入口旧503已修候选并集成；协调者发现并修正PG夹具locks未配置，不能把该fixture故障当第二个产品503。本人实现，独审/最终实际提交门禁另核。 |
| W-2 产品列表/详情/比较 | Wealth列表/详情/比较；R14/R15/R17 | L3产品过滤/分页/2–4项比较/全文disclosure；前端MMF/EQUITY类型映射已修 | `tests/obligations_data/test_product_reads.py`；`frontend/src/mobile/WealthActions.test.tsx` | 有实现，原类型标签问题不再重复报。多产品字段与权限现场仍待核验。 |
| W-3 红申购、适当性、揭示、预留、两阶段 | Wealth申购FORM/助手；W13/G9/OTP；S1 FUND_CONFIRM | `funds.apply_subscribe/apply_fund_confirm` MMF即时/非货币PENDING冻结、当日NAV/份额；L2 suitability、risk_ack/disclosure绑定 | `tests/bankcore/test_funds.py`；`tests/obligations_data/test_funds_exec.py/test_fund_events.py`；`tests/gateway/w4b2b/db/test_confirm.py` | 实际申购/适当性/冻结确认均有实现；F6七日预留跳过P1、F5日期预演明细须补。不能用历史单笔申购成功关闭全部W3。 |
| W-4 明确份额赎回/在途不可用 | Wealth持仓赎回/助手；W14；S1 FUND_SETTLE | `funds.apply_redeem/apply_fund_settle` Decimal份额、预留units/cost、结算前无可用现金、重复卖出拒绝 | `tests/bankcore/test_funds.py:test_redeem_reserves_and_settles/test_redeem_cannot_double_sell`；`test_funds_exec.py/test_fund_events.py` | 有实现。F5到账日预演呈现待补；真实MMF/非货币L1与FORM历史证据不互相冒充。 |
| W-5 持仓/可用预留份额/订单 | Wealth holdings/orders；R16 | `funds.holdings_view/order_view`与L3 get_holdings | `tests/bankcore/test_funds.py:test_holdings_view_and_redeem_flow`；`tests/obligations_data/test_funds_exec.py:test_get_holdings_returns_holdings_and_orders` | 有实现，不用Preview持仓当真实申购结果。逐订单状态现场待验收。 |
| X-1 月消费/异常/卡→降限额 | 单G3/ActionPlan/G4，统一黄确认 | parser/真实汇总异常+card修改+apply_plan整份事务；L1必要gather由独立owner补 | `tests/gateway/w1/test_parser.py`；`tests/agent/test_subs_bills_prompt.py`；`tests/bankcore/test_banking.py`原子回滚 | 工具/多步架构存在，必要gather已分配修复，不重复报未实现。F2聚合事实随候选关闭，整组合现场待验收。 |
| X-2 闲置取消→派生月费定时本人储蓄 | 单G3/计划；R9/R10/W11/W3 | L2权威谓词+cancelled_monthly_fees，仅MONTHLY、顺序/同账户/本人储蓄；当前原话根精确装载 | `tests/gateway/w2/test_derived_cancelled.py`；`w3b/db/test_current_authorization.py`；`tests/agent/test_x2_sources.py` | 连续NO_WRITE→SCOPED旧根问题已有候选且协调者两X2真实G4 PG通过；F5每月节省面板明示待补。无周费自动派生扩权。 |
| X-3 留足7天→稳健产品澄清→派生申购 | G3澄清/G21；R1/R11/R13/R14/R17/W13 | robust_products适当性集合、available_less_7d_charges、用户选择产品、红级揭示；gather由owner补 | `tests/gateway/w2/db/test_reserve_integration.py`；`tests/gateway/w1/test_predicates.py`；`tests/agent/test_funds_prompt.py` | 派生模板/澄清/申购实现存在；F6普通申购shortfall绕过不因派生正控而消失。必要gather在修，不重复分配；整链现场待验收。 |
| X-4 可疑卡冻结→储蓄转活期 | 单G3/ActionPlan/G4，绿+黄整份一次确认 | L2自行异常求卡集合/澄清、freeze+SELF_TRANSFER原子应用；gather由owner补 | `tests/gateway/w1/test_parser_coverage_c.py`；`tests/bankcore/test_banking.py`；`tests/agent/test_subs_bills_prompt.py` | 规则/计划执行存在，必要reads补齐已分配；不能拿单独挂失/冻结证据当整个X4通过。现场待验收。 |

## 明确保留的范围与门禁

- 仍存在的 D2 scene/prepare、攻击D6/D8/指标/控制台附属stub不自动等于29项基础银行业务缺失。D1已有受控reset命令约定，D4本轮已有生产桥接候选；Preview PAN/开户、新卡申请、用途本地标签不扩成当前基础开发任务。具体残留stub位于 `services/gateway/app/api/demo.py`，应按既定比赛/演示安排另核。
- 本次确认的问题是代码/测试驱动的分支缺陷；没有把“本轮未跑现场”写成失败，也没有因为旧CI、测试文件存在、Preview或LLM说成功就授绿。
- 新候选的提交、独审、真实PG及最终精确head完整CI由协调者串行核对。基础发布与demo-v1冻结仍需要两独立reset六场景、完整清单、真实状态/审批/OTP/SQL对账及规定录制/断网回放；本报告不替代这些验收。

## 收口补记

- REMINDER runtime 独审报告：`/private/tmp/sbg-demo-repairs-20261031/review-reminder-runtime.md`，精确ed87为APPROVE；本人仅纯测，真实PG证据由协调者提供。
- F1–F6、OTHER候选均已交指定owner；除上文明确列出的PG/独审回执，不将正在实现的候选提前标绿。本矩阵29行核查完成，不代表六场景现场验收完成。

- 后续门禁补记：F6由root6ef2463补普通申购/新增定时同一七日比较，本人独审APPROVE（44纯测+8自写负控），协调者真实reserve+时钟23pass；PG文件重命名防collect冲突须随集成提交。OTHER由350e/root8d0ac42补双客户scope/排序版本锁，本人独审APPROVE（526相关纯测+8自写负控），协调者真实6pass/3.77s。报告分别为review-reserve-funds-schedule.md、review-scheduled-peer.md；F5展示cap/end_at仍由指定owner收口，不提前标绿。
