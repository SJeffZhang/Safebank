# 40 独立复核：持久 hint 到采纳按钮的链路

核验head：`8e2810e6764e3fd3e37406b31e694605751e0356`。源码检出：`/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank`。本次仅只读本地源码、保存DOM/SQL投影；没有运行测试、服务、浏览器、模型、Docker或数据库，也没有修改产品代码。

正式对象是 `40-isolated`，原句“给王五转50000元”。首轮与39共用旧会话的40另保留在 `40-shared-session-result.txt`，不混入本报告独立复核结果。

## 结论

**发现确定的 id/ref 契约断点（P2）：** 网关真实 hint 和 Agent wire 的键均为 `id`；Agent 的 `_card_replan_hints` 却只从每个hint读 `ref` 来生成 `CardAction(kind="HINT")`。合法生产hint没有 `ref`，所以不会生成采纳action。前端只遍历card.actions读取标签并渲染按钮，根本不会由 `data.hints` 或段落里的5000元反推hint身份。持久化hint存在与按钮缺失因此可以同时发生。

现场已经恢复的部分也应保留：独立会话得到REPLAN/RED，段落给出实际单笔收缩上限 **5000.00元**，没有执行原50000元转账。本问题是可用建议在客户界面无法采纳，不是“hint没有持久化”或“已采纳后被L2拒绝”的证据。

## 已保存现场证据

- `screens/40-isolated-result.txt` 显示“需要调整计划”、固定说明“可用建议会在安全网关核实后显示为按钮”、原因段落及“最多5000.00元”；DOM列出的按钮只有通用导航/新会话/发送等，没有hint采纳按钮。
- `receipts/40-isolated-before-gateway.json` 有1条历史hint；after有2条。新行：
  - `id = hnt_4a05eb42-564d-5d03-8bb2-58d29afd41d3`
  - `parent_plan_id = pln_01M6BMN8BJQ6ECG65WF68AZ88W`
  - `sid = ses_01M6BMM8SD4DVMFAQBXXE0N2MA`
  - `scope_json.action_kinds = [TRANSFER_EXTERNAL]`
  - 仍为`acc_zs_chk`和王五原target_fingerprint，唯一transfer金额 **500000分**，原请求为5000000分。
  - `created_at_wall = 2026-11-01T13:26:53.114563+00:00`
  - `expires_at_wall = 2026-11-01T13:36:42.429752+00:00`
  - `used_by = null`，有非空64字符view_hash。
- 同一after投影中父plan为REPLAN；evaluation `evl_01M6BMN8DRE0E9S0CBASAK92T1` 为RED，`CARD_SINGLE_LIMIT` finding明确resolver=user、step=`stp_transfer_ww`。因此生成REPLAN_HINTS卡的上层条件满足，DOM也确实显示该卡。
- 当前sid没有kind=hint的utterance、没有execution_job；新hint未被采纳。`40-isolated-read-oracle.json`记录已采银行业务投影不变。本次额外比较before/after，仅`bank.captured_at_wall`采集时间不同，其余bank字段相同；没有把时间字段差异当业务写入。
- 保存证据meta均标记同代际`demo_20261101131315_aca4156a`与上述精确head。

保存投影没有完整原始SSE/网络响应，本报告不伪称已捕获现场`actions=[]`的SSE正文；下面源码链可确定：采用真实已校验hint shape生成card时，当前实现必然漏掉其HINT action。这与现场DOM一致。

## 源码链逐段核对

