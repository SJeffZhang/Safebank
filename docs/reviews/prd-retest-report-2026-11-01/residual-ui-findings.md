# 原43复验：助手 read card 残余展示问题

核验日期：2026-11-01。源码：`8e2810e6764e3fd3e37406b31e694605751e0356`，检出 `/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank`。只读本地源码及根agent已保存的DOM；没有调用服务、浏览器、模型、Docker或数据库，没有运行测试，没有修改产品代码。

## 现场结论

12 的核心消费查询已经恢复：保存DOM第50行显示消费 **550.50元**、转账支出 **1800.00元**分列，最终文字正常；数据卡仍有包装和技术来源外露。因此核心数据/最终回复改善与卡片残余缺陷同时成立，应保留两者，不能把新12描述为仍旧GENERIC_ERROR，也不能把全展示链记为已清理。

现场证据：`screens/12-result.txt` 第19–28行，五个交易明细对手方逐字显示 `<untrusted_data source="src_...">云盘/便利零食/街角茶饮/社区生鲜/日用百货</untrusted_data>`；第19和32行显示两张卡的 `来源 src_...`。相应截图为 `screens/12-result.jpg`。本次没有重新抓取页面，仅核读上述既存文件。

## F-UI-01 [P2] 已包装 ReadRecord 被直接用于用户卡片，卡片文本未做显示转换

**明确根因链：**

1. `services/agent/app/llm/gather.py:372` 的普通read执行，以及`:602`的补读，都用 `apply_untrusted_wrapping(...)` 包装网关标记的不可信字段，把包装结果保存为 `ReadRecord.data`。`redact.py:99`先deepcopy，`:49`加入`<untrusted_data source="...">...</untrusted_data>`。这是模型上下文的防注入格式，本身没有问题。
2. `services/agent/app/llm/cards.py:258` `_cards_from_reads`直接取该包装后的`r.data`；`:114` `_safe_data`只调用`:123`的递归字段名过滤。它没有读取该read的`untrusted_paths`，也没有对保留字符串做显示解包。
3. `cards.py:37` `_UNTRUSTED_FIELDS`删除`name/merchant_name/memo/...`，但没有`counterparty/display_name/target_display`。因此交易counterparty虽然已被trust策略正确标为不可信，仍以包装字符串进入UICard。
4. `frontend/src/mobile/Assistant.tsx:735`的通用卡片渲染依次取`display_name → counterparty → merchant_name → target_display → name`，直接`String(...)`放进`:760`的`<span>{name}</span>`；没有显示解包。
5. 最终聊天是另一条路径：`respond.py:798`新增`_data_label`，多个摘要及`:139`模型回复清理包装；这不会改写原`ReadRecord.data`，不会影响`generate_cards`。`main.py:551`在最终state上另行生成cards，经SSE custom发送。因此仅验证respond清理不足以验证卡片清理。

**影响与限度：** 此处是可见协议残留/可读性问题。React把字符串作为文本渲染，本证据没有显示HTML执行、XSS、授权放行或资金变化；也不应为清理展示而去掉模型侧污点或修改底层来源证据。

### 可静态确定的相关范围

