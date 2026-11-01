# 跨场景真实读取链路修复（2026-10-31）

基线为已交付前端分支f4af1ba；本分支fix/cross-scene-gathering。仅修改L1 gather私有读取辅助、必要cards显示与相应纯测试；不改respond/verify/写执行，不运行真实API、模型、DB、Docker或浏览器。

## X1：月度分析 + 卡单笔限额

静态根因：原固定月报路径只接受NO_WRITE且needs_write=false；SCOPED的X1走模型ReAct，模型只读get_cards后正常停止时没有月汇总与异常证据。既有补读只覆盖转账/AA/挂失/赎回。cards原实现仅在无写decision且needs_write=false时显示分析读卡，因此已有真实分析证据也会被写状态隐藏。

修复：仅单条原始typed、SCOPED、needs_write=true、无实际clarification及echoes_untrusted的完整“分析明确自然月消费/账单 + 找异常 + 设置/降低卡单笔限额”句式进入固定只读前提收集。fresh get_profile业务时钟按Asia/Shanghai确定[月初,下月月初)，读取完整get_spending_summary、detect_anomalies和get_cards。保留每个实际返回的source_ref、versions、observed_at、untrusted_paths与污点包裹；不合并分页流水、不读取主机墙钟、不改needs_write/parse_status/原话、不生成或执行写计划。依赖错误直接传播，不以模型或假数据补足。

只为上述X1追加真实summary/anomaly卡，保留原PLAN_STATUS及审批动作。必须同时有当次profile、正确月汇总和完整异常区间；缺证据、错月、截断区间、异常越月或其他写任务均不生成分析卡。已有L2审批/JWT/澄清与L3原子性路径未改。

红测：`tests/agent/test_cross_scene_gather.py`初始5项全部失败，表现为缺汇总/异常读取及只有PLAN_STATUS。绿测：同文件35 passed（包含普通NO_WRITE月报不变、scope/typed/澄清/引用/额外写后缀负控、实际Envelope污点与来源、fresh跨年/闰月区间、四个依赖失败点、402/403/429传播、缺失/错范围卡片负控）。联合以下命令共93 passed：

`.venv/bin/pytest -q tests/agent/test_cross_scene_gather.py tests/agent/test_bill_accuracy.py tests/agent/test_cards.py tests/agent/test_card_map.py tests/agent/test_current_date.py`

相关ruff check和format通过。以上为纯内存/假模型测试，不是X1真实演示或实际降限额验收。

## X2/X3/X4静查（后续补充）

X2已有test_x2_sources/test_idle_subscription_plan/test_subs_bills_prompt、L2 derived/predicate及禁止外部收款目标测试；X3已有多稳健产品澄清与L2预留派生/适当性谓词测试；X4已有L2两动作作用域/可疑卡谓词测试。L3通用多步骤失败回滚由tests/integration/test_execution.py覆盖（本任务未运行PG）；这些均不能替代各完整生产gather→计划→审批→原子执行用例。按协调者指令继续用早停模型核对X4/X3的实际必读遗漏，并独立提交证据。

### X1规划提示的规格歧义

按协调者追加授权，plan.py中原“账单分析类问题一律不生成写计划”的笼统提示收窄为单独只读分析；已有SCOPED混合请求保留原授权写步骤并先完成真实分析读取。仅修改提示规格文案，不改planner状态/来源校验/授权或响应值。首轮联合X1、原订阅账单与plan e2e修复回归为1 failed / 41 passed：原测试依赖旧短语“账单分析类问题”。保留该短语并增加“单独的”限定，重新验证结果另记；没有降低既有断言，也没有生产LLM验证。

提示修正后复跑上述3文件：42 passed，ruff通过；首轮失败与修复均保留记录。

## X4：可疑卡冻结 + 储蓄至活期应急互转

早停模型只读取get_cards，understanding使用合法freeze_card/transfer_self意图时，原补读不含get_accounts与detect_anomalies；实际双步骤计划的from_account_id/to_account_id来源不存在，PlanAdapter抛ReferenceError。这是实际读取依赖遗漏，不是授权或L3原子性改变。

修复仅接受单条原始typed、SCOPED、needs_write=true且无实际澄清/echoes_untrusted的完整“可疑交易对应卡冻结，再从本人储蓄转明确金额到活期应急”句式。fresh get_profile后读取detect_anomalies/get_cards/get_accounts；异常区间严格对应G3 suspicious_cards的“上一自然月月初→当前银行业务时间”，不是仅上月，也不是滚动30天。L3 load_bank_state将业务时间归一到Asia/Shanghai，该实现与G3实际predicate_values代码运行输出已用内存快照对比。所有实际Envelope来源/版本/时刻/污点继续保留；查询不过滤或自行选择卡、账户，不改用户金额和L2 targets，不返回写执行结果。

