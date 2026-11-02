# 2026-11-01 写计划与授权路径只读调查

基线：`aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1`，调查开始时为 clean detached HEAD。报告源：`/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/`，正式代际 `demo_20261101084917_8e1ed563`。仅静态读文件；未运行测试、模型/API、浏览器、Docker、DB；未修改仓库、其他 worktree、原始报告或共享 AGENTS/PRD。本文不是已修复或已验收声明。

## 1. 证据映射与优先级

CSV 的“序号”与“ID”不同：序号39 / ID34 是自然语言赎回；序号32 / ID37 是 X4 冻结与应急互转；序号34 / ID40 才是五万元转账负例。本文使用 ID。

| ID/对象 | 正式证据 | 确认的实际结果 |
|---|---|---|
| 08 | primary-43-results.csv；formal-parser-gaps.md:96；receipts/08-{before,after}-bank.json；08-read-oracle.json | 指令存在且只指向赵六，20000分 MONTHLY；G3返回 FORM_REQUIRED；无取消、仍 ACTIVE。clock-supplement-audit.md:58 进一步记录该指令在原失败分支到期真实付款20000分。 |
| 34 | 34-read-oracle.json；screens/34-result.txt；evidence-review-funds-x3.md:36；receipts/34-{before,after}-bank.json | G3为 SCOPED / FUND_REDEEM / prd_mmf / 明确1000份。成功读取仅 get_profile/get_accounts/list_products，无 get_holdings。计划本地校验终止，MMF仍10000份、无赎回订单/资金变化。 |
| 37 | 37-read-oracle.json；screens/37-result.txt；evidence-review-22-41.md:31 | G3正确 SCOPED；已读取异常/卡/账户；最终 to_account_id tagged object 错误。没有计划、没有冻结、没有200000分互转。不能把后续40解释成被冻结卡拦截。 |
| 音乐/健身取消 | receipts/28-after-bank.json、36-after-bank.json 及对应 gateway 回执；requirements-gaps-final.md:21、:48；REPORT.md:64 | 银行 si_zs_music / si_zs_gym 已 DELETED，网关相同行仍 ACTIVE、revoked_at=null；后续商户请求实际 BLOCKED/MANDATE_NOT_ACTIVE，零扣款。是授权生命周期缺口，非已经发生的绕权资金外流。 |
| 40 | receipts/40-after-gateway.json；screens/40-result.txt；evidence-review-22-41.md:33 | 首个硬违规 CARD_SINGLE_LIMIT；RED/REPLAN，零写。当前卡 ACTIVE / 单笔100000分、活期610000分；页面却说“最多50,000.00元”。保存的 hints 表没有该计划行，缺原始G4响应。 |

其余写意图 PARTIAL：02（收款人未明确）、30（禁止助手改R5）、39（未登记钱七目标）、41（疑问句）。这些没有有效写作用域或实际执行。它们的通用 FORM_REQUIRED / 引导由另一个 parser/交互任务处理；不能为了消除 PARTIAL 给它们补写授权。35/X1、36/X2 核心写已 PASS；不把其展示尾项误归为资金执行失败。

## 2. 08：真实投影与取消目标解析脱节（确定根因）

生产链：

- `services/bank-backend/app/state.py:245` `_derive_views` 对本人/外部/本行目标分别产生通用 `target_display`；EXTERNAL 固定“他行收款人”，同时保留真实 `payee_id`。
- `services/gateway/app/utterance/parser.py:409`/`:412` 可以识别“取消给赵六的定时转账”句式。
- 但 `parser.py:924` 的实体绑定只比较 command.target 与 `ScheduledView.id/target_display`，不沿 payee_id 到当前客户收款人查询。于是“赵六”对“他行收款人”不匹配，返回默认 Parsed/INTENT_UNCLEAR。
- 现有正测 `tests/gateway/w1/test_parser_coverage_a.py:116` 的 `rich_state` 人工填 `target_display="赵六"`（:45），与真实L3投影不同，故未捕获问题。

最小旧 fail 红测：复用该 rich_state，将唯一赵六指令 target_display 改为真实“他行收款人”，保持 `payee_id="pye_zl"` 与本人所属的真实 payee；对精确原句断言 SCOPED / SCHEDULE_CANCEL / 该唯一 instruction_id。旧代码必然默认 Parsed（静态判定，未执行）。生产修改可限定 gateway/parser 与测试，不需修改共享契约，也不必把 L3 display 当授权身份。

