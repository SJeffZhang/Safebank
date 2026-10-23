# 后续共享后端组件事实地图（只读准备）

日期：2026-10-23。核对工作树：`/Users/jet/Downloads/WeBank`，只读记录的 HEAD：`6ad3743cd056dde32c4bce100265d02739ae602c`。本报告没有实现、修改仓库、运行测试、运行 Docker 或派发 agent。报告文件是本次唯一写入。协调者负责把准备结果写入 AGENTS；这里不越权修改其日志。

## 结论与优先顺序

当前确有 **13 个读工具、7 个写工具未实现**，且底层领域实现也缺失。已实际读过 `banking.py` 的全部分发/结果生成、`models.py` 全表、`state.py` 全 loader/persist、`reads.py` 全分支及 `main.py` 路由；结论并非从 `SUPPORTED_*` 推断。

S3a 原始义务存储/loader 按已经 APPROVE 的任务书与 ADR-009执行，不重审、不扩大。建议其后最多六个**职责组**如下；一个职责组并不意味着允许把全部生命周期堆入一个大 PR。尤其基金应按下列读、下单、事件门禁分别验收，未通过的部分不能一起声称完成。

| 建议组 | 对应未实现读 | 对应未实现写 | 最早价值/先决条件 |
|---|---|---|---|
| C1 / S3b 义务查询与窗口投影 | R7、R10、R11（3） | 无 | 紧接 S3a；首先给 W2 派生值、W3 真实预留提供生产读 |
| C2 确定性流水分析 | R4、R5、R9（3） | 无 | R4/R5可先用P1流水；R9须S3a商户与授权；解除异常卡、闲置订阅谓词缺口 |
| C3 定时创建/取消与代扣撤销 | 无（复用 C1） | W3、W4、W11（3） | S3a+C1；需要事务内 job 存储，但不实现 scheduler |
| C4 AA 请求与进度 | R8（1） | W5（1） | P1账户/payee足够；外部付款推进另有边界 |
| C5 风险问卷与测评 | R12、R13（2） | W12（1） | 可在S3a后尽早完成，不依赖定时/AA；为基金与稳健产品谓词供事实 |
| C6 产品、持仓与基金订单 | R14、R15、R16、R17（4） | W13、W14（2） | 产品只读门禁应早于完整基金写；写依赖C5和事件持久化 |
| 合计 | **13** | **7** | 覆盖所有余项 |

实际发布顺序建议：**S3a → C1 → C2 → C5 → C6产品读取门禁 → C3 → C4 → C6基金写/事件门禁**。这让 W2/W3 和 W1 的确定性谓词尽早接真数据；不会让它们等待 AA 或完整基金调度。若严格要求每个 PR 都是单一小组件，C6三道门禁应各有任务书/PR，不能为满足“六组”而合成大 PR。

## 已有实现可复用，不能重复造轮子

路径均相对仓库根；行号为本次读取时位置。

