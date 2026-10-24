# C1c2 Gateway Graph Routing Implementation Plan

> 用户已指定本机Claude Code CLI / DeepSeek V4 Pro实现与独立评审并授权自主推进；协调者只任务/文档/亲验/发布，不编业务代码。按此方式逐任务红绿，不用自身subagent，不再次等确认。

**Goal:** 将已合C1c1客户端接入真实LangGraph的ingest/read/evaluate/execute与PRD6.1.1有界路由，保留完整L2事实且不制造授权。

**Architecture:** 一个C1c2组件负责控制流与客户端适配。现有注入式骨架的明确fake测试可保留；新增显式生产组合入口必须依赖GatewayClient，不能默认为fake或信任调用方传来的text/scope。G4/G8仍未在L2实现，本组件HTTP模拟验证明确标fake；真实三层闭环须后续另验。C1d PG/服务/SSE与C1e完整输出/卡片/回放不做。

**Tech Stack:** 现有Python3.12、LangGraph、Pydantic、httpx与冻结contracts，不改依赖。

**Spec:** PRD3.2/6.1/6.1.1/6.2/6.6/9.5；C1-agent.md；C1c1任务/验收；真实gateway pipeline/entity.py与taint/entities.py中evidence_refs为读工具名的既有协议。

## Global Constraints / ownership