负控必须覆盖：两条同一收款人定时不得猜一条；仅其他客户存在同名 payee/指令不得匹配；未知目标、引号、否定、疑问保持无作用域；DELETED/重复取消的幂等策略明确；不能把“他行收款人”泛化为任意指令；近音/同名至少维持当前精确优先与唯一性规则。适宜以 owner-scoped payee_id 关联为主，display只作显示。

需要 root PG：G3登记→L1已读 list_scheduled 来源→绿色 cancel_scheduled 的 G4/G8→L3 DELETED；到期同一指令不付款、金额/账户/账本不变；并验该取消不会删除同名其他定时。真实到期/DB属于 root，本文未做。

## 3. 34：必要读取完成性被“尝试过”替代（确定停点，具体现场子分支待证）

基线已包含 `99df716 fix: load actual redemption prerequisites before planning`。`services/agent/app/main.py:207` 生产启用 supplement_transfer_reads；`gather.py:433` 在赎回写请求里列出 get_accounts/list_products/get_holdings；`plan.py:369` 对缺 get_holdings 正确拒绝。因此“再加一次赎回补读”不是有效的新根因陈述。

可直接确认的代码缺口：

1. `gather.py:280` 在 `_validate_calls` 前将模型所有 call.name 加入 tools_attempted。
2. 参数不合法的整批请求在 `:290` 后全部不执行；合法工具若被同批另一个错误连带取消，也没有 ReadRecord。
3. 404/NOT_FOUND 在 `:307` 被转换成模型反馈，无 ReadRecord。
4. `_supplement_reads` 在 `:449` 用 `read_tools | tools_attempted` 判断是否补读。于是“工具名已尝试但没有成功结果”会阻止强制 get_holdings，仍返回不完整 reads 给 plan。

正式 oracle 证明缺 holdings；现有回执没有原始模型回合或 read失败详情，不能确定此次是“无效批次”还是“猜错过滤实体404”，也不能声称持仓后端不可用。应把上面两类确定性可复现缺口作为红测，同时补一个生产模式/配置桥测试，避免只测裸适配器默认值。

最小旧 fail 红测：

- 在 `tests/agent/test_redemption_gather.py` 复用 redemption_state，production supplement=True，模型先发合法JSON但不合法 schema 的 get_holdings，下一轮停止；executor能正确服务无筛选 holdings。断言返回含真实 get_holdings ReadRecord、来源为 executor 实际 source_ref。旧代码因 attempted 集合跳过补读而失败。
- 模型猜错 product/account 导致 get_holdings 返回 `GatewayResponseError(404, NOT_FOUND)`，随后停止；断言以无筛选参数补读本人持仓，不能重用不存在的 source_ref。旧代码同样缺结果。
- 混合批：get_holdings 自身合法、另一个调用参数非法，整批未执行，不能把 holdings 视为完成。
- 后端真依赖失败、403/429/provider配额错误仍立即失败；不能无限重试或吞错。只读产品问题不强制赎回读取。

最小实现边界：只调整必要读取的“已成功完成”判定与有限读补齐（read-only），保持 PlanAdapter 的持仓存在、份额上限与用户1000份来源校验。不能把 holdings 总额当赎回数量，不能伪造 ReadRecord/source_ref，不能绕过 L2 适当性/来源/确认。

现有相关测试：`tests/agent/test_redemption_gather.py`（只覆盖未尝试/成功去重/补读依赖失败）、`tests/agent/test_gather_format_recovery.py`（非法JSON恢复，不覆盖合法JSON但schema错误后的必要补读）、`tests/agent/test_redemption_sources.py`。本任务未运行它们。

需要 root PG：明确只赎1000份、黄色一次确认、确认前零写；MMF即时到账与非MMF份额预留/延迟到账、超份额/跨客户拒绝、重放无双赎。这些原生表单补测不能替代修后自然语言链路。

## 4. 37：strict实体提案形状不稳定，已有异常归一重试未解决现场（确定停点）

`packages/contracts/src/safebank_contracts/schema.py:68` 定义 payee_id/to_account_id 为 tagged entity；`:140` from_strict_plan 把非dict/无kind拒绝为 ValueError。`PlanAdapter` `plan.py:1061` 转换后再做来源与语义校验；`:1120` 对 ValueError 只有一次有界反馈重试。

