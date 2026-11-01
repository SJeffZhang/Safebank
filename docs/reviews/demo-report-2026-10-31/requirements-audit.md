# SafeBank 六场景 29 项需求静态核查

核查日期：2026-10-31。核查版本：`origin/main = 22a87799c2b422a6c2186490a5972e11bc05dce4`。

本报告只核对最新源码、PRD、版本化验收记录及已保存的 SQL 回执，没有启动服务、请求 API/LLM、执行数据库查询或进行业务现场验收，也不包含本轮浏览器/UI结果。根工作区当前 checkout `a113787` 落后，故通过 `git show origin/main:<path>` 读取最新文件；下列源码链接统一指向已核对为同一 `22a87799` 的工作树 `/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank`。未更改 AGENTS。

官方公开资源留档要求**至少覆盖 3 个核心场景，鼓励全部**；本项目承诺的是**全部 6 个场景、29 个需求 ID**。不能用官方最低数量豁免团队承诺，也不能把团队 29 项规格说成官方逐项细则。来源：[official-v1.md](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/official-v1.md:9)、[PRD §9.2](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:1314)。

状态口径：**已实现**指读到实际处理逻辑，不等于本轮验收通过；**历史实证**限于下表给出的精确候选、代际与路径；**待验收**表示缺少本轮完整现场证据，不判定失败；**确定缺口**以实际 stub 或缺失的生产链路为依据。历史组件实测不能自动替代最新 main 的整体验收。

> 本文件为并行静态核查基线，保留其历史证据边界。本轮浏览器实跑的新增结果（包括测评503、100元转账误报5000元、连续会话X2错误REPLAN，以及已结算赎回）请以[主验收报告](demo-acceptance.html)为准。

## 历史真实业务证据索引

| 编号 | 证据与适用边界 |
|---|---|
| E1 | [FORM 授权实测](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/form-authorization.md:47)：候选 `f6479e1`，代际 `demo_20261031014249_e694d3ac`，真实 F/app→L2→L3 黄级 20000 分转账和绿级冻结；重放不重复写；SQL 核永久绑定、claims、余额及账本。这是 F/app 路径，不是 L1 转账全分支或完整银行 App 现场验收。 |
| E2 | [定时转账实测](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/schedule-provenance.md:34)、[SQL 回执](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/schedule-provenance-live.json:1)：`5c3d0cf09047bb3b45610366422f68dcd4063956`，真实 L1 case06 创建赵六 20000 分 MONTHLY 指令；10/20、11/20 北京时间 08:00 各一次，共 40000 分，源计划/授权匹配，无重复、账本平衡。 |
| E3 | [AA 创建实测](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/aa-self-source.md:92)、[SQL 回执](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/aa-self-source-live.json:1)：`e884e0c94c1b5558b372f3caed3f20e0072b9b69`，新代际真实 L1 case09 ALLOW/EXECUTED，创建 40000 分 AA，本人 10000 分、三位参与人各 10000 分 UNPAID；银行资金/流水未变。没有证明参与人付款后的进度更新。 |
| E4 | [账单实测](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/bill-analysis-proof.md:110)、[SQL 回执](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/bill-analysis-live.json:1)：`9e611ccd407e790b4dbd9e434e3ec8b61043cdb2`，代际 `demo_20261031124122_5a2da072`，case16 COMPLETED；9 月 644800 分、8 月 133190 分、占比/环比和 3 条异常匹配；上海 `[9/1,10/1)`；NO_WRITE、无计划/审批、捕获 bank 状态前后相同。 |
| E5 | [挂失实测](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/lost-card-source.md:44)、[SQL 回执](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/lost-card-source-live.json:1)：候选 `a01611d`，真实 L1 case23 RED＋真实 OTP 确认，唯一卡 LOST，无补卡，1 次 CARD_LOST operation、零转账、账本平衡。 |
| E6 | [代扣/时钟实测](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/demo-clock.md:63)、[SQL 回执](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/demo-clock-live.json:1)：候选 `2d58518`，真实 L1 取消音乐并申购，D3 推进产生 16 个终态（14 SUCCESS、2 BLOCKED）；取消音乐两次拒扣，周期推进无重复，基金确认 1624.5633 份、预留清零、账本平衡。拒扣结果审计不等于客户拒扣通知。 |
| E7 | [闲置订阅＋储蓄联动实测](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/idle-subscription-savings.md:31)、[SQL 回执](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/idle-subscription-savings-live.json:1)：`d463d4f3351565a9b29abaa65ae3a32251824bff`，真实 L1 case36，只取消健身、其他三项 ACTIVE；派生 19900 分/月本人储蓄指令，两期共 39800 分，健身两次 BLOCKED，无重复、账本平衡。 |
| E8 | [赎回/申购结算实测](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/redemption-sources.md:81)、[SQL 回执](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/redemption-sources-live.json:1)：标准生产 `ccf5d3330b44f4d5049fbbdb5bf5d78ac352ba1b` 新代际真实 L1 case34，MMF 赎回 1000 份，持仓 10000→9000、现金 1200000→1300000 分、单次 SETTLED。另在 `bbeb765` 代际真实 F/app RED＋OTP 非货币申购 200000 分，经 D3 确认 1624.5633 份；再赎回 100 份，经 D3 到账 12311 分、预留清零。后一条不是 L1 非货币赎回实测。 |