| 已有事实 | 证据 | 后续可复用与边界 |
|---|---|---|
| 18读/14写输入与结果全部有契约 | `packages/contracts/src/safebank_contracts/tools.py:154,258`；`views.py:311,386` | 保留 §9.3/§9.4 外部输入输出；有契约不表示业务已实现 |
| 账本纯函数支持POSTED/PENDING/POST_PENDING/VOID_PENDING、最终处理唯一与确定性ID | `bankcore/ledger.py:30,140`；`ids.py` | 供基金、外部付款复用。不能直接把 `apply_transfer` 用作基金申购：后者有独立适当性/份额/订单规则 |
| 转账、add_payee、五类卡写已经实现 | `bankcore/banking.py:55,151,196,385–443` | `apply_plan`逐步回滚、`Context`、operation、StepResult复用；新增工具需扩充分发、result和public effects，不能只改白名单 |
| EntityRef准备摘要已解析新/已有payee | `bankcore/references.py:13` | 对schedule同样适用；但`apply_plan:406`运行时只为transfer替换EntityRef，C3需补schedule执行分支 |
| 唯一loader只加载账户/客户/卡/payee/账本/流水/操作历史 | `bank-backend/app/state.py:85–201` | 指令、订阅、产品、持仓、订单现为BankState默认空；没有AA/assessment集合。S3a负责义务原始实体，不负责其余领域 |
| persist只实现五类效果，未知效果明确报错 | `bank-backend/app/state.py:204–278` | PostTransfer、AppendTransaction、UpsertCard、InsertPayee、OperationEvent/outbox可复用；未来效果目前都不能落库 |
| 未来effect只是类型预留 | `contracts/state.py:238–290` | InsertAssessment缺customer/answers/plan；InsertFundOrder的FundOrderView缺customer、reserved_cost、pending_transfer、plan/step、expires；InsertInstruction仅ScheduledView；不能把展示模型当完整持久化命令 |
| 计划原子执行、幂等/版本/客户锁已有唯一入口 | `bank-backend/app/execution.py:28,155–260` | 后续领域写共用intent→clock→客户版本锁→load→apply→persist→版本+1→结果/outbox；不新增私有绕过写路由 |
| 5读真实实现 | `bank-backend/app/reads.py:49–202` | get_accounts/list_payees/get_transactions/get_cards/get_profile。get_transactions已有merchant_id、CARD_SPEND等字段但merchant_name恒null，需C2接真商户名称 |
| 预留算法已合入 | `bankcore/reserve.py:103,209` | scheduled_occurrences支持任意确定性窗口；projected_shortfall当前仅days=7且仍消费View，S3a会按ADR009转原始义务。S3b不能复制一套金额算法 |
| 当前银行表只到P1核心 | `bank-backend/app/models.py:31–275`；`migrations/schema_v1.json`、`0001_core.py`、`0002_identity.py` | 没有merchant/billing/instruction/run、AA、products/holdings/orders、assessments或event_jobs/executions表。S3a四表已批准，后续只能追加迁移 |
| 当前仅P1种子 | `bank-backend/app/seed.py:1,17` | cus_a/b，无有效测评/商品/商户/领域历史；不能从现有空读结果证明完整PRD场景 |

`get_profile`另有真实缺陷：`reads.py:191`用`date.fromisoformat`但只导入`timedelta`；有效risk_valid_until会触发NameError。C5应精确修复并覆盖有效/过期/空值，不必等基金。

## C1 / S3b：义务查询与1–30天投影（纯读）

**PRD**：§5.1 T-3、§5.4 S-2/S-3、§7.7、§8.2、§9.3 R7/R10/R11。**输入**：S3a完整原始义务、真实账户可用额、客户/目标所有权、请求status/cursor/limit/days/account_id；**输出**：ScheduledView/SubscriptionView/UpcomingResult，金额/目标文本与原始快照一致。

**最小职责**：R7仅SCHEDULED_TRANSFER且last_run来自真实runs；R10仅该客户MERCHANT_MANDATE，合计仅ACTIVE，逐项MONTHLY/WEEKLY折算；R11只计有效授权的真实bill金额，不用max，定时本人互转按同源前缀算法。任意无权账户404，不返回对方余额或联系方式。真实空集合可以返回空；未完整加载必须失败。

**缺文件/变更点**：新增确定性义务展示/窗口投影模块（可在bankcore现有reserve旁扩展，沿用现有公开7天签名）；L3 reads注册与投影；复用S3a state loader。无需新增业务表、apply写、persist分支。S3a任务已经把R7/R10/R11留给此组，勿再把读偷塞回S3a。

**schema**：使用S3a四表，不再新增重复表。唯一已明确的待决是R11负max_outflow的窄signed字段（见末节）；1–30天窗口须从原始anchor/weekday/time展开，不能调用只支持7天的现有函数后返回任意30天结果。

**最小验收矩阵**：正常ACTIVE与DELETED过滤、跨页合计、weekly月/年折算、last_run、days=1/7/30；异常跨客户ID/无效cursor/缺加载或非法周期失败；边界左开右闭/06:00/月末恢复原anchor/闰年/等价offset；一致性R11七天与W3/hint/derived同数、300000分已转后max=295500；读取期间并发取消/创建时snapshot内部一致、读事务不写。该组不写领域状态，无需伪造“写并发已验收”。

**所有权/串行原因**：S3a合入后由单个共享后端owner负责reserve、reads与受控字段快照；与C2/C3共改reads/state/contracts freeze，不与它们同改同一共享面。W2/W3只消费生产接口。

## C2：流水汇总、异常与订阅识别（纯读）

