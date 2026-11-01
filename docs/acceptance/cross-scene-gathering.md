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