## 29 项需求逐项核对

### 场景一：智能转账（T-1～T-5）

规格：[PRD §5.1](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:557)。

| ID / 必需能力 | 最新实现核查 | 历史实证 | 本轮未验收 / 缺口 |
|---|---|---|---|
| T-1 姓名/别名/手机号转账、歧义追问、本人互转 | [L3 收款人查询](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:152)支持姓名/别名/phone；[转账](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:114)有实际账本处理；[L2 目标匹配](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/utterance/parser.py:503)支持姓名/别名/近名。 | E1 为 F/app 正常转账；E2 为 L1 点名定时目标；E7 为定时本人互转。 | 当前 main 姓名/别名/手机号/同近名与即时本人互转全矩阵未现场验收。另有静态部分覆盖问题：L2 `_payees` 和转账目标 exact 比对只使用 name/alias（parser.py:503–514、780–787），未见 phone 匹配分支；因此 L3 能按手机号查询不等于已支持手机号 typed 原话授权。此为源码差距，未发现场请求判定具体返回。 |
| T-2 新增收款人 | [apply_add_payee](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:194)创建/复用及实体引用有真实逻辑。 | [组件测试](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/tests/bankcore/test_banking.py:133)覆盖新增后引用，不是生产 L1 live 证明。 | 新增→审批→权威列表刷新→实际使用的本轮完整链路未验收；没有实证不等于未实现。 |
| T-3 单次/每周/每月定时、列表、取消、到期执行 | [创建](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/standing.py:343)、[取消](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/standing.py:399)、[列表](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:262)、[扫描器](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/scheduler.py:74)均有实际逻辑。 | E2、E7 证明 MONTHLY 创建及两期真实执行。 | ONCE/WEEKLY、取消、余额不足不补扣等组合没有本轮完整现场清单；不把 MONTHLY 实证扩展到全部周期。 |
| T-4 AA 整数分摊、创建、列表/进度、参与人模拟付款 | [分摊/创建](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/split.py:51)、[进度读取](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:293)、[付款纯函数](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/external.py:258)、[L3 事件登记](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/demo.py:93)存在。 | E3 证明创建且所有参与人 UNPAID、资金未动。 | **确定缺口：D4 `/demo/events` 网关入口仍 stub 503**。不能经约定控制台链路完成付款→进度更新；不得以创建成功或纯函数替代收款闭环。余数分摊与已付进度本轮未现场验收。 |
| T-5 转后余额、当日累计、卡限额占用预演 | [PreflightStage](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/pipeline/preflight.py:77)实际调用共享规则并记录预演结果。 | E1/E2 有实际预演—审批—执行链；历史测试覆盖预演和执行一致性。 | 最新 main 全字段展示、变更后复演、额度边界以及用户选择 REPLAN 未在本报告现场验收。 |

