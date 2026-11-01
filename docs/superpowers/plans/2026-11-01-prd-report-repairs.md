# PRD正式43句复验报告开发修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans for the assigned independent unit.

**Goal:** 修复aa7e5d2复验报告8 FAIL与16 PARTIAL中的已证实产品问题，保留原始43句/19-16-8结果供用户另会话重新演示。
**Architecture:** 使用现有L1→L2→L3与冻结工具契约，不重新选型；只修读取、提案、确定性来源、UI契约和执行归档。
**Tech Stack:** Python3.12/FastAPI/LangGraph/PostgreSQL；React/TypeScript/Vite。
**Spec:** PRD §§5–9；docs/reviews/prd-demo-report-2026-11-01/原始报告/CSV/覆盖矩阵及sha256。

## Global Constraints

- 三个Codex子agent独立worktree；root独占共享AGENTS/PRD、真实PG、单栈部署/完整CI，不使用DS worker。
- 不运行浏览器演示、生产LLM或录制/断网彩排；402/403/429立即停止相关请求，不重试不换模型。
- 不删除/reset旧演示库、卷、worktree/WIP。原demo_20261101084917_8e1ed563已改变，不作为新初态。
- 金额整数分、份额Decimal；不降低来源/JWT/epoch/审批/OTP/单次父链与金融约束，不自动猜用户金额/目标。
- 原始失败与未现场覆盖分开，原生补测不替代助手原句。独立红绿、资金安全只读审查、完整40位head CI PASSED后按head合并。

## Review Focus

- readonly原句应NO_WRITE且不带空FORM_REQUIRED，混合写/疑问/未知账号不能借只读语法授写。
- X3 G21父原话→G19→G3 action严格契约；跨会话/缺父/失效/重复不猜，不自动确认或交易。
- 读取失败/校验拒绝不能当成成功read；未知持仓/产品不得用其他数据替代；修正预算固定一次。
- 摘要/只读回复不截掉关键产品或交易，不把历史日当下次、不将UTC日当上海业务日，不向UI暴露内部包装/校验值。
- 取消grant只在已提交终态归档原子撤销，UNKNOWN恢复/orphan同规则，缺grant/懒恢复竞争不复活，错误证据继续拒绝。

## Task 1 — X3/跨轮澄清与双月界面（C）

Files: frontend/src/mobile/Assistant.tsx, types.ts, bill-month.ts, AssistantWorkspace.tsx及独立FE tests。
- [x] 真实wire旧fail红测：G19有确切parent；G3 action无非法clarification_id；typed仅SLOTS补答；FORM不污染新消息。
- [x] 最小修复并加入缺parent/跨会话/重复点击/失败保守负控；专项后提交第一SHA。
- [x] 14双月卡按各自实际月份展示、请求主月正确内嵌；16/35保留。第二独立SHA。
- [x] 真实错误文案/提示上限需和root/B实际响应证据接线；最终FE/BFF/build与独审。

## Task 2 — 读取摘要与必要查询（A）

Files: services/agent/app/llm/gather.py, respond.py, 私有read helper及tests/agent；L3 state.py/reads.py另单元。
- [x] 旧fail：6产品30日NAV不丢后排、交易不整块丢弃、卡剩余限额、扣费/定时/业务日期、闲置下次与持仓名称。
- [x] 保留每个数值真实语义与来源的有界摘要，去内部包装，不放宽verify；提交可独审小单元。
- [x] 13商户消费明确范围/全量分页/正确merchant、32真实compare、34必要成功持仓读取（B提供失败场景），402/403/429不追加调用。
- [x] 12以可复现读取/回复/校验节点补权威月消费来源并加编译图回归；原现场唯一触发缺轨迹，明确保留未知，不删数字/注入校验。
- [x] 34b trade_date按实际PG业务时区roundtrip；实际L3 list_products按规格不携30点NAV。独立红绿/PG由root。

## Task 3 — X4提案与金额上限（B）

Files: services/agent/app/llm/plan.py及私有plan helper/tests；pipeline/preflight.py/decision.py后单元。
- [x] 正式37与真实strictschema重现tagged to_account错误；既有ValueError retry不能重复算新修复。
- [x] 只修有证据且已读来源绑定的结构表示，未知/跨目标/坏来源拒绝，不猜实体、不无限重试；独立SHA。
- [x] 34失败补读与A协调，由A单独修改gather；不覆盖其文件。
- [x] 40先定位实际提示数值来源，再红测单笔/日限/余额/七日预留取真实上限、明确已有缺口；前端C接线，单独SHA。

## Task 4 — L2确定性解析（root）

Files: services/gateway/app/utterance/parser.py, policies/intent_lexicon.yaml, tests/gateway/w1与真实PG。
- [x] 15只读正式原句旧NO_WRITE缺失、07/10/12等FORM红测；混合写/引用/未知语句保持原授权边界。
- [x] 08取消按真实instruction.payee_id和当前客户收款人名解析，唯一/多候选/跨客户/无匹配红绿，原投影“他行收款人”不能当客户名字。
- [x] 02近名候选、30六题测评引导、39未知账号登记引导、41咨询不授权；只在现有G3/G21能力内具体引导，不引入自动写。

## Task 5 — 真实取消grant生命周期（root）

Files: services/gateway/app/state.py, internal/scheduled.py与私有grant helper；真实DB tests。
- [x] 已提交cancel_subscription/cancel_scheduled终态归档旧grant仍ACTIVE红测；未批准/FAILED/UNKNOWN不能提前撤销。
- [x] 正常/UNKNOWN reconciliation/orphan原子归档一致；缺grant取消与懒恢复竞态不得复活，不伪造源计划授权。
- [x] 商户真实已取消给MANDATE_NOT_ACTIVE/CHARGE_BLOCKED，伪造scope/客户/指纹仍BYPASS；独立只读资金评审。

## Task 6 — 25结构错误与安全SSE（root）

Files: services/agent/app/llm/understand.py, services/agent/app/main.py及tests。
- [x] 正式25坏span→合法修正及预算耗尽SSE旧红；保持start<end检查，不自动补span或改语义。
- [x] 一次反馈使用脱敏可操作约束，耗尽用稳定错误码/产品文案；内部input_value/链接/traceback不外露。
- [x] Provider quota仍立即停止，无gather/金融写；C仅展示稳定消息，不做全局文本乱删。

## Integration

- [x] root串行集成每个SHA，逐单元独立只读复审与真实PG；对应AGENTS3/4、接口PRD同步。
- [x] 各旧fail/newpass、原报告映射、未覆盖现场分支和复验入口统一交付；不冒称原43已变绿。
- 最终完整head所有本机CI通过才能匹配head PR合并；本轮不tag、公开进度暂停、比赛上传仍由用户。

## 追加单元与最终门禁

- [x] 服务端hint发布→读取→采纳绑定，G3新原话仍需新G4/G9；真实PG15与完整W3b88、独审74pure通过。
- [x] 历史MCC/普通商户目录/真实交易日及风险期/NAV午夜/未来目标，旧红/新绿、独审与真实PG通过。
- [x] 百分比Unicode符号及近零方向不得借同向容差放过，独审原红/复审通过。
- [ ] 最终明确40位head全部本机CI及按head合并、恢复main；原测试库先无损改名保留。
- [ ] 用户另会话原43真实复验（本开发对话不执行、不代改结果）。