红测：X4最初3项全部失败（两个时间窗/来源缺失，以及合法双步骤计划ReferenceError）。修复后3项通过，补充原话kind、引号、额外外部转账后缀、scope/实际澄清、四个依赖错误及不自动选择多卡负控。G3 oracle首轮夹具缺少predicate_values最后用于版本复查的snapshot，1 failed / 139 passed；补全纯内存snapshot后联合以下6文件140 passed：

`.venv/bin/pytest -q tests/agent/test_cross_scene_x4_gather.py tests/agent/test_cross_scene_gather.py tests/agent/test_llm_adapters.py tests/agent/test_lost_card_sources.py tests/gateway/w1/test_predicates.py tests/gateway/w1/test_parser_coverage_c.py`

相关ruff与diff检查通过。只证明只读前提与本地来源校验；完整X4实际审批、转账、卡状态及回滚仍未由本任务运行。

## X3-A：G20选择后结构化上下文

确凿缺口：G3产品澄清完成后，G20当前utterance为ACTION且text=null；ingest只把已签发scope保存在state.hints、parent链保存在parent_ids，原理解/规划提示均未消费它，不能依赖空文本重建用户选择。现仅允许运行时customer/sid一致、单个当前SCOPED ACTION、非空实际G20 parent链、无实际澄清/echo、唯一同id/kind scope、FUND_SUBSCRIBE和available_less_7d_charges及单个合法product_id的上下文。白名单只包含当前utterance_id、parent IDs、action_kinds、product_id/account_id/derived_template；scope的任意amount、memo、额外产品列表不传入上下文。

理解与规划提示传这份结构化选择，保持ACTION原text为空，不伪造typed原话。理解误判只读时沿原max_retries有界反馈重生成，最终仍使用模型返回值；规划忽略当前选择而选其他真实产品/账户时同样反馈重生成，不替换模型字段。实际read来源、derived计算和L2最后授权/适当性/确认保持原路径。未完成澄清、错customer/sid、任意hint/typed/context附加scope、旧ref、重复选择、缺产品/多产品均不获得这份上下文。

红测：基于真实G20 text=null形状的理解/规划/有界重生成最初3 failed / 5 passed；加入当前选择上下文后8 passed。再用两个均有真实读来源的产品重现“模型忽略当前选择”，1 failed / 23 passed；补选择一致性反馈后24 passed。联合原AA理解、LLM adapter、理财、X2派生和闲置订阅回归共131 passed：

`.venv/bin/pytest -q tests/agent/test_selection_context.py tests/agent/test_aa_understanding.py tests/agent/test_llm_adapters.py tests/agent/test_funds_prompt.py tests/agent/test_x2_sources.py tests/agent/test_idle_subscription_plan.py`

包含402/403/429单次调用止步、scope金额99999999被忽略且金额仍来自原derived计划的负控。ruff通过。该单元只闭合结构化上下文；X3必要读取已另有2项红测，留下一独立单元补足，不把X3生产整体标完成。

## X3-B：选后实际读取与派生计划

先用真实G20选择后形状和早停模型复现两项失败：没有账户/7天待扣/有效测评/选定产品读证据，合法派生申购计划的账户与产品read ref不存在。修复只在X3-A绑定上下文成立、既有understanding.needs_write=true时，通过实际读取保留fresh get_profile、get_accounts、upcoming_charges(days=7)、get_risk_profile与G3已选择的get_product；不选产品、不计算金额、不把无效测评改成有效、不更改understanding、JWT或授权。错产品响应直接失败，不读取其他产品替代。

原2红转绿；追加原clarification/NO_WRITE/只读理解、五个依赖失败点、402/403/429单次止步、失效测评原样保留、错产品与任意scope金额不入金融值负控，18项通过。补编译后的build_gateway_graph实际G20 ingest→UnderstandAdapter→GatherAdapter→PlanAdapter→红级REQUIRE_APPROVAL中断用例；首轮仅测试误把decision字典当对象造成1 failed / 18 passed，按真实状态形状修正后断言原ACTION/parent链、5份实际读取、只当前utterance入计划、正确产品和等待审批，绝无执行。

最终在当前全部L1源码上运行：`.venv/bin/pytest -q tests/agent -m 'not db'`，**859 passed / 40 deselected**（8.45秒）。40项为明确排除的数据库测试，不算通过。相关G3/W2派生/测评纯测试另联合127 passed（是重叠子集，不相加）。本轮全部修改的Python文件ruff check/format及git diff --check通过。

上述是实际编译图和假上游/假模型纯测试，未调用生产模型、真实服务API、PG、Docker或浏览器；不能据此宣称X3生产演示、真实适当性授权与资金预留/结算已验收。
