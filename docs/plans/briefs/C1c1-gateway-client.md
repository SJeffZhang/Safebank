# C1c1 Gateway Client Implementation Plan

> 执行方式已由用户指定：本机Claude Code CLI（DeepSeek V4 Pro）逐任务实施，另一个独立DeepSeek CLI评审；协调者不编业务代码、不用自身subagent代替。用户已选择自主推进，技能默认再次确认/子agent方式不适用。

**Goal:** 将PRD G20/G21/G2/G4/G8的HTTP边界实现为可复用、严格验证、无隐式重试的L1客户端。

**Architecture:** 复用现有httpx同步调用与运行时AgentContext；A服务令牌仅客户端配置，assistant JWT每次由context取。只连配置的L2，不导入gateway服务代码。C1c拆为本组件客户端/协议与下一组件C1c2完整LangGraph路由，分别可独审/验收。

**Tech Stack:** 当前锁定Python3.12、httpx、Pydantic与safebank_contracts；不改依赖。

**Spec:** PRD §3.2/6.1/6.1.1/9.1/9.3/9.5；C1-agent.md；C1b验收。读实际gateway schemas/read.py、scope.py、decision.py、api/sessions.py、api/tools.py、errors.py与auth/deps.py作协议事实，禁止从其内部import。

## 设计取舍与范围

完整C1c一次同时改客户端/占位state/图路由会混合协议与控制流验收；先做HTTP/wire独立边界，再由C1c2接入原图，是协调者根据既有组件门禁的拆分选择，未改架构。新共享contracts会破坏冻结v1，本次采用L1内独立wire模型（下一步state通过适配或复用此模型），不动共享包。L2 G4/G8当前未落地，测试使用HTTP模拟端并明确标fake；不能称真实三层联调。G20/G21与G2已存在，协议按实码核对，不由LLM猜。

## Global Constraints