**PRD**：§5.2、§5.4 S-1/S-5、§5.6异常卡/闲置谓词、§8.2异常、§9.3 R4/R5/R9。**输入**：客户真实流水全历史（loader已保留，查询响应仍≤180天）、商户mcc/category/usage_expected、原始bill/mandate、month或range/lookback；**输出**：SpendingResult、AnomaliesResult、DetectedSubscriptionsResult，含真实证据与可关联实体ID。

**缺文件/变更点**：`bankcore.analytics`尚不存在，应实现纯消费分类/汇总、anomalies、detect_recurring、normalize_period；L3 reads新分支并修merchant_name映射；唯一loader向纯函数提供最小merchant资料（S3a数据可复用，不能强读默认空）。不写余额/账户，不生成OperationEvent，不建“AI分析结果”事实表。

**最小schema**：S3a merchants和既有transactions已足够承载PRD规则；流水已有kind/merchant_id/card_id/category/channel。无须新usage表：近60与60–120天“非扣费类交易”直接按既有流水确定性计数。完整合成历史属于后续baseline安装，不给现存P1库凭空添加历史。

**纯分析细节**：消费仅CARD_SPEND/MANDATE并只计已过账；P2P单列，PENDING/RELEASED不能算消费；R5只CARD_SPEND，只用发生前历史，合并同交易的多个信号；R9先交易周期识别后关联本人授权，不能从“已有mandate”反推检测一定命中。典型金额/波动基准等未定处见末节，不能擅定成接口外新规则。

**验收矩阵**：无流水/上月0→bp=null、8类别/当前月环比/跨账户聚合；4异常每个阈值下/等于/大于、此前90天不足10笔、重复第2笔起、无未来信息泄露、同交易多码；周期≥3与≤5%边界、7/28/29/30/31日、非周期商户、usage_expected false、近60与前60日边界、已取消授权仍有交易候选但不能取消虚构instruction；跨客户ID与只读事务；提交新交易时读取快照不混版本。

**所有权/串行原因**：纯analytics可先写独立任务书，但生产接线需S3a/C1，仍由共享owner串行合入reads/state。完成后W1按契约真跑谓词、W2数字来源、W3风险检查共用这些纯函数；不得在gateway另写分析算法。

## C3：定时创建/取消与撤销代扣（领域写）

**PRD**：§5.1 T-3、§5.4 S-4、§7.7/7.8/7.11、§8.2/8.4、§9.4 W3/W4/W11。**输入**：冻结的Schedule/Instruction、客户与plan/step上下文、S3a原始义务快照；**输出**：新影子义务、InsertInstruction/SetInstructionStatus/ScheduleEventJob/OperationEvent、W3/W4/W11既有result与public effects。

**缺文件/逻辑**：`bankcore.standing`及三个apply不存在。需初次未来日期、MONTHLY原锚点、WEEKLY星期/时间、ONCE、结束条件、目标所有权、仅本人指定kind可取消、首次到期预演、不冻结当前余额、commitment_30d/grant_scope_hash确定性计算；复用scope_for对schedule已有目标客户解析，补apply_plan的schedule EntityRef运行时替换。撤销代扣只删授权状态，保留bill及未来商户请求来源。重复取消no_op不能重复节省金额/事件。

**loader/persist**：S3a已提供raw instruction/billing/run，不再重建；需将未来预留effect改为完整typed raw命令并给persist实现insert/status/job，扩展step_result/public effects。OperationEvent+outbox继续已有唯一写入。L2 scheduled_grants属于W5/gateway，不由本组直接跨库写；创建/撤销输出足够已提交事实供其消费。

**最小新增schema**：S3a四表之外，若本组成为首个事件生产者，必须先把`event_jobs`最小持久结构与ScheduleEventJob落地（PRD8.3：id/kind/rank/due_at/immutable_payload/payload_hash/status/lease_until_wall/attempt_count，来源周期唯一），明确由本owner维护，供基金复用。不能只insert standing_instruction而漏首次job。**这里不实现scheduler、时钟advance或S回调**；event_executions与事件执行入口另有后置门禁，不能拿它冒充本组已交付。