| 环节 | 当前实现 | 本次判断 |
|---|---|---|
| 网关hint schema | `services/gateway/app/schemas/decision.py:43`：`ReplanHint(id,label,scope,expires_at_wall)` | 没有`ref`字段 |
| 持久化与G4响应 | `hint_sources.py:286`使用`proposal.id`存hints.id；`api/plans.py:510`用`published_hints.model_dump(mode="json")`生成replan_hints响应 | 发布保留id，不转换为ref；已有真实持久行 |
| Agent网络wire | `services/agent/app/gateway/wire.py:137`：`GatewayReplanHint`也要求id；`gateway/client.py:134`校验真实G4返回 | 真实hint不会成为测试中的ref形状 |
| Agent graph state | `graph/graph.py:469–473`保存`decision.model_dump(mode="json")` | 正常保留id，没有额外映射ref |
| REPLAN card生成条件 | `llm/cards.py:301`要求REPLAN且有user resolver；`:369`逐finding查resolver | 本例CARD_SINGLE_LIMIT满足；卡已显示 |
| **错误映射点** | `llm/cards.py:217`只取`h.ref`；`:219–220`只有ref非空才append HINT action；label仍被放入`data.hints` | 真实id型hint产生标签数据但无action |
| SSE | `main.py:551–556`直接serialize `generate_cards(final_state.values)` | 不在SSE层补id→ref |
| 前端收标签 | `Assistant.tsx:584–590`仅遍历`card.actions`中的HINT且`ref`以hnt_开头，才GET`/sessions/{sid}/hints/{ref}` | 无action就不会尝试标签读取 |
| 前端显按钮 | `Assistant.tsx:626–634`要求`a.kind === HINT && labels[a.ref]`，按钮label来自服务器 | 不以`data.hints`直接显示；无action则无按钮 |
| 固定说明 | `Assistant.tsx:696–697`对任何REPLAN_HINTS卡都显示“核实后显示为按钮” | 即使actions为空也有此说明，形成现场占位死路 |
| 点击后采用 | `Assistant.tsx:380–389`本应提交`kind=hint,hint_id=ref`，其余text/action/clarification为空 | 本例按钮不存在，根本没进入采用；不能把它写成G3采用失败 |

BFF `frontend/server/bff.mjs:30`明确允许GET hint路径，`api.ts:23`将该路径用同站GET转发；本次没有看到“BFF缺少hint路由”的源码依据。

## 其他会使按钮不出现/不可用的现有条件

这些是现有实现的条件，不应替代上面已经确认的id/ref错误，也不能无网络证据就认定本例是其中某种服务拒绝。

1. 即使将来action存在，ref不是hnt_前缀时不发GET。
2. G22a `api/sessions.py:321–350`要求当前会话/客户合法、hint存在、used_by为空、TTL未到，并通过`validate_hint_source`对父计划/最新evaluation/epoch/授权根/完整scope/hash等绑定复核。失效或已消费建议被拒绝是正常防线，不能绕过。
3. 前端GET失败会被`.catch(() => {})`静默吞掉，没有用户可见错误，也没有重试；`label`空字符串同样不显按钮。无action时该请求根本不会发生。
4. 按钮一旦存在，`busy`只会将其禁用，不会隐藏；本例DOM完全无按钮不能仅用busy解释。
5. SafeCard卸载后不保存异步label，这是正常清理；本例卡仍可见。

SQL中的TTL只是保存时事实；现在再读可能已经过期。本轮不发任何服务请求，也不通过新请求/换session尝试采纳。不能把晚于TTL的检查失败倒推为最初按钮缺失原因。

## 自动验证缺口

`tests/agent/test_cards.py:106–123`的`test_replan_user_generates_hints`使用：

```python
{"ref": "hnt_abc", "label": "请选择收款人"}
```

它既没有真实schema必需的id/scope/expires_at_wall，又恰好满足错误的ref读取，测试自然会看到一个action。该夹具没有经过`GatewayDecision`实际wire校验，因此没有暴露真实G4→card字段不一致。

`test_nov1_replan_receipt.py`使用真实id型hint验证段落上限、失败step、TTL和scope，这能证明解释正确，却没有把同一个decision继续送入card→SSE→前端按钮链。gateway PG的发布→G22a→G3采用→新G4/G9也能直接调用端点，无需经过Agent card；它们通过不等于客户界面入口完整。

本次没有运行新测试或构造新服务回放，以上仅报告已有断言和生产wire之间的范围差异。

## 展示期间的处理边界

继续保留“50000元安全阻止/5000元解释正确/持久hint存在”和“没有可采纳按钮”两项结果。不要手工typed一个5000元请求、调用G3/API或篡改DOM代替缺失按钮；那会变成另一条授权路径，不能补成本例hint采用成功。

本报告只定位和留存问题，不实施修复。本例没有验证hint实际采用/新预演/新审批/执行闭环，也没有证明银行授权防线失效。
