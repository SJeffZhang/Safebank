# C1：L1 Banking Agent 并行流（2026-10-24 用户确认）

依据 PRD §6、§9.5、§13 流 C、ADR-002。实现与评审由本机 Claude Code CLI 的 DeepSeek V4 Pro worker 执行，协调者只写任务书、验收与发布。前端/BFF继续暂停。每个小组件完成即冻结、协调者亲验、独立审、PR/当前head四CI成功后合并，才能进入下一项；不得一次实现全部。

## 边界与公共约定

- 业务/测试只可改 `services/agent/**` 与 `tests/agent/**`；另允许本任务的 `docs/progress/C1a-agent.md`。共享contracts、gateway、bankcore、L3、Makefile、compose、依赖锁、AGENTS/PRD均冻结。缺接口或依赖先记待决，由协调者另派共享组件，不自行越界。
- 读取完整AGENTS、PRD §3.2/6/9.1/9.5/11/13及ADR-002、现有tests/test_langgraph.py和contracts。不导入gateway内部代码，不向L3或银行DB发请求。L1只有A令牌、助手JWT、公钥与agent库权限，不能持F/D/S/写服务凭证。
- 助手JWT、服务令牌、LLM API key只放运行时context或客户端配置；不得进AgentState、configurable、checkpoint、提示、SSE或日志。日志只记录合成标识/错误code。
- 单条shell命令，不用 `;` 或 `&&` 串联。禁止push、全栈make up/down/test-p1*/test-integration、删除/重建Docker卷、reset/TRUNCATE现有业务库。临时PG也不能共享长期数据。
- 每组件先编写会失败的行为测试，记录实际红测命令/结果后实现，再绿测。报告记录fake/内存/PG/live边界；fake不算真实G3/G4/G8联调。业务失败、UNKNOWN、依赖缺失不可伪装成功。
- 本地提交，提交信息末尾加 `Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>`。进度写指定组件文件，最终回复简短：SHA、实际命令/结果、未覆盖和待决。遇到冲突停在明确检查点。

## 组件顺序与独立门禁

| 组件 | 输入/输出与验收 | 依赖/禁止范围 |
|---|---|---|
| C1a 图骨架与AgentState | 真实LangGraph StateGraph与Runtime context；ingest→understand/gather/plan/evaluate/await_approval/execute/respond/verify_output骨架，端口注入；重置本轮字段、两客户/两sid隔离、interrupt同值/错值resume、新input替代旧interrupt。内存saver可测，不宣称PG持久化或生产银行图完成。 | 首次只做本行；外部端口必须由调用方显式注入，缺依赖fail-closed，不能默认成功。 |
| C1b DeepSeek适配与strict计划 | 固定deepseek-v4-pro、显式/beta、thinking disabled、temperature=0；真实contracts schema和反向转换；understand/plan强制单工具、无历史assistant新请求与一次校验重试；gather只读工具最多3轮/untrusted封装；代码填request/sid/utts，排除W12。Mock请求断言+最小live验证需单独记录。 | C1a已合，先任务书细化；Flash仅图片且抽取untrusted，不新增多模态功能。 |
| C1c gateway客户端与路由 | 经G20/G21 ingest、G2读取、G4 evaluate、G8 execute；PRD6.1.1逐行覆盖，REPLAN只按evidence_refs定向补读且次数有界，STATE_CHANGED使用details.decision不重复evaluate；UNKNOWN不轮询/不新提交。 | C1a/b；未完成L2接口用PRD契约fake，真实联调单列。|
| C1d 持久化、审批与服务入口 | AsyncPostgresSaver/setup、真实临时agent PG全表搜JWT为零、thread运行锁、PRD6.5三个HTTP入口/SSE；interrupt前无副作用，resume仅ID相同也须G8由L2验证审批，新input显式丢弃旧interrupt。 | 客户端/路由已合；PG fixture仅独立tmpfs。共享compose/deps运行接线另组件，不默认健康探针已替换。|
| C1e 回复校验、卡片与回放 | PRD6.7数字来源/长数字掩码/确定性cards与6.6 live/record/replay，ID规范化双向映射、wall字段剔除、profile/case区分、stale/miss不降live；ADR-004。 | 前序门禁后细化；录制来自真实模型，fake回放不冒充demo；共享ADR由协调者记录。|

## 当前派发：只做 C1a

输入：经过边界适配的本轮身份/原话引用，以及显式注入的节点依赖。输出：可通过真实LangGraph内存checkpointer运行和中断恢复的图，不开放生产HTTP入口、不调用live LLM、不调用银行后端。

- AgentState按PRD6.1建模，消息只存展示用user/assistant文字；重置所有本轮字段。外部token放 `AgentContext`，Runtime读取。新input与resume路径区别清楚，resume不能再次ingest清空approval。
- 各节点通过小且明确的类型化端口接入（后续由C1b/c实现）；不要为尚未实现的节点返回“执行成功”占位。未提供端口须明确错误。禁止构建平行自制状态机替代LangGraph。
- C1a只验证骨架控制流、依赖注入、状态/身份隔离、中断语义；完整返回码表留C1c。不把stub决策当L2授权，不自动确认或生成OTP。
- 必测：同thread第二条新消息清旧reads/plan/decision/count/approval/execution/reply；两sid及两customer不能串状态；同thread跨customer拒绝；正确resume继续、错resume不触发execute；新input在pending interrupt时从START重新开始且旧resume不能执行旧计划；interrupt之前端口调用计数不重复；Runtime密钥不出现在内存checkpoint或公开输出；缺端口失败且无execute副作用。
- 若LangGraph原生新input行为不满足旧interrupt清除，先证明红测并在ingest适配，不删断言。
- 退出：`make lint`、`.venv/bin/pytest tests/agent -q`（零skip）、`make test`、`make contracts-check`；worker可以跑以上命令。本组件无PG，不能宣称Postgres saver已验证。
- 文档：更新 `services/agent/README.md` 明确骨架可用范围/运行方式/生产入口仍为探针；进度写 `docs/progress/C1a-agent.md`，记录精确红绿、退出命令与未覆盖。完成后本地提交并停止，等待协调者验收。

C1a输入边界增量（8ed1f7c，最终独审APPROVE）：LangGraph会将缺key的新input合并旧state，故C1d的新输入服务必须调用唯一invoke_new_input（或等价经独立审查的异步入口），不能把裸graph.invoke({})当新消息。resume前必须维持持久customer/session与Runtime/config绑定，不能因为HTTP层也检查而删除图内guard。