**验收矩阵**：ONCE/WEEKLY/月末/闰年/到期结束；本人/本行他人/外部目标及新增/复用payee引用；当前/过去首次、无权/错kind、同源目标、坏周期拒绝；ACTIVE→DELETED、重复no_op、bill保留；同一plan20重试仅一instruction/job/operation，取消竞争只有合法结果；取消+定时组合第二步失败整份回滚；snapshot预演effects=真实PG重载，包括义务预留与30天承诺量；并发版本冲突/clock改变不得旧快照提交。创建不等于到期扣款已验证。

**所有权/不能并行**：models/state/persist/dispatch/freeze/new migration为共享面，与C4/C5/C6串行；event_jobs schema只指定一个owner，不让基金另建同名表。W3/L2决定风险与授权，L3仍执行硬约束，不把L2策略塞入apply。

## C4：AA请求与进度（先不动资金）

**PRD**：§5.1 T-4、§7.7.1、§8.3、§9.3 R8、§9.4 W5。**输入**：CreateSplit、本人账户/payees、确定性plan/step；**输出**：SplitView、InsertSplit及OperationEvent，分摊和等于total，首次均UNPAID。

**缺文件/逻辑**：`bankcore.split`尚不存在，应提供allocate/apply_create_split_request和查询投影；整数余数给本人、不含本人则给首位参与人；人数含本人时计入self_share。1–20个不同且属于当前客户的payee、每项≥1分。不得用Participant金额直接发起真实转账。

**最小schema/loader/persist**：新增split_requests与split_items（PRD8.3全部列、账户与客户所有权、item payee归属、参与人唯一、正数、状态/paid字段组合约束）；唯一loader增加可计算raw split集合；InsertSplit必须携customer/request/item归属而不只公共SplitView；persist插request/items，同事务保存操作/结果。R8无request_id时稳定历史分页，有ID时仅本人一项，received/outstanding从items推导。不要为读取动态汇总再建冗余总额表。

**外部付款界限**：`apply_split_payment`和payment_event_id防重是PRD已要求的后置领域事件，并不属于W5“创建请求”。可先独立验收请求/状态；若要验PAID，必须通过正式事件域入口并由同一账本函数外部入账，不加私有“把item设PAID”路由。事件推进与DEMO API暂不纳入本任务；完整AA进度的已收款路径仍待该依赖，清楚记录。

**验收矩阵**：10000/3→3334+3333+3333、含/不含本人、1分/20人边界、重复/他客户payee/非本人账户拒绝；同plan重试只一request/items；并发创建各自独立；第二步失败不留request；真实PG→R8/HTTP→SplitView往返、无付款时余额和账本零变化；后置付款的重复/竞争与失败回滚需另门禁，不把未运行当通过。

**所有权/串行原因**：独立split.py纯规则文件可隔离，但contracts/state、models、persist、reads、apply_plan均重合，发布仍串行。

## C5：风险问卷与测评（独立于基金）

**PRD**：§5.5 W-1、§8.2 rq_v1、§8.4、§9.3 R12/R13、§9.4 W12。**输入**：固定rq_v1六题option_id，customer/plan/step/sim_now；**输出**：QuestionnaireResult、RiskProfile、AssessmentResult，append-only测评事实与customers派生摘要一致。

**缺文件/逻辑**：`contracts/risk_questionnaire.py`尚不存在；新增版本化静态题文/选项与纯评分apply，六个明确question_id各一次，拒绝假题ID/重复/缺题/score或level额外输入。固定6–24分和R1..R5边界；有效期12个日历月、闰日取月末。get_risk_profile从真实assessment事实取ID/日期；R18继续同源，修date import。

**schema/loader/persist最小增量**：新增risk_assessments（id/customer_id/questionnaire_id/answers/score/level/assessed_at/valid_until/plan_id，字段按PRD8.3；append-only触发器；客户+日期/ID稳定检索），复用customers已有risk_level/risk_assessed_at/risk_valid_until（不必为了本组件重写旧列类型）。raw AssessmentState/InsertAssessment补customer、answers、plan与必要上下文；在同事务插事实并更新摘要，loader提供真实最新assessment而不是仅customers无ID摘要。

**授权归属**：仅app_form的origin校验由L2执行；L3不能靠模型答案自报等级。前端暂停时用F服务令牌+app JWT走既有gateway测试，不能因此开发问卷页/BFF。只读Rq内容是展示模板，不赋写授权。