### 场景二：账单分析（B-1～B-4）

规格：[PRD §5.2](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:572)。

| ID / 必需能力 | 最新实现核查 | 历史实证 | 本轮未验收 / 缺口 |
|---|---|---|---|
| B-1 按时间、类别、商户查询流水 | [get_transactions](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:200)含半开时间范围、category、merchant_id、kind 筛选及 filtered_all 总计。 | E4 使用原始流水独立 SQL 核月合计，不代表逐个自然语言筛选已验收。 | 全部筛选组合、分页与自然语言商户定位未在本轮现场验收。 |
| B-2 月消费分类、占比、环比 | [spending_summary](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/analytics.py:45)确定性聚合，L3 直接调用。 | E4 证明 9 月/8 月金额、类别份额及环比与 SQL 一致。 | 最新 main 多个月份/账户范围/零基期组合未现场复验；不能用当前页流水求月合计。 |
| B-3 异常交易及比较依据 | [detect_anomalies](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/analytics.py:190)实际生成规则证据。 | E4 有 3 条异常，包含深夜大额、新商户大额、4 分钟重复等证据。 | 其他时间窗口和无异常/多卡等边界未现场复验。 |
| B-4 完整月报，汇总＋异常、数字有来源 | [精确月报路径约定与实现证据](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/bill-analysis-proof.md:57)使用业务时钟、完整月聚合、异常和固定格式；缺证据不能报成功。 | E4 为完整正常月报实证。 | 本轮未重新运行。修复有界于完整 typed 月报句式，其他表达不自动覆盖；年度报告是前端组合读取，不能等同此 B4 实测。 |

### 场景三：卡片管理（C-1～C-6）

规格：[PRD §5.3](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:588)。

| ID / 必需能力 | 最新实现核查 | 历史实证 | 本轮未验收 / 缺口 |
|---|---|---|---|
| C-1 卡状态、限额、当日已用 | [get_cards](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:191)、[card_view](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:314)计算已用、剩余、current/replacement。 | E5 真实读取本人卡以支持后续挂失。 | 状态、限额、刷卡＋对外转账已用合计的全部字段本轮未现场逐项核验。 |
| C-2 调低限额，黄级 | [apply_card](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:247)按新旧值生成 TIGHTEN；非占位。 | [组件测试](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/tests/bankcore/test_banking.py:152)有降低与方向断言。 | 未找到本轮该操作完整 L1＋审批＋SQL 收口；不能据此判定失败。 |
| C-3 调高限额，红级 | 同一 [新旧值比较逻辑](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:247)生成 LOOSEN；L2 风险分级已开发。 | 开发/组件记录有高风险分级修复，不等同该场景生产 live。 | 本轮完整 L1 红级 OTP＋最终限额 SQL 未验收。 |
| C-4 境外/线上/ATM/小额免密四渠道，关闭黄/开启红 | [渠道修改](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:255)逐字段比较并生成 TIGHTEN/LOOSEN。 | 组件测试包含开启境外渠道及方向。 | 四渠道分别开关、混合编辑及实际权限生效的完整现场矩阵未验收。 |
| C-5 临时冻结绿级、解冻红级 | [冻结/解冻](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:263)有实际状态变化及冻结卡权限约束。 | E1 真实 F/app 绿级冻结；组件测试覆盖解冻及状态冲突。 | 本轮 L1 冻结/解冻、红级 OTP、受影响交易的完整组合未验收。 |
| C-6 红级挂失、不可逆、可选补卡 | [挂失及补卡](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:288)实际派生新卡、继承限额、重置渠道、更新 current_card_id。 | E5 证明真实 L1 RED＋OTP 挂失，但 replace=false。 | 补卡实现存在，不能写成未实现；replace=true、限额/渠道继承、旧卡不可恢复的本轮生产闭环未验收。新卡申请/开户是额外 Preview 功能，与补卡不能混同。 |