| 助手卡类型 / read | 字段与现有处理 | 结论类型 |
|---|---|---|
| TRANSACTIONS / get_transactions | trust标记counterparty/memo/merchant_name；cards删后两者，保留counterparty；通用renderer直接显示counterparty | **现场12已复现**包装外露 |
| ANOMALIES / detect_anomalies | 同样保留嵌套`transaction.counterparty`；Assistant.tsx:715直接`String(tx.counterparty)` | **源码可达**相同包装外露；其他现场例待根agent保存证据 |
| ACCOUNT / get_accounts | trust标记`display_name`；cards保留；通用renderer优先display_name | **源码可达**相同包装外露 |
| SUBSCRIPTIONS / upcoming_charges的scheduled_items | trust标记`target_display`；cards保留；通用renderer显示target_display | **源码可达**相同包装外露 |
| SUBSCRIPTIONS / list_subscriptions、detect_subscriptions及upcoming商户行 | trust标记`merchant_name`，cards直接删除；通常无可用商户名时renderer降级到`instruction_id/merchant_id` | **源码可达**另一类残余：商户名缺失/内部ID外露；不等同必然显示XML包装 |
| PRODUCTS / list_products、compare_products、get_product | trust标记`name/risk_disclosure`；cards删name，renderer通常降级到`产品 · {id}`；risk_disclosure虽保留但此renderer未展示它 | **源码可达**产品名丢失/内部ID外露；不应称这些卡当前一定展示risk_disclosure包装 |
| HOLDINGS / get_holdings | trust无包装；renderer:722–723直接显示`product_id` | 不属同一包装根因，但仍有内部ID代替产品名的展示限制 |
| TRANSACTIONS / get_spending_summary | trust无不可信字段；月summary卡显示权威category/amount | 不会因该read自身产生XML包装；仍受下述来源字段展示影响 |
| CARD / get_cards | trust无包装，显示受控状态及尾号 | 不会因该read自身产生XML包装；仍受下述来源字段展示影响 |

`list_payees/list_scheduled/get_split_status/get_risk_questionnaire/get_risk_profile/get_profile`在cards.py:73被明确跳过，不产生对应read card，不能把通用卡片问题扩写为这六类read必然展示包装。上述范围仅限**助手SSE read card**，没有证据支持把它扩展为原生账单/账户/订阅业务页面同样有此问题。

## F-UI-02 [P2] 每张带source_ref的卡片公开显示内部src标识

这是与字符串包装不同的独立展示点：`cards.py:275`把原read的`source_ref`写入UICard，`frontend/src/mobile/Assistant.tsx:612`无条件渲染`<small>来源 {card.source_ref}</small>`。所以即使将counterparty外层标记去掉，顶部`来源 src_...`仍然存在。

范围为所有非空source_ref的SafeCard，包含ACCOUNT、TRANSACTIONS（明细与月汇总）、ANOMALIES、CARD、SUBSCRIPTIONS、PRODUCTS、HOLDINGS等。12 DOM已实见明细及月汇总卡各一条。source_ref是内部溯源ID；本证据没有表明它是密钥/令牌，不应描述为凭证泄露。问题是技术标识直接进入普通客户界面，且与“展示去内部来源”的目标不一致。

## 为什么现有自动验证没有证明这条链已清理

- `tests/agent/test_cards.py:18–55`只验证name/memo/merchant_name/alias等字段名被移除，未覆盖已包装counterparty/display_name/target_display的最终DOM。
- `frontend/src/mobile/AssistantWire.test.tsx:23`异常卡夹具使用裸`counterparty:'可疑商户'`，没有经过gather包装；upcoming夹具使用裸`target_display:'储蓄账户'`。它们验证wire字段与金额渲染，不能证明生产包装字符串显示正确。
- 同文件`:15`的helper主动以`screen.getByText('来源 src_wire')`定位卡片，意味着当前前端测试明确容许/依赖技术来源标识可见。
- 新query summary/compiled verifier测试覆盖聊天事实和回复校验。它们通过不能自动覆盖另行生成的UICard、SSE和前端字符串渲染。

本次没有新增或运行测试；以上是对已有断言范围的只读核验，不冒充新的红绿测试。

## 现场记录建议（不实施修复）

继续按原43逐条演示，分别记录“核心业务事实/最终回复”与“助手卡展示”。重点留存后续15/16/35的异常counterparty、17的账户display_name、24/26/27的商户或target_display、31/32的产品名与内部ID、34b的持仓产品ID；仅在实际出现时写现场发现，不用本报告静态可达范围代替观察。不要为复验改代码、去掉模型侧包装或重置旧结果。

该报告是对修复映射中“display-only去包装”的范围补充：聊天摘要已有对应实现，read card通路仍未清理。旧43原报告及本轮保存证据保持原样；本对话只演示和报告问题。