- 只写services/agent/app/gateway/**、tests/agent/gateway/**、services/agent/README.md和docs/progress/C1c1-gateway-client.md；graph/**、llm/**、gateway服务、contracts、bankcore、L3、依赖/Makefile/CI/compose/AGENTS/PRD冻结。
- 不实现G3原话创建、G6确认/OTP或任何F/D接口；不持F/D/S/GATEWAY_SERVICE_TOKEN/银行DB凭据。不得直接L3/DB访问。
- 金额严格整数分，Decimal份额按契约；授权只L2决定，客户端不能生成ALLOW或把失败当成功。
- 无默认依赖/成功替身；有限timeout、follow_redirects=False，不自动重试evaluate/execute，不自动查询UNKNOWN、不新建plan代替UNKNOWN。
- 所有凭证只客户端私有配置/context；不得出现在模型、AgentState、异常repr、响应/错误details、日志中。不要回显原始httpx异常request/header或服务端任意message。
- 不读.env/.secrets/用户真实数据；不live、不push/PR/merge/派agent、不全栈/卷操作；本地红绿与小提交。前端/BFF暂停。

## Review Focus

1. 相同客户端两session切换：JWT必须逐调用取context、无跨客户/session串用；body sid与context不符本地零HTTP。
2. 合法hint/action原话text可能null，完整wire字段不能被占位模型extra-forbid误拒，也不能将其视typed授权。
3. 畸形/额外/未知枚举/非整数金额和成功状态缺plan绑定字段：必须协议失败，无返回成功。
4. 409 STATE_CHANGED/POLICY_CHANGED内details.decision完整保留且绑定原plan；202 UNKNOWN不轮询/重提。
5. 超时、断网、非JSON、302与服务端异常文本含测试secret：保留可路由的固定错误类别/HTTP状态，不泄凭证或擅定资金失败。

### Task 1: Wire与只读调用

Files: 新建services/agent/app/gateway/{__init__,wire,client,errors}.py；tests/agent/gateway/test_client_reads.py、test_wire.py。

Interfaces（类名可依已有规范微调，提交前进度准确登记；不改外部graph接口）:
- GatewayClient(base_url: str, service_token: str, *, http_client: httpx.Client | None = None, timeout: float = 10.0)。service_token是A，不能从state取。
- get_utterances(utterance_ids: tuple[str,...], context: AgentContext) -> tuple[GatewayUtterance,...]，G20按context.session_id、query ids；返回ids精确匹配请求且不能重复/漏项/跨请求。
- get_clarification(context: AgentContext) -> GatewayClarification | None，G21完整候选/partial_scope/期限；当前parent归属/进入clarify留C1c2。
- read_tool(tool: str, args: dict, context: AgentContext) -> GatewayReadEnvelope；G2仅18个READ_ARGS，用原契约验证必填（history_days不可补猜），验证对应READ_RESULTS；保留source_ref/untrusted_paths/versions/observed_at和原始data，不由客户端重写来源。下一组件再桥接现有Gather ReadExecutor。

- [ ] 写红测：X-Service-Token=A与Authorization=Bearer context.assistant_token、正确路径/参数/body；两context用同client不串JWT；缺凭证/sid不符/未知或写tool零HTTP；G20缺/多/重复id拒；hint/action text=null保留；G21 null和完整候选；ReadEnvelope字段不丢，18个工具生产参数/结果schema校验。
- [ ] 跑.venv/bin/pytest tests/agent/gateway/test_client_reads.py tests/agent/gateway/test_wire.py -q，确认因组件未实现失败，不是环境错误。
- [ ] 最小实现wire/types和上述只读方法；path各段编码，工具名只白名单，不接受用户URL。
- [ ] 重跑以上回归转绿；本地小提交。

### Task 2: 评估/执行与错误边界

Files: 修改gateway/{client,wire,errors}.py；新建tests/agent/gateway/test_client_write_paths.py、test_errors.py。

Interfaces:
- evaluate(draft: PlanDraft, context: AgentContext) -> GatewayDecision：POST /plans/evaluate仅内部PlanDraft格式、sid及utterance_ids原样；禁止extra origin/auth字段。
- execute(plan_id: str, request_id: str, context: AgentContext) -> ExecutionResult | UnknownExecution：POST /plans/{id}/execute body仅request_id，不带steps/approval_id/OTP。
- GatewayResponseError：只可供C1c2路由的status、Code、固定安全类别、必要且经wire验证的details.decision；未知code或畸形JSON用安全protocol异常，不猜成功。保留execute网络结果不确定性类别，路由/模板留C1c2。
- GatewayDecision遵从全部字段（含finding effect/evidence_refs/resolver、preflight、risk、evaluation_id/revision、replan_hints、versions、clock_version、policy_version、shadow_b1）；复用冻结ExecutionResult，不用C1a最小Decision删字段。严校plan关联；错误409新decision的plan_id必须与execute目标一致。

- [ ] 红测：ALLOW/REQUIRE_APPROVAL/DENY/REPLAN完整wire；evaluate sid不符零请求；execute仅request_id、返回plan_id错拒；202 UNKNOWN恰1HTTP无后续GET/POST；409新decision完整/错误plan拒；422/429/401/403/404/409审批错误/503保留status+Code；非JSON/302/超时/断网以及含credential测试文本不泄密不成功。金额float/bool/string拒。
- [ ] 跑具体失败用例，记录实际红。
- [ ] 最小实现，全部HTTP不重试、不轮询，无状态或授权判定逻辑；跑定点绿并小提交。

### Task 3: 组件验收

- [ ] make lint、.venv/bin/pytest tests/agent -q（零skip/xfail）、make test、make contracts-check全部实际跑。
- [ ] README/进度注明MockTransport属于fake、G4/G8尚未真联调；记录接口/红绿/命令/剩余C1c2路由与C1d/e边界。
- [ ] 本地commit，Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>；停止冻结交协调者亲验/独审，不自行发布或开始C1c2。

自审：本计划只覆盖C1c协议子组件；PRD6.1.1逐行路由、ingest接线、PG saver/SSE/输出与真实三层闭环均明确后续，不宣称已完成。无新模型/金额规则/架构/权限决定。