### 场景四：订阅管理（S-1～S-5）

规格：[PRD §5.4](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:605)。

| ID / 必需能力 | 最新实现核查 | 历史实证 | 本轮未验收 / 缺口 |
|---|---|---|---|
| S-1 周期扣费自动识别 | [detect_subscriptions](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/analytics.py:343)有确定性识别逻辑，L3 调用。 | E7 在真实 L1 联动中读取闲置/非闲置标记；不等于各周期识别规则全矩阵。 | 金额波动、28–31 天/7 天、至少 3 次等识别边界本轮未现场复验。 |
| S-2 订阅列表、下次扣费、月/年化花费 | [list_subscriptions](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:269)实际列出订阅，并按 MONTHLY×12/WEEKLY×52 折算合计。 | E6/E7 归档 SQL 证明真实授权状态与后续周期；未覆盖所有列表/年化输出。 | 本轮列表、分页、年化和周/月混合的现场对账未验收。 |
| S-3 未来 N 天扣费及主动提醒 | [upcoming_charges](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:312)有真实未来窗口/预留读取。 | 历史跨场景和预留组件使用真实扣费事实；没有主动提醒通知实证。 | **确定缺口：每日模拟 09:00 为未来 3 天生成 CHARGE_UPCOMING 的生产链路未实现。** 页面未来 7 天列表不等于主动提醒；本轮查询结果亦未现场复验。 |
| S-4 取消代扣授权，之后商户扣费仍产生但拒绝并通知 | [apply_cancel_subscription](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/standing.py:470)真实删除授权；[scheduler](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/scheduler.py:96)仍扫描有效商户账单。 | E6 音乐、E7 健身两期 BLOCKED，无重复扣款、账本平衡。 | **确定缺口：CHARGE_BLOCKED 客户通知生产者未实现**；已有拒扣结果/审计，不能写“已经通知客户”。本轮未重新推进验证。 |
| S-5 闲置识别、建议 | 同一 [analytics](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/analytics.py:343)计算 is_idle；L2 权威谓词重新求值。 | E7 只取消健身，三项非闲置保持 ACTIVE，修复前过量取消曾被 L2 拒绝。 | 本轮未重新运行；其他使用类别/时间边界/只给建议不写入的路径未完整现场验收。 |

### 场景五：理财操作（W-1～W-5）

规格：[PRD §5.5](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:622)。

