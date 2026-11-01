# 正式43例与PRD 29需求差距（本轮终态）

执行源 `aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1`；代际 `demo_20261101084917_8e1ed563`。固定主清单 [primary-43-results.csv](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/primary-43-results.csv) 共43例：**19 PASS / 16 PARTIAL / 8 FAIL**。6项补测独立记账；没有把原生表单或时钟补测回改主例。29项需求尚未完成全条件验收，矩阵未关闭整项PASS。

完整条件、来源和证据见 [requirements-coverage.json](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/requirements-coverage.json)；主结果SHA256为 `6c0d1ff29a08d0f16d5847b290e83d8938fb5e0482f2254ed492b9681dca5abb`。

8个主例FAIL为：08取消赵六、12流水助手最终失败、13商户查询、25订阅年费理解异常、32产品比较、34助手赎回、37应急联动、38产品选择。

## 已增加的业务证据

- 原生同两产品比较、MMF赎回1000份立即到账100000分通过；主32/34仍FAIL。
- 33原200000分申购于10/16按NAV1.2311确认1624.5633份，SETTLED/pending归零。非MMF原生赎回1000份先预留、10/19到账123110分；stable剩624.5633份/cost76890/预留0。
- NOV1两条MONTHLY定时分别成功20000分和19900分；音乐/健身两笔已取消商户请求BLOCKED/MANDATE_NOT_ACTIVE且零transfer；4笔有效代扣正常，站内提醒/阻断/定时/基金通知存在。clock内部45条SQL检查PASS；查询原完成job没有重复业务/通知/grant。
- D4同request两次调用仅一笔10000分入账、一项PAID；AA已收100/待收200，张三活期从565000到575000分。均为合成外部事件，不是朋友真实支付。

补测权威汇总：[supplement-results.json](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/supplement-results.json)；[clock oracle](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/clock-business-verification.json)；[AA receipt](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/aa-payment-verification/receipt.json)。

## 必须保留的状态与规范差异

- 38真实前置是33预留200000、34失败：available1000000−7日会员费4500=995500分。其选择按钮CONTRACT_INVALID，没有生成该金额申购；后续原生MMF到账不能倒灌正式前置。
- 两笔被取消代扣的bank指令为DELETED，但NOV1 gateway中对应grant仍ACTIVE/revoked_at=null；PRD要求取消后REVOKED。实际拦扣正常、零资金外流；这是持久授权状态不符合文档，不能扩大成已发生资金漏洞。
  依据：[PRD:1177](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/PRD.md:1177)、[PRD:1205](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/PRD.md:1205)、[NOV1 gateway receipt](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/receipts/SUPP-CLOCK-NOV1-after-gateway.json)。
- B2历史汇总读取已存tx.category并按白名单归其他，和PRD商户MCC→八类尚未收口；新交易生产者已有merchant_category映射。核心报表金额正确不等于完整分类口径验收。
- 结算后已保存的一份DOM捕获在金额变化途中，aria金额/持仓/SETTLED和SQL正确；该图不作为静止金额视觉证明，需另行重拍。中断后浏览器连接不可用，本轮未重拍；此为证据限制，不记产品失败或资金错误。

## 29需求逐项未关闭范围

