# 02 候选选择后失败：只读接线核验

源码head：`8e2810e6764e3fd3e37406b31e694605751e0356`。检出：`/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank`。仅只读源码及保存的02证据；没有运行测试、服务、模型、浏览器、Docker或数据库，没有修改产品代码。

## 结论及证据等级

发现**确定的静态接线冲突**，位置与保存错误的 `main.py:500` 一致：候选点击产生合法 `kind=action,text=null`，BFF原样转交；Agent HTTP stream入口在调用graph前构造本地`UtteranceRef`，其中`text`必须是str，却使用`u.get("text", "")`读取。字典含`text: null`时，get的默认值不会生效，传入的是None，触发本地结构校验。

这条确定冲突足以解释“L2已登记SCOPED，但Agent未进入gather/plan”的现场分界。**现有脱敏日志只保留ValidationError计数和栈位置，没有校验字段/完整入站body，不足以宣称已恢复现场全部异常细节或唯一根因。** 报告保留这一证据限制；没有把错误直接归因生产模型、API限额或scope错配。

## 现场保存事实

- `screens/02-choice-error.txt`：原句“给王转200块”，正确显示王武/王五候选；点击王五后显示公共安全错误“未能生成有效回复，请刷新查看实际业务状态。”，没有审批或转账结果。
- `02-agent-error-sanitized.json`：ValidationError出现1次，栈定位`/app/services/agent/app/main.py`, line500, `_stream`；provider限额状态匹配数组为空。该脱敏文件不包含完整验证错误，不能靠它判定唯一字段。
- `receipts/02-after-gateway.json`：选择action `act_01M6BMQECR9QWD7XHGZMEMXG9J`，kind=CLARIFICATION，父`utt_01M6BMQ2M90RBNX2MGZG5NDWSX`，字段为`payee_id=pye_zs_ww`；其used_by为新action utterance `utt_01M6BMQEF9T98TNCQYADK1C7VD`。
- 新utterance的kind=action、parse_status=SCOPED、父链正确；normalized_scope保存20000分、王五指纹、`from_account_id=acc_zs_chk`，不是误猜目标/金额。其`text_length/text_sha256`均为null，与合法action无text的模型一致。
- sid=`ses_01M6BMQ1MVXGH2V845C3ZN265P`，该sid读记录只有两条`_utterance_snapshot`，没有业务工具read，没有plan/job。它们是授权登记所需快照，不能称已进入Agent业务gather。
- 比较02 before/after银行投影，仅`captured_at_wall`采集时间变化，其余银行投影相同，未执行200元转账。网关action已登记/消费source不等于金融执行成功。

## 实际生产路径

| 环节 | 源码 | 实际约束与推断 |
|---|---|---|
| 前端候选 | `frontend/src/mobile/Assistant.tsx:248–280` | 校验真实G21单一parent；先G19登记CLARIFICATION，再发送`kind=action, action_id, text=null, hint_id=null, clarification_id=null`。这符合本轮修过的G19/G3协议。 |
| G3输入 | `services/gateway/app/utterance/models.py:21–35` | `Registration.exclusive`要求action仅有action_id，text必须为空；clarification_id仅能给typed补答。不能为迁就Agent而把候选action伪装成typed原话。 |
| G3输出 | `utterance/service.py:505–524` | row直接保存`text=body.text`，action对应None；返回`as_ref(row)`。保存的规范scope证实本例G3成功。 |
| BFF登记 | `frontend/server/bff.mjs:559–568` | 调`/sessions/{sid}/utterances`得到G3 utterance及assistant token。 |
| **BFF→Agent** | `frontend/server/bff.mjs:570–580` | 构造`input.utterances[{id,kind,text:r.data.utterance.text}]`，没有把null转为空串；pipe`:195–203`把该body发`/threads/{sid}/runs/stream`。 |
| Agent HTTP请求形状 | `services/agent/app/main.py:439–454` | 路由参数是`body: dict`；当前没有独立的Pydantic `AgentRequest`模型在HTTP入口预先适配nullable text。 |
| **Agent过早校验** | `main.py:497–506` | 在graph.ainvoke前执行`UtteranceRef(id=...,kind=...,text=u.get("text", ""))`。JSON null转None，get默认值只覆盖key缺失，不覆盖None。 |
| 本地schema | `services/agent/app/graph/state.py:35–40` | `UtteranceRef.text: str`，不接受None，因此该合法action输入在这一层不兼容。 |
| 原本可用的nullable适配 | `services/agent/app/gateway/adapters.py:144–158` | `gateway_utterance_to_ref`明确为hint/action做`text=utt.text or ""`。但它在graph ingest的G20之后才用；main:500已失败，`:510`graph.ainvoke尚未执行，不能依赖这个后置适配修复HTTP入口。 |
| 公共错误 | `main.py:286–310`与stream异常处理 | ValidationError映射MODEL_OUTPUT_INVALID及现场同文安全提示。提示不包含原字段/堆栈，是正确的错误脱敏；它不会说明究竟是哪一个本地校验字段。 |

生产G20 wire本来允许null：`services/agent/app/gateway/wire.py:74–86`的GatewayUtterance.text为`str | None`，注释明确hint/action可以null。这里不是“action输出违反既定协议”，而是HTTP外层过早套了只接受字符串的L1本地ref。

## 范围及与其他现场问题的关系

- 该冲突不限收款人02；通过同一BFF messages路径提交的**任意合法action/hint且text=null**都静态命中。38的产品候选action因此有相同潜在阻断，应按实际现场证据单独记录，不能仅凭静态推断预填FAIL。
- 40的hint按钮缺失是另一独立问题（见`40-hint-ui-review.md`的id/ref错配）。即使以后补出按钮，合法kind=hint登记仍会携null text，并可能在这里继续失败。当前40没有按钮/没提交hint，不能把02错误写成40已经发生的第二次错误。
- 显式typed SLOTS补答text为非空字符串，静态上不命中这个null转换；但本轮演示不能把它当作候选点击的替代通路来补PASS。
- 尚未到graph.ainvoke的这条接线不需要模型推理即可失败。没有生产模型错误证据，也没有402/403/429匹配；“无限额匹配”本身不证明整批所有调用均无额度问题。
- 已成功的G19/G3/规范scope保持其成果；不能为避开HTTP结构错误，放宽授权、伪造用户原话、清空父链、重新登记任意目标或用CLI执行。

## 现有自动验证为何没有覆盖此入口

- 前端`AssistantClarification.test.tsx:41–70`用fetch fake接收真实前端`/messages` body，检查action协议正确后直接返回成功SSE，没有执行BFF转发和Agent HTTP模型构造。其成功能证明前端G19/G3请求形状，不能证明跨服务流已通。
- BFF `frontend/server/bff.test.mjs:180–214`的stream集成夹具返回的是typed+字符串text；没有action/hint+null text进入真实Agent。
- Agent `tests/agent/test_sse_wire.py:203–208`及`test_nov1_sse_errors.py:57–58`的HTTP stream输入也为typed+字符串。
- `tests/agent/gateway/test_wire.py:82–86`正确验证hint/action null被GatewayUtterance接受；`tests/agent/routing/test_ingest.py:169–182`正确验证后置`gateway_utterance_to_ref`将null转空串。这些测试没有穿过main.py:500的前置构造，不能证明HTTP入口兼容。

以上只读核验没有新增红绿测试，也没有声称已修复或已复验通过。展示期间继续保存本次安全失败与零金融写，不重试/改句/借另一通路覆盖原结果。