**验收矩阵**：6/9/10/13/14/17/18/21/22/24各档边界，非法question_id/假score/缺题；未测评、有效、恰好到期、过期、2月29日；重复plan不会追加两条，同客户并发两份测评最终事实/摘要一致、版本冲突正常，SQL故障两者全回滚；snapshot→apply→execute→重载比对answers/score/level/有效期；L2 agent origin拒绝与app_form允许属于联调门禁。不得因R18没异常就认定R13真实实现。

## C6：产品事实、持仓与基金（需要分门禁）

**PRD**：§5.5 W-2..W-5、§5.6稳健产品、§7.7、§8.2 Decimal/日历/净值/申赎、§8.4、§9.3 R14/R15/R16/R17、§9.4 W13/W14。

**读取门禁**：输入raw Product及sim_now、C5真实测评和客户持仓/订单；输出4个既有read结果。先新增纯`bankcore.calendar/nav`或同等小模块与产品投影；products表全字段按PRD8.3，risk_label不能从产品名称猜。净值30交易日与合成规则必须同源；list不默认适当性过滤；compare保持指定2–4个唯一产品；R16真实订单/持仓须等相应表和loader完成，不能用默认空冒充持仓已加载。产品读取可先独立发布，让W1稳健谓词与W2接线；R16不可随前三读一起假标完成。

**下单门禁**：输入冻结Subscribe/Redeem、客户完整产品/assessment/holding/order、CHECKING账户；输出原result、内部订单/持仓/effects及job。实现apply_risk不重复，复用C5。新增`bankcore.funds`的apply_subscribe/apply_redeem、日历交易日/15:00规则、floor4/floor_cents/成本ROUND_HALF_UP。MMF即时过账/到账；非MMF订阅PENDING全额占款，赎回先reserved_units+reserved_cost并固定应收；未来应收不当现金。

**schema最小增量**：products、holdings、fund_orders（PRD8.3全部字段，numeric(18,4)，units≥reserved≥0、cost≥reserved_cost≥0、customer/account/product所有权复合关联、订单状态/字段组合），复用C3或独立先决门禁统一创建的event_jobs；每产品INTERNAL结算账户。不要另加一个余额账本、缓存NAV事实源或用float。迁移只建schema；产品/结算账户初始化作为显式合成数据安装，不能改原P1历史或冒充baseline。

**loader/effect/persist真缺口**：当前ProductView没有base_nav/base_date，FundOrderView没有客户/plan/step、expires_at、reserved_cost/pending_transfer，HoldingView不含明确owner；需最小typed raw内部实体与完整Insert/Update效果，不能将这些值塞任意JSON逃过contract核验。loader只提供请求客户的持仓/订单，产品为公共合成定义；persist插/更订单及持仓、ScheduleEventJob、复用账本/交易/operation/outbox，原子更新客户版本。

**领域事件门禁（不含scheduler）**：apply_fund_confirm/apply_fund_settle/apply_pending_expiry当前均缺。可先独立验证纯函数及PG持久一致性，但完整非MMF W13/W14的完成还需要正式事件execute/block入口、event_executions终态与job入口锁/clock→客户锁（PRD8.4）。确认/到期竞争必须只能一终态；成功确认后到期事件不能再释放或伪记第二次成功。**不实现时钟advance、扫描worker、lease循环、S回调或App/BFF**。若后置入口未交付，只能标“下单/纯领域已验收，完整生命周期待事件组件”，不能宣称7写全部完整生产可用。

**验收矩阵**：无/过期测评与超风险、起购/最小份额、非CHECKING/他人账户拒绝；周五15:00前/恰好15:00/周末、T+n与09:00；MMF即时、非MMF冻结/确认/超时释放、重复confirm、confirm/expiry竞争；多次部分赎回+全部可用赎回成本守恒、超卖/并发申赎/出账不透支；同plan20重试一订单/job、第二步失败全回滚、同pending唯一处理、内部结算全库平衡、预演与真实effects/重载所有订单持仓一致；R16市场价值只可用份额、应收单列、PENDING与30天终态窗口、不泄露他客户。

**所有权/串行原因**：基金raw contracts/schema与共享plan dispatch/persist变动最大，C5必须先固定真实测评，C3或另一个明确owner必须先固定event_jobs公共存储；不能与其同时竞争改同一迁移head/persist。为抢时间并行造两套calendar/事件表会制造一致性风险。

## 执行时的验收命令与发布门禁（本次均未执行）