基线已包含 `00d4712 fix(contracts): malformed strict plan raises ValueError so the planner retries` 及 `tests/agent/test_llm_adapters.py:1960` 的“坏首轮、好次轮”正测。正式此head最终仍报该 ValueError；因此本轮不是再做 TypeError→ValueError。X4必要读取路径已在 `gather.py:194` 确定性获得真实数据，本例读取证据与此相符。

证据限制：保存DOM只证明最终待转换的某个 to_account_id 不满足 tagged形状；原始两次计划均未保存，不能断言一定是裸字符串（也可能null/另一非dict）。不能凭错误字符串推断首次重试是否执行或实际计划的两步内容。

可审查的最小红测入口：复用 `tests/agent/test_cross_scene_x4_gather.py:97` 的两步X4提案与真实read来源，把 transfer.to_account_id 改为同值已读账户裸字符串、payee_id改为null，重复两轮；旧PlanAdapter最终ValueError。若采用有限形状规范化，断言生成的仍是冻结目标卡→同一客户储蓄转活期200000分、来源和depends_on不变，而非向任意外部目标。若选择只加强prompt而不规范化，此红测只能证明最终安全停点，不能证明已恢复成功；仍需生产模型由 root 另按授权验证。

安全实现建议：规范化仅放 L1 提案适配边界，限既有实体同值表示（已读ID/合法none），随后完整 PlanBody、arg_sources与L2重新验证；不能全局放宽 contracts decoder来接受任意目标，不能增加重试次数代替根因，不能根据“应急”猜账户或金额。至少给生成模型明确的 SELF transfer 两个tagged字段范例。原始结构再次耗尽时返回正常无执行的错误，不向用户外露Python异常。

必要负控：未知/跨客户ID、无read来源、错pointer/源与目标同账户、改2000为其他金额、冻结与互转中途失败均不得有部分提交；不得自动执行任何一个步骤。真实SQL原子性与fresh风控须 root PG独验。

## 5. 取消代扣后 grant 未撤销：统一终态归档没有生命周期处理（确定根因）

生产静态搜索：scheduled_grants 写入仅见 `seed_grants.py:44` 与 `internal/scheduled.py:745` backfill；无将 grant 更新为 REVOKED 或设置 revoked_at 的生产实现。表已支持两状态（`tables.py:259`）。

正常终态统一入口：`services/gateway/app/state.py:708` `archive_execution_terminal` 验证并归档 L3终态、消费claims、发 EXECUTION_RESULT。`state.py:952` `archive_orphan_execution_terminal` 是 UNKNOWN孤儿恢复入口。两者调用 `_finish_terminal`（:924），该函数只改plan/claims/utterance，不处理被取消指令。`api/execution.py` 的正常G8、resume以及G5/G8 UNKNOWN reconciliation都会调用归档；只在某一个 HTTP route 加更新会漏恢复路径。

最小旧 fail PG红测：用 `tests/gateway/wave1_state/helpers.py` seed / `test_terminal.py` 模板，给当前客户seed ACTIVE grant与真实已提交 cancel_subscription StepResult（instruction_id/status=DELETED）；归档 COMMITTED 后断言对应grant REVOKED且revoked_at已填，其他客户/指令不变。旧代码保持ACTIVE。参数化 cancel_scheduled、normal/orphan、UNKNOWN reconciliation、no_op重复取消、同计划取消+新定时。需要匹配当前plan中的取消步骤和已提交结果身份，不能仅凭未核实字符串构造成功。

撤销位置：只在经过现有真实性/plan/customer/attempt guard 的 COMMITTED、EXECUTED取消步骤终态归档时，同一gateway短事务里撤销。evaluate、预演、PENDING_APPROVAL、FAILED、UNKNOWN尚无事实、L3 CANCELLED都不能提前撤销。L3与gateway无法跨库原子；若L3已提交但gateway归档故障，应保持UNKNOWN后重查归档，幂等恢复撤销。

必须补的恢复/竞争边界：

