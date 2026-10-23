# C1b：DeepSeek strict 理解/收集/计划适配（2026-10-24）

前置：C1a 已合 PR #17；基线 main eea02a6。只实施本组件，C1c gateway HTTP/路由、C1d PG/服务、C1e回复校验/回放另做。实现与独审均原本机 DeepSeek CLI，前端/BFF暂停。

## 输入、输出与所有权

输入为 C1a 的本轮可信身份/已登记 utterance 引用、结构化 reads/Decision 摘要，以及显式注入的只读工具端口。输出为经本地契约校验的 Understanding / reads / PlanDraft；不执行任何写工具、不授权。提供适配 C1a Ports 的 understand/gather/plan 实现。真实 gateway 尚未完成部分必须保持显式端口，不发假成功。

允许 services/agent/app/llm/**、必要的 services/agent/app/graph/state.py 或 ports.py 中本地数据适配（不改图路由/身份guard/invoke边界；如必须改先报告），services/agent/README.md、tests/agent/**、docs/progress/C1b-deepseek.md。共享 contracts/schema、L2/L3/bankcore、Makefile/CI/compose/依赖锁/AGENTS/PRD冻结；缺共享能力先报告，不能复制另一套计划 schema。

先完整读 AGENTS.md、PRD §6/§9.3/§9.5、ADR-002、C1-agent.md、C1a验收/最终评审、packages/contracts/schema.py 与 plan.py/tools.py，以及现有 scripts/probe_contracts.py 和 P0 adapter tests。C1a Understanding/ReadRecord 为本地骨架占位形状；需要扩展时只做本组件最小变更并保留 C1a 22项回归，不扩写 C1c。

## 必须实现与验证

- 固定 deepseek-v4-pro、显式 https://api.deepseek.com/beta，thinking.type=disabled、temperature=0，沿用 ADR-002。优先复用当前已装 ChatDeepSeek/官方兼容客户端与现有生产 schema；无必要不造 SDK。不得改模型或开启思考/Flash，不为测试改 strict。API key/助手JWT/服务令牌只在运行配置或客户端对象，绝不入state/configurable/checkpoint/提示/结果/日志。
- understand 单个 report_understanding strict 工具；结构按 PRD slots数组、禁止自由键映射。plan 复用现有 plan_tool/from_strict_plan 和 PlanBody/PlanDraft；13变体、哨兵/实体引用逆转换，不含 W12 问卷。request_id/session_id/utterance_ids 由代码从本轮真实身份/非context原话填，不接受模型自填。未知/重复/多tool calls、坏JSON、Schema或契约错误不静默接受。
- understand/plan 每次新建 system+human 请求，无历史 assistant。仅验证失败重试一次，第二次仍错明确失败；网络/API错误有界，不吞错误/无限重试，不把错误原文中的密钥回显。新请求只附精简验证摘要，不能污染重试历史。所有 *_wall 字段递归剔除，金额整数分、份额Decimal不经float。
- gather 只绑定18个 READ_ARGS 所声明的read工具，经显式只读端口执行，工具参数由对应契约验证；最多3轮，拒绝未知/写工具/多步绕过，达到上限给明确结果/失败。返回结构化reads，不把自由文本授权；untrusted字段在模型输入中使用带source标记的封装并正确转义边界/闭标签，不能提升信任。模型提议工具名/参数不等于执行授权。tool消息只由合法结果生成；关闭思考不声称实现了开启思考 reasoning_content 回传。
- mock transport/客户端测试必须检查真实最终HTTP payload（endpoint、thinking/temperature、strict/tool_choice、生产工具schema、身份字段不入LLM、无wall/密钥、重试次数/新消息历史、只读上限及故障）。新增测试先实际红再绿，真实LangGraph+C1a ports贯穿至少一例；不只测试mock本身。
- 最小live在本地显式手动执行，离线CI不需要密钥/网络。提供可复跑的少量live入口（最多理解2+计划3+gather1个合成场景，总请求上限8，含重试），安全读取环境/项目既有.env中的DEEPSEEK_API_KEY但绝不打印文件/headers/key，证据只存脱敏合成输入/工具schema哈希/响应校验/usage/延迟。不运行旧330请求probe。协调者会亲自复跑，worker不能把未跑live写通过；API失败只报脱敏错误，不换模型。禁止记录真实个人数据。

## 退出与交付

make lint；pytest tests/agent -q（零skip/xfail，live单独显式入口不混skip掩盖）；make test；make contracts-check；actionlint由协调者亲跑。进度写每条红绿、实际命令、fake/真实LLM边界、未覆盖与待决。仅本地提交，末尾 Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>；停止等待协调者亲验/独审。单条shell命令不用 ;/&&；不push/PR/merge/派agent/全栈/docker卷操作，不读取打印settings.json或.env/.secrets内容。
