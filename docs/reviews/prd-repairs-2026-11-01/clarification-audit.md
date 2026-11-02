# 2026-11-01 澄清与 UI 接线只读审计

基线：`aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1`。范围：产品候选按钮、X3/X4 的 UI/澄清边界、正式 CSV 中全部 16 项 PARTIAL 的展示与接线症状。仅本地只读证据；未调用 API、模型、DB、Docker 或浏览器，未运行测试，未修改仓库、原报告、CSV、WIP、AGENTS/PRD。

## 证据与判断边界

- 原报告根：`/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/`。
- 阅读 `REPORT.md`、`primary-43-results.csv`、`formal-parser-gaps.md`、`requirements-gaps-final.md`、`evidence-review-funds-x3.md`，以及 37/38 的保存 DOM、read-oracle 和 gateway 回执。只从回执输出本 case 的状态、作用域和记录数量，未输出整块回执。
- CSV 共 43 例，19 PASS / 16 PARTIAL / 8 FAIL；CSV 的「序号」不是 case ID。X4 = ID 37（序号32），X3 = ID 38（序号41）。
- 源码定位与保存现场吻合，但没有 HTTP 请求原文/响应 trace；下面明确区分现场已见失败与源码下一屏故障。
- 原生产品比较、原生 MMF/非 MMF 赎回、后续时钟结算不能回改助手原句 32/34/38。38 的原始前置为 33 已预留 200000 分、34 失败；`1000000 − 4500 = 995500` 分是独立 oracle，未实际进入本次申购预演。

## X3：两个连续契约错误，最小修复在前端

### 现场已见

`screens/38-clarification.txt` 显示三候选「均衡混合 / 稳盈短债 / 稳健增利」。`38-choice-result.txt` 中稳盈短债按钮 active 后出现 `CONTRACT_INVALID`。`38-read-oracle.json` 根为 `CLARIFYING`；`receipts/38-after-gateway.json` 本 sid 保持 OPEN SLOTS，required_fields=`[product_id]`，候选为 prd_mixed/prd_sbond/prd_stable，父为本 case 的 typed 根。没有 form_action、plan、approval 或 execution_job。候选正确、尚未选择时等待是预期；点击合法候选后不能继续才是失败。

### 跨层数据流

1. G3 解析并保存原话及 OPEN SLOTS；G21 返回 `parent_utterance_ids`、字段和权威候选（`services/gateway/app/api/sessions.py:293`）。
2. Assistant 在 stream 结束后 GET G21，保存 `clar`（`frontend/src/mobile/Assistant.tsx:145`），以该候选渲染按钮（:319），点击进入 `choose()`（:185）。
3. `choose()` 发 G19 `kind=CLARIFICATION`，却固定 `parent_utterance_id:null`（:194）。`ActionRequest.shape` 要求该类 action 的 parent 非空（`services/gateway/app/utterance/models.py:53`），因此请求在契约层被 422 拒绝，action 不落库。这与现场立即 CONTRACT_INVALID、零 action 吻合。即使跳过 shape，G19 还严格核父 ID 属于 current clarification（`api/sessions.py:231`）。
4. `frontend/src/mobile/types.ts:236` 的 Clarification 类型没有声明 G21 已有的 `parent_utterance_ids`，调用侧因而未承接正式父链。
5. **第二屏、源码确定但现场未到达**：G19 若成功，`choose()` 随后发 F3/G3 `kind=action`，又附 `clarification_id:clar.id`（Assistant :203）。`Registration.exclusive` 只允许 typed 补答携带此值（`utterance/models.py:33`），仍会 422。action 本身已由 G19 存父链，G3 应仅引用 action_id，不能再次自报澄清。
6. BFF 对 G19 用白名单直代理 body（`frontend/server/bff.mjs:621`），F3 登记同样透传 body（:559）。没有证据需要修改 BFF 来代填授权父链。
7. 正确的现有服务端范例是 `tests/gateway/w1/db/test_utterances.py:190`、`tests/gateway/w2/db/test_real_w1_integration.py:120`：G19 传合法 parent，随后 G3 action 只传 action_id，最终合槽 root 的 parent_ids 指向旧根。

