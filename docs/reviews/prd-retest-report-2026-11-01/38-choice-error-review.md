# 38 产品选择后失败：与02的null-text接线交叉核验

核验head：`8e2810e6764e3fd3e37406b31e694605751e0356`；代际`demo_20261101131315_aca4156a`。源码检出为`/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank`。本次只读已有源码、38投影/观察JSON/DOM，不调用服务、浏览器、模型、Docker或数据库，不运行测试，不修改产品代码或结果表。

## 结论

38已与02一样走通“原话澄清→用户选择→G19 action→G3 SCOPED登记”，随后在进入新申购计划前出现公共Agent错误。保存action的`text_length/text_sha256`均为null，符合真实action的nullable text契约；它经BFF原样转交后，会命中02报告已确定的HTTP入口静态不兼容：`main.py:500–503`提前构造`UtteranceRef(text=u.get("text", ""))`，但本地`UtteranceRef.text`要求str，显式JSON null不会被get默认值替换。

**这是与38实际分界吻合、生产路径可确定的接线冲突；不是完整现场异常堆栈或唯一根因证明。** 本次38证据没有完整SSE/Agent入站body/字段级ValidationError，不能把02脱敏栈复制冒充38的日志。本次也没有运行新复现测试。

与旧aa7现场相比，38最初的G19/G3协议错误已改善；现在候选已实际登记为正确scope，完整X3仍没有进入新预演/审批/申购，不能记为成功。

## 38保存事实核验

原句：“把下周要扣的会员费留够，剩下的钱买稳健型理财”。选择保持用户先前明确指定的稳盈短债`prd_sbond`。

- `screens/38-choice-error.txt`和`.jpg`显示均衡混合/稳盈短债/稳健增利三个候选，点击后出现“未能生成有效回复，请刷新查看实际业务状态。”，没有申购计划/审批面板。
- DOM同时显示只读概览：李四活期11000元、未来7天预留45元。概览自身说明预留展示不代表扣款或转账。
- 新typed根原话：`utt_01M6BNNFPKCEJXJGZMVKK4TFHF`，sid=`ses_01M6BNNER0KB5Z7WPX5Z2SSTEM`，parse_status=CLARIFYING；scope为FUND_SUBSCRIBE、`account_id=acc_ls_chk`、`derived_template=available_less_7d_charges`，robust_products为mixed/sbond/stable三支，未替用户选产品。
- G19：`act_01M6BNNXX2P3YF4SSX3VNWD04F`，kind=CLARIFICATION，parent为上述typed根，normalized_fields只有`product_id=prd_sbond`，used_by已绑定新action utterance。
- 新action：`utt_01M6BNNXY5SH1V73Y7YJ4CKTGY`，kind=action，parse_status=SCOPED，parent_ids为同一typed根，source_id为同一G19 action。
- action scope保留FUND_SUBSCRIBE、活期源账户、原derived_template和三个候选谓词，并加入用户所选`product_id=prd_sbond`；没有改成另一产品/自动具体化申购金额。
- **action的`text_length=null`、`text_sha256=null`**，而typed原话length=22且有原文SHA。此处null是合法action形状，不是原话丢失或篡改证据。
- 当前sid无plan、无execution_job。before/after银行投影除`captured_at_wall`采集时间外全部相同；`38-action-observation.json`和`38-read-oracle.json`也记录无新计划/无银行变化。

读记录顺序同样符合“选择后未进入金融gather”：13:44:29原话`_utterance_snapshot`；13:44:31的get_accounts和upcoming_charges早于选择；13:44:43.711386选择时只有新增`_utterance_snapshot`。不能说“38完全没有业务read”，也不能把选择前的概览read冒称选产品后的申购前置读取已经完成。

观察JSON中的`independent_max_cents_at_current_state=1095500`是当时1100000分可用减4500分预留的独立计算；`max_was_not_live_preflight_or_executed=true`。它不是L2已返回的live申购预演，更不是已提交10955元订单。不能据此扩写为X3部分申购成功。

## 与02的同一静态链

| 边界 | 02 / 38共用实现 | 对38的作用 |
|---|---|---|
| 候选→登记 | `Assistant.tsx:248–280`先G19，再发送kind=action、action_id，text/clarification_id/hint_id均null | 38保存的正确G19和action证明该段已通过，不再是旧CONTRACT_INVALID停点 |
| 合法G3协议 | `gateway/app/utterance/models.py:21–35`的Registration要求action只携action_id；`utterance/service.py:505–524`保存body.text并返回 | action.text=null是契约要求；不能通过伪造typed原话修补演示 |
| BFF→Agent | `frontend/server/bff.mjs:570–580`把G3 utterance的id/kind/text原样放入`input.utterances` | null继续传给Agent |
| Agent HTTP入口 | `agent/app/main.py:497–506`在graph.ainvoke前构造本地UtteranceRef；text使用`u.get("text", "")` | key存在且null时得到None；不是空串 |
| 本地类型 | `agent/app/graph/state.py:35–40`要求`text: str` | 与合法action输入静态不兼容，足以在graph前阻断 |
| 后置适配 | `agent/app/gateway/adapters.py:144–158`有`text=utt.text or ""`，graph ingest G20后才调用 | 该适配虽然正确，但HTTP前置失败时尚未到达 |
| 公共错误 | `main.py:286–310`把ValidationError/LlmToolError映射为安全公共消息 | 38出现相同公共文案与该链一致；仅凭文案不能区分所有底层异常 |

02有脱敏栈精确定位main.py:500；38目前没有同级堆栈。本报告交叉定位为同类静态接线，保留二者证据强度差异，不将02一条日志复制成38第二条日志。

## 现场判定边界

38应保留失败结果：正确产品选择已登记，但没有新申购计划、预演、审批、订单或资金变化。不能为补全演示改成typed补答、手工输入10955元、走原生表单/API或重置action后改句，以此替代原候选点击成功。

本次不修代码。与02类似，前端候选测试只fake成功SSE、BFF/Agent HTTP测试主要typed字符串，后置wire/ingest测试才覆盖null；这些已有自动验证不能证明当前完整候选→Agent HTTP链已成功。40缺少hint按钮仍是独立PARTIAL展示问题，不能合并为同一次现场错误。