可写services/agent/app/graph/**、新增services/agent/app/gateway/adapters.py、gateway/__init__.py仅必要导出/import-cycle调整、llm/{gather,understand,plan}.py及llm/__init__.py仅context/定向读/nullable原话兼容、tests/agent/**、README与docs/progress/C1c2-gateway-routing.md。C1c1 client.py/wire.py/errors.py、模型schema/redact/client/live、gateway服务、shared contracts/bankcore/L3、依赖/Makefile/CI/compose、AGENTS/PRD冻结。跨边界缺口先报告，不擅自扩大。

只调用L2 A+runtime assistant JWT，无F/D/S/L3/DB凭据；服务token/JWT/API key不进AgentState、configurable、checkpoint、提示、异常和日志。两context共用同client不能捕获/缓存旧JWT。图不能生成ALLOW、审批、OTP或改用户金额/目标。不调用G3/G6/F/D/G5轮询。有限循环，UNKNOWN不重提/不新plan。禁止live/全栈/长期卷/reset/push/PR/merge/派agent。

## Design / interfaces

- 保留显式Ports/build_graph/invoke_new_input兼容既有C1a/b测试；新增make_gateway_ports(client: GatewayClient, *, understand, gather, plan, respond, verify_output)->Ports（或同语义清楚命名）与invoke_gateway_input(graph, utterance_ids: tuple[str,...], *, config, context)。生产入口仅接受ids，G20权威返回替代传入text/scope；原fake入口不得被生产组合误用。最终签名/使用方式写README，保证没有隐式成功默认。
- AgentState完整保留G20原话/parse_status/parent_ids/scope/echoes、G21 clarification、GatewayDecision全部字段/UnknownExecution及安全固定route/error字段；不要把完整Decision降成旧占位丢risk/eval/revision/preflight/versions/hints。避免gateway包导入client→graph.state循环，不能靠删验证或仅测试导入顺序掩盖。明确fake旧Decision只留骨架测试，生产仅GatewayDecision。
- ingest始终thread/context身份检查，然后G20/G21；本轮清reads/plan/decision/counters/approval/execution/reply及新增字段，resume不重新ingest。OPEN clarification的parent必须属于本轮G20 ids才clarify；parent不匹配不沿用，过期以L2结果为准。clarify生成固定补充说明/保存L2 ids与required_fields/candidates，不能授权或自行选候选。
- hint/action text=null必须保留kind/id/scope，不能伪造typed原话或span。LLM仅看到标注L2注册的结构化hint/action事实（去*_wall），来源应保持hint/action；旧文本拼接/字符索引须兼容null。context原话排除PlanDraft.utterance_ids。INTENT_UNCLEAR且needs_write使用G20权威parse_status，不能仅看LLM.unclear而进入plan。未识别kind安全拒绝，不洗成typed。
- Gather桥接每次call的context到client.read_tool；ReadEnvelope→ReadResult/ReadRecord保留source_ref/untrusted_paths/versions/observed_at。沿用且只做一次strip_wall/untrusted wrapping。不得存原始未包裹数据给提示。正常gather仍18工具/至多3轮。
- REPLAN只有非空findings且全部resolver=agent、replan_count<2才再gather→plan；此轮只开放evidence_refs中精确匹配READ_ARGS的读工具，最多3次实际read（不是3轮×无限并行）；没有合法读工具/所需参数无法合法获得则结束要求用户，不猜参数/不开放全工具。参数仍由现有strict gather提案验证；允许工具外的call整个批次零HTTP。保留旧reads并按source_ref合并，不能丢上轮引用。replan_count每次自动修订只加1；422 CONTRACT_INVALID→plan同预算，两个来源共用上限，不因gather重入重置。
- execute使用已绑定plan_id与每次调用的代码request_id（不带approval/OTP/steps），409 STATE_CHANGED/POLICY_CHANGED中同plan新decision直接路由，不evaluate；该类复演本轮最多1次，更新approval_id并清旧值，超过结束。ALLOW只允许此有界重入，不自动重试transport。
- 安全route_error只保存固定status/code/phase/category；不保存Exception/httpx或任意message/details。execute的202/503/transport/无法确认结果的protocol失败保留原plan_id并标UNKNOWN，固定“确认中”，不宣称未执行。evaluate/ingest/read的503/transport不产生execute；明确未执行仅在尚未发出execute的路径。拒绝/过期/used/locked/cancelled等按PRD固定结果模板；ExecutionResult FAILED不能显示成功。C1e之前允许注入respond/verify，但路由错误/UNKNOWN的固定模板由代码兜底，不能让自定义LLM改为成功。完整PLAN_STATUS/BLOCKED卡片推送仍留C1e，不假称已做。

## Review Focus

1. G20权威数据替代伪造input/null hint不会变typed；OPEN clarification跨轮parent不串。
2. 两客户/session、新input替旧interrupt与错resume仍零execute；完整state/checkpoint搜索合成token为零。
3. REPLAN读工具白名单+3调用上限与422共享2次预算，批次越界不能部分发请求；用户resolver不自动改金额。
4. STATE_CHANGED不evaluate、只一次重入、新审批替旧id；UNKNOWN无额外HTTP，网络不确定不能显示资金失败。
5. import冷启动与C1a/b/C1c1全回归，L2 wire事实不丢，不因MockTransport绿冒充真实G4/G8。

### Task 1: Authoritative ingest and state

Files: graph/{state,ports,graph,invoke,__init__}.py；gateway/adapters.py；必要llm兼容；tests/agent/routing/test_ingest.py。

- [ ] 写红测test_ingest_uses_g20_not_input_text、test_null_hint_keeps_kind_scope、test_clarification_requires_current_parent、test_gateway_parse_unclear_blocks_plan、test_ingest_two_contexts_no_token_leak：断言G20/G21各调用、仅L2事实、null可运行、不该执行时HTTP evaluate/execute=0、冷进程两个import顺序成功。
- [ ] .venv/bin/pytest tests/agent/routing/test_ingest.py -q，记录因未实现的真实红。
- [ ] 实现上述签名与完整状态适配，保留原身份/新input/resume门禁；不接G3/F。
- [ ] 定点绿及既有tests/agent/test_graph.py/test_llm_graph.py绿，本地commit。

### Task 2: Context-safe reads and bounded replan

Files: gateway/adapters.py、llm/gather.py、graph路由；tests/agent/routing/test_reads_replan.py。

- [ ] 红测：真实MockTransport G2+scripted strictLLM经过图保留source metadata/包裹；同client两个context逐次JWT；REPLAN list_payees只调用该读、4调用批次零HTTP/总最多3、未知证据不开放、user resolver零自动补读、REPLAN2次后停止、422与REPLAN混合仍最多2次、旧read引用保留且用户金额不被代码改写。
- [ ] 跑该文件确认红，实现最小有界参数与桥接，不改LLM schema/model；定点绿，小提交。

### Task 3: Decision/execute routes and uncertainty

Files: graph/graph.py及必要纯routing.py；tests/agent/routing/test_routes.py/test_resume.py。

- [ ] 对PRD6.1.1每行参数化红测：ALLOW/REQUIRE_APPROVAL/DENY/REPLAN、422、授权过期/401/403/404/429、409各审批错误、executeFAILED/202/503/transport/protocol、STATE/POLICY_CHANGED四decision分支及第二次拒，所有断言HTTP调用次数/plan_id/approval_id/计数/固定回复而不只节点名。
- [ ] 新审批interrupt与旧resume错值不发G8；正确ID也仅G8由L2确认，不能当L1批准；UNKNOWN无poll/re-evaluate/newplan，重开新input不沿用旧execution。
- [ ] 跑定点真实红后最小实现，再定点绿与小提交。

### Task 4: Component acceptance and freeze

- [ ] make lint、.venv/bin/pytest tests/agent -q（零skip/xfail）、make test、make contracts-check全跑；make demo当前明确未实施exit2如实登记，不能改成fake成功。
- [ ] README/进度记录精确interfaces、逐行路由覆盖、红绿/命令/HTTP fake边界；G20/G21/G2真服务集成仍待协调者，G4/G8当前不可用；C1dPG/SSE/C1e未做。无live不自称真实模型闭环。
- [ ] 本地commit署名DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>，冻结回SHA与结果，停止等协调者亲验/独审，不开始C1d。

自审：这是既定C1c的控制流子组件；未改共享协议/架构/模型/整数金额。P2所有真实三层、确认、数据库/网络、PG恢复、输出/回放退出仍待后续，不能标P2完成。