### 最小文件边界

- `frontend/src/mobile/types.ts`：承接 G21 的 parent_utterance_ids（以及如需的过期元数据），保持正式 wire。
- `frontend/src/mobile/Assistant.tsx`：选项只来自当前 G21，G19 引用它的父原话；G3 action 不带 clarification_id；来源失效/不完整时安全停止并刷新，不猜父根、不直接生成 FORM 购买。
- 新建 `frontend/src/mobile/AssistantClarification.test.tsx`，或定点扩展 `AssistantWire.test.tsx`。当前 mobile 相关测试统一默认 `{clarification:null}`，没有实际点击 G21 候选的请求断言。
- 不需要放宽 `ActionRequest`、`Registration`、G19 父链校验，不能把 CLARIFICATION 改成 FORM 绕过助手原句。

## 通用 FORM_REQUIRED 与跨轮路由

### 已观测和源码根因

正式 07、10、14、15、17、24、26、27、29、31、34b 各有只读结果仍显示空 FORM_REQUIRED；02 则是需要真正消歧却没有有效候选。两者不能一起删澄清。

- Parser 对正式只读表达覆盖很窄，无法匹配时为 INTENT_UNCLEAR；G3 在 `utterance/service.py:482` 建立 OPEN FORM_REQUIRED（空 required_fields/candidates）。这是当前服务端 directive，不能归因于旧气泡。
- L1 `graph/graph.py:399` 对 INTENT_UNCLEAR 强制 unclear=true，但保留模型 needs_write=false，随后仍允许 gather/read/respond（:599/:608）。
- `llm/cards.py:330` 只看 unclear 与 INTENT_UNCLEAR，就生成 CLARIFICATION，未排除已经完成的只读请求；已有 `tests/agent/test_cards.py:146` 甚至把 needs_write=false 的此行为写成预期。
- 前端同时显示该 custom card 的通用文案（Assistant :593），又在 run 完成后 GET G21 并渲染独立补充区（:145/:311）。所以出现正确答案加两份不适用提示。
- **静态后续故障，正式轮未证实发生**：Assistant.send :179 对任何后续 typed 消息自动附当前 clar.id，不区分“新需求”和“本次补答”，也不区分 SLOTS/FORM_REQUIRED。G3 `service.py:390` 直接进入 typed_fields/merge_scope，而 require_slots :192 会拒绝 FORM_REQUIRED，以 INTENT_SCOPE_EXCEEDED/409 停止，下一句连自己的 parser 都未进入。
- SLOTS 同样不能把任意下一句都当补答。应有明确的补答操作与新需求发送路径，服务端继续严格验证父链、候选、epoch、范围和期限。
- SafeCard 只实际处理 HINT / OPEN_APPROVAL / NAVIGATE（Assistant :511），CLARIFY 依赖独立 G21 补充区处理。这本身不证明按钮缺失，但候选处理测试必须点击真实补充区，不能只断言卡 actions 存在。

### 责任边界

- Parser/服务端只读分类与保留无写授权由 root/查询 agent 协调；UI 不能凭模型 needs_write=false 清除服务端授权数据或忽略真正 SLOTS。
- L1 cards 的无关 CLARIFICATION 生成与前端独立 G21 区应同时验证；仅藏 custom 气泡仍会留下当前 FORM_REQUIRED。
- 前端小组件任务可独立处理“普通新消息不隐式附旧 clar.id、显式 typed 补答/候选选择才引用它”，并保留 38 真正 SLOTS。

## X4：本次不是澄清按钮问题