| 需求 | 已观测主路径或子路径 | 仍需验证/修复 |
|---|---|---|
| T-1 | 01姓名转账10000分；03别名转账30000分；04本人互转50000分 | 姓名/别名/本人互转01/03/04通过；02近名追问只有空FORM_REQUIRED，未完成候选选择闭环。；手机号、真实红级转账、超时/状态变化/UNKNOWN和执行重放等分支未在本轮补齐；39/40/41安全零写不能替代这些正路径。 |
| T-2 | 05重复账号no_op去重 | 05仅证明与种子赵六相同账号的去重no_op，没有真实新增孙九。；从未登记合法目标的黄级新增正路径、值级授权绑定与新增请求重放尚未验证。 |
| T-3 | 06/36 MONTHLY创建且无提前扣款；SUPP-CLOCK-NOV1两指令各一次SUCCESS/POSTED并推进周期 | 06 MONTHLY及36 SELF MONTHLY创建和到期已由clock证实，各一条SUCCESS/POSTED、next_run_at推进、授权绑定与站内结果通知存在。；07列表仍多FORM_REQUIRED/目标泛称；08取消失败，不能因赵六后来按仍有效指令执行而改判08。；ONCE/WEEKLY、结束日/月底、余额不足不重试、取消后不再执行与真实回调重放尚未全部覆盖；原D3 job GET不等于重新投递事件。 |
| T-4 | 09含本人四人均分/创建零扣款；SUPP-AA-PAYMENT同请求两调用仅一笔10000分到账/一项PAID | 09四人均分创建通过；SUPP-AA-PAYMENT同request两次D4仅一项PAID、一条10000分入账，已收100/待收200。；10原始进度查询仍PARTIAL/FORM_REQUIRED；付款后的实际助手进度查询未提供本轮新证据。；100元三人余数、不含本人、错误金额/新事件重复支付、跨客户读取等未覆盖。 |
| T-5 | 01/03/04部分预演；40 REPLAN零资金变更 | 主转账面板和40安全REPLAN仅为部分预演材料；40提示最多50000元与实际单笔/余额不一致。；完整余额/available/当日累计/卡占用展示、多步骤中途失败原子性、状态/政策变化重新确认及UNKNOWN恢复尚未逐项验证。 |
| B-1 | 12结构化消费55050分及转账分列正确（主回复失败） | 12流水卡55050分正确但助手最终fixed fallback；13未按商户查出实际兰州拉面4550分，均保留FAIL。；类别筛选、全量分页、组合筛选与空结果/期间补问仍未覆盖；原始untrusted_data包装展示未修复。 |
| B-2 | 14/16/35源金额和9月/8月统计核心数字已对账 | 14聊天9月/8月数值正确但结构化卡错月、内嵌仍10月；16/35核心9月报告数字通过不等于所有表示层一致。；PRD MCC→八类与当前按已存tx.category白名单归并的历史实现存在未收口差异；新增交易有merchant_category生产者映射，不能笼统称完全没有MCC映射。；空月/前月0与历史/新增类别统一边界未验证。 |
| B-3 | 15明确近期窗口两异常；16/35九月三异常 | 15明确最近窗口两异常、16/35固定9月三异常为正证据；15仍有无关FORM_REQUIRED与包装。；全部异常规则阈值/边界/样本量、多个可疑卡澄清与跨客户过滤未在现场逐项覆盖；37联动失败不否定已正确读出的异常数字。 |
| B-4 | 16九月报告644800分、前月133190、38412bp及展开页一致 | 16核心9月报告与展开同月/数值PASS；仍有全局原始包装及B2分类口径限制。；无数据/依赖失败等报告分支未验证，不能关闭完整B4。 |
| C-1 | 17卡状态/单笔/日限/余额等读取部分正确 | 17少报当日剩余限额19600元并混淆余额与单笔可刷上限，另FORM_REQUIRED。；补卡后当日用量聚合及全部状态查询边界未验证。 |
| C-2 | 18及35单笔收紧 | 18/35单笔收紧正路径通过。；日限收紧、FROZEN收紧/LOST拒绝、最大值与single<=daily、拒绝/过期及重放分支未全部验证。 |
| C-3 | 19单笔升800000分/RED OTP与用户确认 | 19单笔升限RED/OTP/用户最终确认与审批前零变更通过。；日限升限、冻结/挂失不可放松、最大值/旧审批失效等分支未全部验证。 |
| C-4 | 21境外开启RED；20境外关闭YELLOW | 21/20只覆盖境外开启/关闭且分级和真实字段改变正确。；online/atm/quickpass三项、混合开关、实际刷卡渠道拒放与卡状态边界未覆盖。 |
| C-5 | 22a GREEN冻结；22b RED解冻 | 22a/22b冻结与解冻核心状态转换通过；37未冻结所以40不能作为冻结外转拦截证据。；FROZEN刷卡/外转拒绝、SELF/代扣不误拦、重复no_op/LOST不可恢复尚未全部验证。 |
| C-6 | 23李四挂失LOST、无补卡 | 23仅挂失：李四ACTIVE→LOST、无新卡/补卡/资金副作用。；明确replace=true补卡的ID/尾号/限额继承/默认渠道/current卡切换与不重置当日用量尚未验；不能将挂失PASS等同补卡PASS。 |
| S-1 | 24四项月扣费候选/月费26500分 | 24四项月扣费识别正确但FORM_REQUIRED/内部ID；clock后有效商户扣款已发生。；周费、波动5%/次数/间隔边界、历史与新MERCHANT_CHARGE同口径识别仍未重新查询验证。 |
| S-2 | 24月费及28已取消音乐节省口径（不替代25） | 25理解span=[0,0]内部异常、无业务reads、年费3180元未交付，FAIL保留。；周/月折算、逐项floor、取消后总额/下次due与所有状态列表未完整查询；24月费或28节省不能替代全年查询。 |
| S-3 | NOV1四个有效商户提醒作业与CHARGE_UPCOMING；原D3完成job查询不重复写业务/通知/grant | 26真实45元代扣+200元定时预留245元，但聊天漏解释定时且窗口日期错误。；clock已有4条实际有效扣费的09:00 REMINDER/CHARGE_UPCOMING及唯一billing+due通知，原job查询未重复；未证明每个空日扫描或真实事件重投递恢复。；站内通知存在不等于外部短信/系统推送或全部通知文案UI验收。 |
| S-4 | 28/36取消状态DELETED；音乐/健身实际各一次BLOCKED零扣款及站内通知 | 28音乐/36健身取消后，clock各一次MANDATE_NOT_ACTIVE/BLOCKED且0transfer，CHARGE_BLOCKED通知存在；有效4笔代扣正常执行。；已删除音乐/健身的gateway scheduled_grants在NOV1回执仍ACTIVE/revoked_at=null，违反PRD取消后REVOKED口径；实际拦扣正常，不推断资金漏洞。；重复取消/新事件重投递、通知文案/已读与全部失败边界未全部验收。 |
| S-5 | 27唯一闲置健身候选；36 L2集合对应取消 | 27唯一健身闲置识别正确，但下次日期错写09/25实际10/25，另FORM_REQUIRED；36取消集合正确。；近60/60–120天、培训类别、无历史/仍使用等完整边界未覆盖。 |
| W-1 | 29现有风险R3读取；30直接改R5零业务写 | 29仅现有R3读取且多FORM_REQUIRED；30直接改R5被零写阻止但缺六题指引。；本轮没有固定rq_v1六题正路径补测；面板、计分R1–R5、落库/有效期/幂等与缺测评/过期分支不能记通过。 |
| W-2 | 31≤R3列表；SUPP-COMPARE-APP原生两产品比较 | 31风险匹配列表正确但FORM_REQUIRED；32助手声称两已存在产品不存在，FAIL保留。；SUPP-COMPARE-APP原生两产品比较的R2/R3、NAV1.0520/1.2310、起购/确认/赎回天数和历史收益提示PASS；这不修复32自然语言链路。；独立get_product完整详情/揭示版本/NAV历史、2–4产品全边界仍未全部覆盖。 |
| W-3 | 33非MMF红级受理/冻结与T+1确认SETTLED；38b R2申购R4硬DENY | 33 RED确认下单200000分后PENDING_CONFIRM/pending正确，10/16以NAV1.2311生成1624.5633份并SETTLED/pending0；38b适当性硬DENY通过。；MMF申购即时过账、无/过期测评、起购/15点截止/非交易日、确认超时EXPIRED释放、SCHEDULED_SHORTFALL未全部验证。；38产品选择CONTRACT_INVALID属于X3失败，不改成成功；单一申购子路径成功不能关闭完整W3。 |
| W-4 | SUPP-MMF-APP原生部分赎回即时到账；SUPP-NONMMF-RED预留后延迟到账SETTLED | 正式34缺get_holdings被plan guard阻止，FAIL保留；SUPP-MMF-APP原生1000份即时100000分到账，MMF剩9000份。；原生非MMF1000份先预留、10/19实际123110分到账SETTLED，stable剩624.5633份/cost76890/reserved0，SQL oracle已核并有结算后UI。；自然语言赎回链路、赎全/超份额/重复售卖、成本末差、实际执行重放等仍未全部覆盖。 |
| W-5 | 34b原始持仓/待确认订单核心数据；原生UI及SQL观察下单/结算生命周期 | 34b初始持仓/待确认订单正确但FORM_REQUIRED、产品名/UTC日期错误。；clock后的三订单SETTLED、份额/成本/预留及原生UI已核；尚未覆盖FAILED/EXPIRED/跨客户全部订单状态与助手刷新链路。 |
| X-1 | 35真实分析+降限同计划执行 | 35核心fresh9月汇总/三异常+同计划限额800000→100000一次黄确认PASS。；MCC历史口径、原始包装及完整多步失败/拒绝原子性仍未收口。 |
| X-2 | 36同计划取消/派生定时；NOV1真实SELF到期划转19900分及健身拦扣 | 36同计划取消健身+derived19900分MONTHLY SELF创建、11/1单次划转及授权/通知通过。；健身后续商户请求实际被拦扣；但其grant仍ACTIVE，PRD撤销口径未满足。；WEEKLY/未知费率拒绝、旧根隔离/中途失败/事件重投递等边界尚未全部验证。 |
| X-3 | 38三产品候选集合正确；选择后的业务未执行 | 38出现三候选但用户明确选稳盈短债后CONTRACT_INVALID，未生成995500分申购预演/订单，FAIL保留。；第一G19契约屏障已现场失败；后续G3父链失配为源码事实，未越过第一屏实跑。；完整选择→派生金额→红确认→申购结算、扣费缺口REPLAN/缺测评DENY全链路未通过。 |
| X-4 | 37失败前已实际读取异常/卡/账户并安全零写 | 37已读取异常/卡/账户但to_account_id tagged实体错误，未生成可确认计划、无冻结或互转，FAIL。；单计划冻结+200000分应急互转、成功后冻结外转拒绝、多卡澄清及中途原子失败仍未验证；22a/04分别PASS不能替代。 |

## 独立冻结门禁

D2 scene prepare仍stub、七幕独立初态演示、两个独立reset完整链路、生产录制与实际断网回放、攻击/对照指标、精确最终head CI/独审/交付物核对仍是额外冻结门禁。它们不增加第30个业务项，也不能用默认六例、clock45断言或OBSERVED退出0替代。

本轮未补固定六题问卷正路径；T2仅重复去重、C6仅挂失、四渠道仅境外、ONCE/WEEKLY、MMF申购、缺测评/过期、确认超时、执行重放与原子失败等分支仍不能宣称完成。

本报告只读取已有文件并修改本人负责的矩阵/报告；没有调用服务、DB、LLM或浏览器，没有修改主CSV、AGENTS或产品代码。