- 新创建定时在第一次到期前可能没有持久grant（正式06/36即如此）。取消时只 UPDATE已有行会漏撤销墓碑；源创建计划的COMMITTED记录还在，旧事件可触发补建ACTIVE。需决定以绑定原指令身份的撤销墓碑或权威当前状态阻止backfill复活，并同步文档口径。
- `_backfill_grant` (`internal/scheduled.py:689`) `INSERT ON CONFLICT DO NOTHING` 后直接返回本地构造的 ACTIVE字典（:759），没有重读可能已被并发撤销的行。PG竞态要覆盖 backfill读None→取消撤销/墓碑→backfill冲突，保证持久/实际使用均不复活。
- S1当前先检查 revoked/scope (`scheduled.py:910`)，随后才跑商户当前状态 (`:935`→`_handle_merchant_event`)。直接补REVOKED后，原本已取消合法商户请求的错误码会从 MANDATE_NOT_ACTIVE 变为 SCHEDULED_BYPASS。PRD §5.4要求前者，§7.11又泛述后者；root需明确合法取消商户与伪造grant/scope的优先级，不能只改状态不看站内阻断通知。
- `_backfill_grant` 只从源创建计划严格恢复；REVOKED不得改回ACTIVE；missing/依赖不可读仍RETRYING；终态 event_decisions 不重复记账/通知/重建grant。

资金边界：`scheduled.py:601` 对商户拿fresh快照，调用共享 `bankcore/events.py:326` `apply_merchant_charge`；`:376` 拒绝非ACTIVE/到期mandate。正式两笔取消后零扣款与该机制吻合。应保留该双层拒绝，不能用“已有grant撤销”替代fresh银行状态校验。

root PG亲验：正常音乐取消与X2健身取消；取消后合法商户请求BLOCKED/零transfer/正确通知；未取消四笔代扣继续正常；UNKNOWN恢复、SQL回滚、孤儿恢复、取消-before-first-backfill、旧event回放及backfill撤销竞争。这一组件涉及授权持久化，应独立评审。

## 6. 写类PARTIAL与case40的最小边界

02/30/39/41均安全零写，但尚无相应业务完成：不要把02“王”自动选王五；不要让30直接改R5或伪造问卷；不要把39未知目标自动注册后转账；不要让41疑问句获得TRANSFER作用域。精确原因/候选/问卷跳转可由另任务补，负控必须留下。

40确定是错误指导文案，尚不能确证错误“最多50000”来自哪层：L1 respond.py:363/389只抄网关replan_hints标签；L2 decision.py:156依据preflight max生成标签，但正式保存gateway.hints无该计划行，也未留存G4原始payload/agent checkpoint。不能把“无hint持久行”等同“响应里没有hint”，也不能推断界面金额是有效可执行建议。

静态可确认的相关算法缺口：preflight.py:272 `_failed_step_max` 对CARD_SINGLE_LIMIT直接返回单笔限额、对CARD_DAILY_LIMIT直接返回日剩余，不同时取available、7日预留、其他限制的最小值；因此一般情况下hint也可能不可执行。最小纯测可设单笔限额高于余额但请求更高而先命中CARD_SINGLE_LIMIT，证明目前建议数仍超余额；修后建议需能在原scope下重新预演通过，不能自动改变金额。当前正式case40反而单笔最小为100000分，现静态算法应给1000元，故不足解释50000元现场文字；需要保存响应或用确定性全pipeline旧fail定位，不应伪称根因已闭合。

标签含“确认风险后重试”也会误导硬限额（respond.py:52）；风险升红AMOUNT_ABOVE_YELLOW_CAP/CUMULATIVE_OUTFLOW与硬CARD_SINGLE_LIMIT需区分，OTP不能解除硬限额。此处仅文案和有效建议，不放宽阈值，不自动提限/改额/执行。

## 7. 建议独立实现划分

1. **取消目标映射**：gateway parser与parser单测；root另串PG，必要L3投影无需改变。不要顺手修改只读解析分类。
2. **L1写计划可靠性**：gather.py完成性补读、plan.py有限实体形状与错误收口、agent测试；与查询任务修改gather.py可能冲突，须独立worktree或串行整合。契约decoder保持严格。
3. **grant取消生命周期**：state.py统一正常/孤儿归档、必要S1恢复防复活与PG专项；不要混入L1/frontend。涉及授权与竞态，独立安全复审。
4. **有效REPLAN建议/文案**：preflight/decision+agent respond所需专项，先拿可确定红测；case40实际50000来源保留未闭合标记，不以猜测修复替代证据。

本报告仅文件/控制流核对，所有上述旧fail为静态预期而非已运行红测；无新增PASS、无现场修后回归、无部署、无合并、无冻结。