保存 `37-read-oracle.json` 与 `receipts/37-after-gateway.json` 的本 case 根为 SCOPED + ACTIVE，clarification_id=null，当前 sid **无 clarification**。scope 已有 CARD_FREEZE + TRANSFER_SELF、from=acc_zs_sav、to=acc_zs_chk、200000 分与唯一可疑卡。已读 detect_anomalies/get_cards/get_accounts；没有 plan、approval、execution_job。

`screens/37-result.txt` 的错误是 `to_account_id must be a tagged entity object`。前端只是把 stream error.message 原样 setError：Assistant :88 → :151；BFF `server/bff.mjs:274` 透传 message。因此 X4 写计划实体问题交由负责 plan 的 agent；不应修前端目标选择来代替该失败。

真正多可疑卡 SLOTS 仍是后续应测试的边界，本轮单卡 37 没有现场证明它。

## 全部 16 个 PARTIAL 的 UI/接线分组

| case ID | 已有事实 | UI/接线未闭合（保持原结果） |
|---|---|---|
| 02 | 安全零写 | 无收款人候选或具体问题，空 FORM_REQUIRED；需要上游建立绑定候选 |
| 07 | 两条 ACTIVE 定时正确 | 多余 FORM_REQUIRED；聊天 UTC 日期、收款人泛称他行；目标命名还涉 L3 view/查询 |
| 10 | AA 总400/本人100/已收0/待收300正确 | 多余 FORM_REQUIRED；不能用后续 AA 付款回改此轮数字 |
| 14 | 聊天9月/8月统计正确 | 结构化卡月份警报、内嵌仍10月并要求选择，另 FORM_REQUIRED |
| 15 | 明确9/15–10/15两异常正确 | FORM_REQUIRED 与 raw untrusted_data 标签 |
| 17 | 状态/单笔5000/日限20000/余额6100正确 | 未给剩余日限19600；把余额可用与单次可刷上限混淆；FORM_REQUIRED/raw标签 |
| 24 | 四项月费合265正确 | FORM_REQUIRED 与内部 si 标识 |
| 26 | 会员45、定时200的源数据正确 | 聊天漏解释定时且窗口说错；FORM_REQUIRED；UI不可把245预留减成45冒充同口径 |
| 27 | 唯一闲置健身199/月正确 | 下次9/25误写，实际10/25；FORM_REQUIRED |
| 29 | 李四R3正确未修改 | 多余 FORM_REQUIRED |
| 30 | 未改R5、未新增测评 | 只有通用 FORM_REQUIRED，缺亲自六题问卷导航/说明 |
| 39 | 未登记目标、未新增/转出 | 只有通用 FORM_REQUIRED，缺具体安全解释/登记引导 |
| 40 | REPLAN/RED、CARD_SINGLE_LIMIT、零写 | “最多50000元”与单笔1000/余额6100不符；不自动改金额；37没冻结不能称冻结拦截 |
| 41 | 咨询疑问句未产生写授权 | 没回答可行性或具体追问；只通用 FORM_REQUIRED；保留问号和零写 |
| 31 | R3筛出4款≤R3产品，无越级购买 | FORM_REQUIRED 与内部产品ID |
| 34b | 10000份+2000待确认核心事实正确 | FORM_REQUIRED、产品误称、trade_date UTC截日10/14而实际上海10/15；不改基金状态 |

### 表示层的独立根因/边界