下列是后续任务书应要求的命令，**新增名字仅为建议，当前Makefile不存在，不能引用成已经有的测试**。

1. 每组先固定自己的文件路径，例如 `tests/bankcore/test_analytics.py`、`tests/integration/<component>/`；纯函数用 `.venv/bin/pytest tests/bankcore/<真实文件> -q`。C1须附 `make test-reserve`，C2读也需真实PG/HTTP证明来源而不只FakeL3。
2. 为各新增组提供一个独立真实PG专项target（建议`make test-l3-<component>`），使用显式tmpfs PG、独立DB与合成fresh fixture；必须零skip。不存在target前不得宣称命令可执行。schema组须覆盖空库upgrade head两次、旧0002或上组head带余额/交易/intent/outbox/版本升级保留、运行角色DDL/TRUNCATE禁止、历史DDL文件字节不变；不可删重建任何长期卷。
3. 普通门禁：`make lint`、`make test`、`make contracts-check`（仅受控内部快照增量，公共18/14入参和结果保持冻结；负max待决须先文档明确授权）。纯读查询还要证明REPEATABLE READ READ ONLY和并发一致性。
4. bankcore/L3写变更：协调者串行亲跑 `make test-p1`、`make test-p1-concurrency`、`make test-p1-consistency`、`make test-integration`，并加该组新的真实并发/一致性矩阵。现有P1并发仅5项转账/卡/pending，现有17项一致性只覆盖P1，不能把旧绿结果当新工具被测。不得并行控制compose；保持后台CLI，不开Desktop。
5. 每组专项红→绿、独立只读评审、owner修复、协调者复跑→独立base main PR→当前精确head全部CI真实成功→merge commit。CI必须实际跑该组PG专项，默认无PG的skip不是通过。未完成事件推进、完整baseline与正式L2接线分别列待决。

## 真正矛盾/未定处与处理边界

这里不自行裁决新需求，不修改PRD。前两项已在S3a任务书明确后置；其余是这次逐项对照发现的实施前精确定义缺口。

1. **R11可流出上限的有符号范围**：PRD7.7公式与S2实现会在既有缺口下产生负`max_outflow_cents`，而`views.AccountReserve.max_outflow_cents`目前是非负Cents，不能如实序列化。S3a任务书已明确S3b处理窄signed字段。要么受控修这一字段并同步契约/PRD，要么明确其他语义；本报告不clamp、不放宽全部Cents。
2. **R11支持days=1..30，而现有projected_shortfall只允许7**：这属于明确实现差距，不是可以把公共范围缩到7的授权。S3a原始billing已解决锚点事实来源；S3b需同源窗口能力/内部helper，同时保留7天公开函数签名，不能复制一套算法或用展示next_due_at猜月末。
3. **R9 typical_amount与≤5%浮动基准尚未定**：PRD5.4只写“金额波动≤5%”，没有相对首笔/均值/中位数或max/min的定义，R9结果要返回typical_amount_cents。写任务书前请协调者固定确定性口径并同步PRD；否则边界测试会各说各话。本报告不替团队选统计量。
4. **recent_return_bp的比较窗口与舍入尚未定**：PRD8.3说由NAV序列计算、R17给30交易日历史，但未明确recent究竟首尾30点还是其他窗口、bp舍入规则。基金读门禁前应精确定义并同步PRD；不能把硬编码收益当事实。
5. **事件完成不是“调用apply_plan”自动获得**：PRD8.5/8.6要求独立到期job、event执行/终态、block推进；当前main无这组路由，snapshot遇event_id仍403，scope_for只接steps。此为真实依赖缺口而非设计冲突。C3/C6可补producer与纯事件，但不能把scheduler引入本轮组件，也不能隐瞒非即时业务完整交付仍依赖后续事件入口。
6. **未来effect已有名字而缺持久化事实**：这是实现不完整，非PRD矛盾。允许按共享补缺授权添加内部raw state/effects；外部工具不得借此改字段、默认放行或假成功。新迁移owner/顺序由协调者固定，不能让多个owner自行都叫0004。

所有任务都保持三明治边界、LLM不授权、整数分/Decimal、单一load/persist、既有外部接口、无App/BFF、无scheduler实现、无长期库reset/重建。完整PRD baseline种子安装仍单独后置，不是上述读组件的隐含附带任务。