| ID / 必需能力 | 最新实现核查 | 历史实证 | 本轮未验收 / 缺口 |
|---|---|---|---|
| W-1 固定六题测评、R1–R5、12 月有效，仅 App 可提交 | [问卷/测评读取](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:395)、[确定性计分/保存](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:326)存在；不能用 LLM 自报风险等级。 | E8 使用已有有效测评完成实际理财；这不是新问卷提交实证。另有真实 PG 组件测试。 | 完整 App 问卷逐题选择→黄级审批→权威等级更新，以及 agent 提交拒绝、过期边界，本轮未现场验收。 |
| W-2 产品列表、详情、比较 | [产品读取实现](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:437)实际支持筛选、比较、详情，非硬编码成功占位。 | E8 在真实交易前使用产品事实；不等于多产品比较所有字段已验收。 | 本轮列表/详情/比较、风险匹配与分页完整现场核对未验收。 |
| W-3 红级申购、全文揭示/risk_ack、适当性、MMF 即时/非货币两阶段与未来扣费预留 | [apply_subscribe](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/funds.py:228)、[确认](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/funds.py:527)有真实冻结、净值/份额与后续确认逻辑。 | E6 为 L1 申购＋时钟确认；E8 为 F/app 真实 RED＋OTP 非货币申购、冻结及确认。 | 本轮未运行；无测评/过期/超风险硬拒绝、揭示勾选及 MMF/非货币所有正常异常路径仍需完整清单。**基金结果客户通知未实现**，见通知缺口。 |
| W-4 按明确份额赎回，黄级，到账前资金不可用 | [apply_redeem](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/funds.py:372)、[结算](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/funds.py:604)有份额预留与到账处理；部分数量来自有效 typed 原话。 | E8：真实 L1 1000 份 MMF 赎回；非货币 100 份预留/到账为 F/app 实证。 | 本轮未重新运行；不能将 F/app 非货币赎回记成 L1 实证。超额、重复、在途资金不能再消费等完整现场组合未验收。 |
| W-5 持仓、可用/预留份额、在途订单 | [holdings_view](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/funds.py:661)、[L3 读取](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/reads.py:461)实际投影持仓与订单。 | E8 真实 SQL 核份额、预留、订单与最终现金；E6 证基金确认。 | 本轮未逐状态核对返回/展示；不能拿预览种子持仓当本次申购结果。 |

### 场景六：跨场景联动（X-1～X-4）

规格：[PRD §5.6](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:642)。共用 [apply_plan](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/packages/bankcore/src/safebank_bankcore/banking.py:570)多步骤预演、错误时返回原状态/零效果，以及 L3 原子执行；组件原子性不等于所有联动生产路径都已验收。

| ID / 必需能力 | 最新实现核查 | 历史实证 | 本轮未验收 / 缺口 |
|---|---|---|---|
| X-1 月账单/异常/卡→调低限额，一份计划一次黄级确认 | 聚合、异常、卡与限额工具均有实际实现；组合计划执行逻辑存在。 | E4 证明账单部分；卡限额有组件测试。 | 未找到此整条组合相同等级的已归档 L1＋单次审批＋SQL 收口，记待验收，不判失败。 |
| X-2 闲置订阅取消→以月费派生金额创建本人每月储蓄 | [L2 闲置谓词](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/pipeline/intent.py:218)重算目标，来源/派生约束保留；取消和定时实际执行。 | E7 有整条 L1→审批→SQL→两期时钟实证，金额 19900 分/月、只取消健身。 | 本轮未重新运行；WEEKLY 混入派生模板拒绝、联动中途失败整计划回滚等完整现场边界未验收。 |
| X-3 留足未来七天扣费→稳健产品澄清→派生金额申购，红级揭示/OTP | [robust_products 谓词](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/pipeline/intent.py:229)核测评/产品；未来扣费、派生来源和申购逻辑存在。 | E8 证明单独申购/赎回；不证明本组合选择、派生金额和扣费预留完整链。 | 未找到整条组合生产 L1＋澄清＋审批＋SQL 收口；扣费不足 REPLAN、无有效测评 DENY、到期资金不可提前用仍需现场核验。 |
| X-4 可疑交易对应卡冻结→储蓄转活期应急，整份黄级一次确认 | [suspicious_cards 谓词](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/pipeline/intent.py:247)自行读取异常求目标，冻结与本人互转实现存在。 | E1 单独冻结；E5 单独挂失不是 X4；共用原子性有组件证据。 | 未找到整条组合 L1＋目标澄清/选择＋一次审批＋SQL 收口；记待验收，不以“工具存在”判组合已通过。 |

## 可确定的实现缺口与静态部分覆盖