- **14 多月份比较**：`bill-month.ts:8` 将含“前一个月”的比较请求返回 null；Assistant :143 给该轮所有卡共享单值 requestedMonth，CardData :573 将两个月汇总各自与同一值相比；AssistantWorkspace :21/:47 在无可确定单月时显示默认当前月选择器。这能解释“聊天双月正确、卡拒绝、内嵌10月”。最小方向是明确多月比较的显示上下文/允许月份集合，而不是移除月份范围校验。该部分与查询 agent 的月度读取范围协调，独立文件为 bill-month.ts / Assistant.tsx / AssistantWorkspace.tsx 及已有 AssistantTopics.test.tsx。
- **raw untrusted_data**：gather :471 把自由文本包装进 ReadRecord.data；cards :123 只按字段名删部分不可信键，display_name/counterparty/target_display 等包装仍可流入卡；Assistant :631 与异常 :610 直接 String 显示。产品名/merchant_name 被删又退化为内部 ID（:640/:642），持仓也明确显示 product_id（:617）。应在 L1 卡数据边界保留可安全作为纯文本显示的名称，同时保持提示词污点包装；不要用 UI 正则抹标签或将不可信文本变成授权。React 当前按文本节点渲染，证据是协议标记外露，不能夸为已执行HTML/XSS。
- **错误外露**：25 ValidationError、37 tagged entity 错误走 Agent SSE → BFF → mobile alert。应由拥有生产 Agent 错误边界的 agent 输出稳定用户文案/错误码，服务侧留诊断；单在 UI 掩掉字符串不能补齐未完成业务。现有 BFF 错误透传测试并不证明此类消息已脱敏。
- 07/26/27/34b 的多项日期错误位于助手聊天内容。共享 ui.date/dateTime 已显式 Asia/Shanghai（ui.tsx:414），不可统一改时区 helper 就宣称修复模型文本；核查真实读字段与输出事实边界。

## 最小红测入口（建议，尚未运行）

1. **X3 点击协议红测**：用 AssistantWire.test.tsx 的 render/mocked stream/request 风格，G21 返回真实形状 SLOTS（含 parent_utterance_ids=[utt_parent] 与三候选），完成原句后点击稳盈短债；断言 G19 parent=utt_parent、唯一字段 product_id=prd_sbond，随后 F3 kind=action 只引用 action_id、clarification_id为null/缺失。当前会分别在 null parent / 非法 action clarification_id 失败。
2. **安全停点**：同组件 fixture 给缺父/过期/新 sid 旧澄清，断言不提交候选 action、不猜父/产品、不生成 FORM；G19失败不发F3，保留短错误并允许刷新。可用 deferred promise 防双击提交；不要碰DB。
3. **跨轮污染红测**：第一轮完成只读且 G21=空 FORM_REQUIRED，下一条普通新需求断言 clarification_id=null；真正 SLOTS 显式补答仍引用正确 clr.id。当前任意新消息都会携旧ID。
4. **只读重复澄清红测**：在 `tests/agent/test_cards.py` 给 INTENT_UNCLEAR+needs_write=false+真实 reads，断言不额外生成要求写字段的卡；配前端 G21 directive 用例。服务端只读分类测试由查询/parser agent 负责，不能仅UI隐藏当完整修复。
5. **多月比较红测**：AssistantTopics 中使用精确14原句，分别传9月/8月source cards，断言两者都保留自己的月份、不报警/不退默认10月；另保留真正错月source的拒绝负控。先与查询 agent 对齐来源月份，不能放宽为任意月份。
6. **纯文本名称红测**：`tests/agent/test_cards.py` 用生产 ReadRecord 包装文本，经 generate_cards 到 AssistantWire；断言可见名称正确、无内部 `<untrusted_data` 协议文本，同时无伪造按钮/写参数，原 LLM 提示仍有污点包装。该变更主要归 L1 cards 拥有者。
7. **X4错误UI边界**：生产 graph/plan agent 的红测必须先重现错误实体与双步计划；frontend只补SSE错误可理解/不外露内部栈的断言，不承担修正to_account_id。

建议小组件退出命令：`npm --prefix frontend test -- src/mobile/AssistantClarification.test.tsx`，随后定点 AssistantWire/AssistantTopics/PlanLifecycle；涉及 cards 时跑对应 pytest。完整 frontend test/build/BFF 与最终40位head CI仍由root执行。本审计没有运行以上命令，不宣称红测或测试通过。