1. **D4 AA 付款/外部事件的网关入口未实现。** [demo.py:84–86](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/api/demo.py:84)把 D4 注册为 stub；[stub:18–20](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/api/__init__.py:18)固定抛 503。L3 事件登记、S1 系统事件和付款纯函数存在，但不能绕过 L2 来宣称 D4/AA 全流程通过。D2 场景准备同样是 stub。D1 公共 reset 虽仍为 stub，项目已有新增 [demo-reset 管理命令约定](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:1806)，因此不能笼统称“reset 未实现”。
2. **扣费提醒/拒扣/定时/基金结果的客户通知生产链未实现。** [PRD:1189](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:1189)要求六类通知及每天模拟 09:00 未来三天提醒。实际 [notify.py:17–31](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/notify.py:17)只定义 APPROVAL_PENDING、EXECUTION_RESULT；[scheduler:74–112](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/bank-backend/app/scheduler.py:74)仅生产定时/商户扣款 job，未生产 REMINDER；[S1:710–712](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/internal/scheduled.py:710)不支持 REMINDER；其中 [SCHEDULE_RESULT:253](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/services/gateway/app/internal/scheduled.py:253)是 audit_append，不能当客户通知。服务源码检索未见 CHARGE_UPCOMING/CHARGE_BLOCKED/FUND_RESULT 生产代码。
3. **手机号转账在 L2 typed 目标匹配中存在静态覆盖差距。** L3 读工具按 phone 检索已经存在；L2 `_payees` 与 exact 分支仅按 name/alias/近名。此项应单独核实/补齐，不能用只读搜索支持来证明自然语言写授权支持。本报告没有执行现场请求，故不记录运行失败码。

## 合成预览和真实后端的边界

[前端 api.ts:12、28](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/frontend/src/mobile/api.ts:12)只有构建时 `VITE_BANK_MODE=live` 才调用 BFF；其他值走 `previewRequest`，助手也走合成流。因此页面齐全、合成确认、UI单测或截图均不能证明真实三层业务状态。

[HANDOFF:143–152](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/frontend/HANDOFF.md:143)及 [PRD:1558–1562](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:1558)明确的额外边界：完整 PAN/查看卡号 PIN 验证、新卡申请/开户仅 Preview；用途标签仅 localStorage；短信/系统主动推送未实现；Preview 助手不调用真实模型；报表为组合读取、txt 导出。它们不能被写成冻结 18 read/14 write 的真实银行能力。可选补卡属于已有后端 W10，不能与 Preview 新卡申请混淆。

## 当前仍保留的整体门禁

- [最新开发记录:23–25](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/development-2026-10-31.md:23)仍要求两个独立 reset 的六场景真实验收、完整 43 条真实状态清单、生产模型录制、实际阻断外网后的回放及 L2/L3/审批/OTP/SQL结果；全部通过前不能冻结/tag `demo-v1`。
- [demo-runner:35–49](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/demo-runner.md:35)默认 01/16/27/34/36/23 是六场景代表序列，不是全部 29 需求或 43 条。自动 SQL 模块只支持这六条固定正例；其他用例不能推断通过。该组件 [末尾说明:263–266](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/demo-runner.md:263)仅账单16已有先前实际 oracle SQL正例，其余五条自动核验、完整时钟/回滚/两轮门禁仍单独待验收；不能把历史人工 SQL 实证混成新工具六条全部通过。
- [PRD §15](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/PRD.md:1768)还有至少 35 正常用例、每场景≥5、replay TSR、授权隔离/UNKNOWN/原子性、安全对照/指标等要求。开发 CI、模拟 provider 测试、现有组件实证分别证明各自范围，不自动关闭这些门禁。
- [官方提交物留档](/Users/jet/.codex/worktrees/safebank-freeze-live/WeBank/docs/acceptance/official-v1.md:10)：技术文档、≤10分钟答辩PPT、源码（README/部署/测试）、安全自评为必交；≤5分钟演示视频可选。模板详细格式、上传大小和提交入口权限仍未核实。本报告不宣称提交物已齐或已提交。

本文件为本轮静态需求审计，29 个需求 ID 已逐项列出；没有新增现场业务验收结果。
