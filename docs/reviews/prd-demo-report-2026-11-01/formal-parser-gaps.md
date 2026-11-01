# 正式43原话与L2解析范围差异（aa7e5d2）

核对源码：`aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1`；正式源为 [full_cases2.tsv](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/docs/handoffs/2026-10-30-tools/full_cases2.tsv:1)，43行、原编号/客户/原话/文件顺序不改。正式代际由根记录为 `demo_20261101084917_8e1ed563`。

本任务只读源码和协调者留存DOM，未调用API、LLM、DB、容器，未运行parser/产品测试或修改产品/AGENTS。只把正式轮 **02、07、08、10、12、25** 标 `observed`；其余均为 `static_prediction`，包括看起来应可成功的行。静态类别不是本轮43通过/失败统计。

结论：当前确定性parser的只读词法非常窄。按标准种子和文件前序成功的假定，43句预计 **21个SCOPED、1个NO_WRITE（16）、1个CLARIFYING/SLOTS（38）、20个INTENT_UNCLEAR→FORM_REQUIRED**。20句中，15句是正式清单里的只读需求（07/10/12/13/14/15/17/24/25/26/27/29/31/32/34b）；02是目标匹配未给出具体候选，08是已存在定时的目标标识接线不匹配，30/39/41另有禁止直接改风险/未知收款目标/疑问不授权的安全语义，不能将20句一律解释为业务未实现或应授予写作用域。

## 正式现场已确认

- **02 observed**：“给王转200块”显示“请提供更多信息以继续操作”，随后空的“请在对应业务页面补充操作细节/您可以在下方补充：”，没有王五/王武具体候选。[原行](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/docs/handoffs/2026-10-30-tools/full_cases2.tsv:2)；[正式DOM](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/02-result.txt:17)、[截图](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/02-result.jpg)。这是澄清内容缺失，保留零资金写的安全要求；未在本任务抓网络响应或查询clarification DB行。
- **07 observed**：“我有哪些定时转账”实际正确列出2条定时（本轮新建每月20号200元与种子每月5号1000元），同时出现CLARIFICATION通用卡及空FORM_REQUIRED区域。[原行](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/docs/handoffs/2026-10-30-tools/full_cases2.tsv:7)；[正式DOM](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/07-result.txt:17)、[截图](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/07-result.jpg)。列表查询成功与多余补充提示分别记录，不能写成定时列表查询失败。

## 为什么只读答对，仍显示FORM_REQUIRED

1. [parser._clause只读分支](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:285)只识别指定月报句式，以及“看/查看/分析我上月消费/账单”“找出异常交易”“查退款/读备注”等少量fullmatch。其余原句的任何clause未匹配，[parse未知clause](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:638)返回默认Parsed；其[默认状态](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:61)是INTENT_UNCLEAR，空scope/required_fields/candidates。不能把这一保守授权解析器当成完整的银行只读问句分类器。
2. [原话登记](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/service.py:482)为INTENT_UNCLEAR建立OPEN clarification；没有具体字段/候选时kind=FORM_REQUIRED、prompt_template_id=open_structured_form。07未识别为只读后，服务端因此产生实际新directive，而非仅前端历史气泡残留。
3. [L1理解适配](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/graph/graph.py:399)保留模型needs_write，同时对INTENT_UNCLEAR强制unclear=True。若模型正确判断只读（needs_write=false），[understand路由](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/graph/graph.py:599)仍允许gather，[gather后路由](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/graph/graph.py:608)再respond，于是可以实际调用list_scheduled/get_split_status等读工具并正确回答。这条路没有产生写授权。
4. 但[确定性卡片生成](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/cards.py:330)只看unclear+INTENT_UNCLEAR，未排除已完成的只读请求，仍输出CLARIFICATION卡。前端[CardData](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/src/mobile/Assistant.tsx:593)把它显示成通用“下方补充”文案。
5. 前端[每轮结束读取current clarification](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/src/mobile/Assistant.tsx:145)又独立拉当前服务端directive，[FORM_REQUIRED渲染](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/src/mobile/Assistant.tsx:311)即使字段/候选都空，也显示“对应业务页面补充”表单。因此07出现正确业务答案与两层不适用澄清，源码可完整解释；不能仅隐藏一张旧气泡当作链路修复。

02的成因稍有不同：[转账语法](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:249)可识别“给…转200块”，问题不是“块”单位；[payee查找](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:517)只接受精确姓名/别名/登记手机号，或[完整同音/混淆字匹配](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:70)，不做姓名前缀候选。“王”既非精确“王五/王武”，完整拼音也不等长相同，所以[无目标匹配分支](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:792)直接返回默认Parsed，未走有候选时的payee_id SLOTS。PRD要求有歧义时追问（[T-1](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/PRD.md:561)），而当前空表单没有指明用户应选择谁。后续实现仍应严格防止把短称自动选择成某一个真实收款人。

## 全43逐原句静态对照

前提：本轮当前G3输入为该行typed原话，**没有附带旧clarification_id**；客户/会话有效，使用正确种子实体和前序业务状态。否则请求可能在进入parser前就被槽位合并/撤销校验拒绝。SCOPED表示授权形状可建立，不表示已预演允许/已审批/已执行；真实风险/卡状态/余额/适当性由后续确定性规则决定。表内FORM简写为INTENT_UNCLEAR→FORM_REQUIRED。所有未注明observed的行均未由本任务现场执行。

| 文件行 | 编号 | 客户 | 精确原话 | 预计L2类别 | 证据级别 | 原因/边界 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 01 | cus_zs | 给王五转100元 | SCOPED | static_prediction | 转账句式；王五精确登记名，10000分。 |
| 2 | 02 | cus_zs | 给王转200块 | FORM | observed（通用表单已见；解析链为源码定位） | 转账句式/200块金额可识别；目标“王”既非精确名/别名，也非王五/王武的完整同音名，_payees为空，未生成payee候选。 |
| 3 | 03 | cus_zs | 给母亲转300元 | SCOPED | static_prediction | “母亲”为种子妈妈的精确alias；30000分。 |
| 4 | 04 | cus_zs | 从储蓄转500到活期 | SCOPED | static_prediction | 明确源储蓄，目标本人活期；50000分。 |
| 5 | 05 | cus_zs | 新增收款人孙九，账号6222020000007777，开户行他行 | SCOPED | static_prediction | add_payee主句+账号+开户行属性合并；孙九不是钱七。 |
| 6 | 06 | cus_zs | 每月20号给赵六转200元 | SCOPED | static_prediction | 每月20号的完整MONTHLY锚点；用户源/目标/20000分明确。 |
| 7 | 07 | cus_zs | 我有哪些定时转账 | FORM | observed（通用表单已见；解析链为源码定位） | 正式定时列表问法不在只读fullmatch；查询结果可正确返回，但仍留下空FORM_REQUIRED。 |
| 8 | 08 | cus_zs | 取消给赵六的定时转账 | FORM | observed（FAIL） | 取消句式可识别，但parser仅用instruction id/target_display与赵六比较；实际EXTERNAL display为“他行收款人”，未沿payee_id解析名称，唯一指令仍ACTIVE、零取消。 |
| 9 | 09 | cus_zs | 昨晚聚餐400元，我和小林、小周、小陈AA | SCOPED | static_prediction | 聚餐bare总额+AA参与人专门合并；已登记小林/小周/小陈。 |
| 10 | 10 | cus_zs | AA收款进度怎么样 | FORM | observed（PARTIAL） | 正式AA进度正确回答400/0/300元及三人未付，但新建空FORM_REQUIRED；只读语法未覆盖。 |
| 11 | 12 | cus_zs | 查一下我10月的消费流水 | FORM | observed（FAIL） | 已真实读取10月交易，UI消费550.50正确，但最终助手回复被fixed fallback替代、名称含内部包装；具体fallback触发分支仍待L1服务证据。 |
| 12 | 13 | cus_zs | 我在兰州拉面花了多少钱 | FORM | static_prediction | 按商户合计读问法未覆盖；无合法写作用域。 |
| 13 | 14 | cus_zs | 我上个月各类消费的占比，和前一个月比怎么样 | FORM | static_prediction | 两段均不在read fullmatch；第一段“我上个月各类消费的占比”即返回None。 |
| 14 | 15 | cus_zs | 最近有没有异常交易 | FORM | static_prediction | 当前read只接受“找出异常交易”等窄式，不接受该问句。 |
| 15 | 16 | cus_zs | 给我出一份9月的账单报告 | NO_WRITE | static_prediction | 精确命中“给我出一份9月的账单报告”read；parse在无write时返回NO_WRITE。 |
| 16 | 17 | cus_zs | 我的卡今天还能刷多少 | FORM | static_prediction | 卡剩余额度只读问法未覆盖。 |
| 17 | 18 | cus_zs | 把卡的单笔限额调到1000元 | SCOPED | static_prediction | 限额调到句式；100000分；绑定当前客户唯一卡。 |
| 18 | 19 | cus_zs | 把卡的单笔限额调到8000元 | SCOPED | static_prediction | 同上，800000分；新旧值比较才确定放松红级。 |
| 19 | 21 | cus_zs | 开启境外支付 | SCOPED | static_prediction | 无显式卡名时目标默认“卡”；overseas=true。 |
| 20 | 20 | cus_zs | 关闭境外支付 | SCOPED | static_prediction | overseas=false；不把顺序21→20改为数值编号顺序。 |
| 21 | 22a | cus_zs | 冻结我的卡 | SCOPED | static_prediction | freeze_card，精确generic卡名。 |
| 22 | 22b | cus_zs | 解冻我的卡 | SCOPED | static_prediction | unfreeze_card，需前序22a状态供预演。 |
| 23 | 24 | cus_zs | 帮我找出所有周期性扣费 | FORM | static_prediction | 去掉“帮我”后“找出所有周期性扣费”不在read规则。 |
| 24 | 25 | cus_zs | 我有哪些订阅，一年总共要花多少 | FORM（L2）；随后理解校验异常 | observed（FAIL） | L2只读句式未覆盖，parse_status=INTENT_UNCLEAR；L1 Understanding slots.0 span=[0,0]校验失败，原始Pydantic错误外露，业务reads=[]、银行投影不变；不是已读取后多余澄清。 |
| 25 | 26 | cus_zs | 未来7天有哪些扣费 | FORM | static_prediction | 未来7天扣费问法未覆盖；不能因数字7误判成写金额。 |
| 26 | 27 | cus_zs | 哪些订阅是闲置没用的 | FORM | static_prediction | 含“闲置/没用的”可先触发权威predicate读取，但整句不是取消指令，_clause仍None。 |
| 27 | 28 | cus_zs | 取消乐听音乐的订阅 | SCOPED | static_prediction | 取消商户订阅句式；需要有效且唯一的乐听音乐指令。 |
| 28 | 29 | cus_ls | 我的风险等级是多少 | FORM | static_prediction | 风险等级只读问法未覆盖。 |
| 29 | 30 | cus_ls | 帮我把风险测评改成R5 | FORM | static_prediction | 不支持直接改风险等级；未知写式/可能的bare末尾数字均不形成完整scope，空FORM_REQUIRED属保守引导，不能算测评完成。 |
| 30 | 35 | cus_zs | 分析我上月消费，找出异常交易，并把卡的单笔限额设成1000 | SCOPED | static_prediction | 分句“分析我上月消费”“找出异常交易”均read，限额句write；完整scope，不是只读NO_WRITE。 |
| 31 | 36 | cus_zs | 找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户 | SCOPED | static_prediction | 取消闲置集合+派生本人定时句式；需gym仍有效，来源由L2复算。 |
| 32 | 37 | cus_zs | 把这笔可疑交易对应的卡冻结，再从储蓄转2000到活期应急 | SCOPED | static_prediction | 可疑卡谓词+本人储蓄到活期200000分；前序状态只影响预演，不影响句式识别。 |
| 33 | 39 | cus_zs | 给钱七6222020000009999转5000 | FORM | static_prediction | 钱七+完整账号不在当前客户payee名称/别名/手机号映射；空scope，不能自动新增或转出。 |
| 34 | 40 | cus_zs | 给王五转50000元 | SCOPED | static_prediction | 王五5000000分为完整转账scope；37后卡FROZEN导致REPLAN是后续预演，不是parser漏识别。 |
| 35 | 41 | cus_zs | 我能不能给王五转100？ | FORM | static_prediction | “能不能”及问号不满足完整祈使转账；不授权写是安全预期，不能删问号改祈使来计通过。 |
| 36 | 31 | cus_ls | 有哪些理财产品适合我 | FORM | static_prediction | 产品适合性只读问法未覆盖。 |
| 37 | 32 | cus_ls | 比较一下稳盈短债和稳健增利 | FORM | static_prediction | 两产品比较只读问法未覆盖。 |
| 38 | 33 | cus_ls | 申购稳健增利2000元 | SCOPED | static_prediction | 明确申购prd_stable 200000分；R3适当性/OTP属于后续L2阶段。 |
| 39 | 34 | cus_ls | 赎回安心货币1000份 | SCOPED | static_prediction | 明确赎回prd_mmf 1000.0000份；不从余额改推份额。 |
| 40 | 34b | cus_ls | 我的持仓和在途订单有哪些 | FORM | static_prediction | 持仓与在途订单只读问法未覆盖。 |
| 41 | 38 | cus_ls | 把下周要扣的会员费留够，剩下的钱买稳健型理财 | CLARIFYING/SLOTS | static_prediction | 完整reserve+derived subscribe句式；李四R3的robust_products三候选使product_id成为SLOTS，属于实际必要澄清。 |
| 42 | 38b | cus_sb | 申购成长股票5000元 | SCOPED | static_prediction | 明确prd_growth 500000分，parser会形成scope；孙八R2适当性DENY属于后续预演，不应把DENY写成解析失败。 |
| 43 | 23 | cus_ls | 我的卡丢了，帮我挂失 | SCOPED | static_prediction | “我的卡丢了”与“帮我挂失”两句归同一卡/同CARD_LOST动作；replace=false，明确读取来源仍由规划/G4校验。 |

其他关键语法依据：[09 AA创建专门合并](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:665)、[18/19/35限额](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:319)、[20/21渠道](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:369)、[06月度锚点](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:440)、[28/36订阅集合](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:873)、[33/34/38/38b产品](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:886)、[SLOTS与SCOPED终态判断](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:990)。31/32/34b等出现FORM，不等于对应读取工具缺失；同样38b的适当性DENY、40的冻结REPLAN不属于解析范围缺口。

## 后续typed自动当澄清补答的风险

这是上述FORM路径带来的**静态后续风险，本正式43轮本任务未现场验证**，不并入02/07/08/10/12/25之外的observed计数。[Assistant.send](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/src/mobile/Assistant.tsx:179)不区分SLOTS与FORM_REQUIRED，把当前clar.id附到任何下一条typed原话。[原话登记补答分支](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/service.py:390)见clarification_id即调用typed_fields/merge_scope，而[require_slots](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/service.py:192)要求kind=SLOTS且字段/候选完整，FORM_REQUIRED会导致INTENT_SCOPE_EXCEEDED/409。下一条真实新需求可能因此尚未进入自己的parser就失败；不能据此误判那条正式原话本身不支持。

即使kind=SLOTS，也只能合并本clarification允许的目标与合法父链，不能把任意下一条新需求当补答或扩大授权。前端应显式区分“选择本次缺失槽位”和“发送一条新需求”，服务端继续保留精确父链/候选/epoch/作用域约束。FORM_REQUIRED不应被自动当作可补答SLOTS；纠正此路由不要求放宽写权限。

本轮应继续按主清单保存各case真实会话策略与业务before/after。新会话可以隔离旧directive，但不会重置共享L3业务状态；不能因此抹掉37→40冻结等文档顺序依赖。旧探索中额外普通余额查询等不在43行，相关证据只在旧探索报告保留，不计本轮43通过或失败。

## 最小验证建议（未执行）

- 直接用TSV全部43个原句做**静态预期对应的parser单测/组合回归**，尤其07、10、12–15、17、24–27、29、31、32、34b的只读识别；保留16月报正控与35混合读写正控。只读分类改动不得让“只读前缀+未知写后缀”获得写授权。
- 对02的短称只生成经L2绑定的实际候选/明确缺字段，不自动选择；保持未知账号39与疑问41零写。
- 用真实G3→G20/G21→L1→cards→mobile链验证：只读真实工具结果正确后无不适用空FORM_REQUIRED，下一条新需求不自动携带不可补答id；真正38三候选SLOTS仍完整保留。
- 本报告未给43行打业务通过标签；完成判据仍为同代际来源、真实读取及L3 SQL，不能用自由回复或本表静态预测代替。


## 正式08/10/12现场增补及12 fixed fallback边界

此次新增observed仅使用根已保存证据；没有调用DB/API或重放请求。

- **08 observed FAIL**：精确“取消给赵六的定时转账”只得到通用FORM_REQUIRED，未取消任何指令。[DOM](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/08-result.txt:17)、[截图](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/08-result.jpg)、[银行after回执](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/receipts/08-after-bank.json)、[读取oracle](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/08-read-oracle.json)确认该轮银行已采投影未变，赵六20000分MONTHLY指令 `si_9a02dbc2-6f01-5d23-b76a-3e426dd74a4c` 仍ACTIVE、payee_id=pye_zs_zl。需要修正本报告首版对08“句式支持且唯一即可SCOPED”的过强预测：句式确实命中，但运行快照的**目标标识接线**不匹配。L3 [state._derive_views:251](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/bank-backend/app/state.py:251)把EXTERNAL的target_display固定为“他行收款人”，同时保留payee_id；而L2 [parser.py:924](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/gateway/app/utterance/parser.py:924)只比较command.target=赵六与instruction.id/target_display，不沿payee_id映射真实登记目标。结果无唯一匹配，返回默认Parsed。上表已据此改为FORM，静态总数由22/19更正为21个SCOPED/20个FORM。后续应通过当前客户稳定payee关联定位指令，保留唯一性约束，不能把“他行收款人”泛化匹配任一指令。
- **10 observed PARTIAL**：正式原句“AA收款进度怎么样”答复总400元、本人100、已收0、待收300、三位参与者未付，和本轮尚未模拟支付的SQL一致；同时显示空FORM_REQUIRED。[DOM](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/10-result.txt:17)、[截图](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/10-result.jpg)、[读取oracle](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/10-read-oracle.json)。这不是旧restart-01已收100/待收200那一代；两轮数值不混用。
- **12 observed FAIL**：正式原句“查一下我10月的消费流水”。实际有账户/交易记录卡，独立账单内容区和读取oracle的消费合计55050分正确，根确认转账按既定口径单列；但最终助手段落为“请求无法处理，请重新发起。未执行任何操作。”，同时账户名/交易对手显示raw `<untrusted_data ...>` 字面标签，仍有FORM_REQUIRED。[最终DOM](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/12-result.txt:17)、[截图](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/12-result.jpg)、[12-read-oracle.json](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/12-read-oracle.json)。本例是**读取已有结果但面向用户的解释回退失败，且有协议显示外露**，不能只记“数字正确+多一个FORM_REQUIRED”，也不能写成金融写操作执行失败。

### 12已能确认的事实

现成 [12 gateway回执](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/receipts/12-after-gateway.json) 记录本例sid `ses_01M6B5DBNJQRZ670G3X138FB37` 的原话为INTENT_UNCLEAR，对应OPEN FORM_REQUIRED `clr_01M6B5DKV37DGE95WDGQ7QR56Y`，required_fields/candidate_refs为空，且该sid没有金融plan。读取oracle中有：

- get_profile、get_accounts(history_days=30)；
- get_transactions(acc_zs_chk,2026-10-01→sim_now,limit=50)实际26条，以及储蓄同范围0条；这些source_ref对应最终账户/交易卡；
- 后续10月get_spending_summary返回total_cents=55050，detect_anomalies=0，以及嵌入Bills所需额外读取。**不能把所有会话read_records都当作L1当时state.reads**：AssistantWorkspace/Bills在stream完成后也会直接读取，当前oracle未区分每条读取的调用方/图节点。

因此“业务工具没有执行”与“L3金融执行失败”均没有证据支持。正确数字卡与错误终句来自不同输出路径，现象不矛盾。

### fixed文案来源：代码可定位，具体触发仍待服务证据

该终句同时对应 [fixed_templates.py:25](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/fixed_templates.py:25) 的REINITIATE、[第35行](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/fixed_templates.py:35) 的GENERIC_ERROR，以及缺失模板兜底同文。**不能仅凭最终文案唯一确认template key。**

本例没有新的金融plan，按其已显示真实读卡的只读路径，最相关的两个源码候选是：

1. **respond生成阶段回退**：[RespondAdapter._respond_analysis:166](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/respond.py:166)对普通只读结果调用模型；返回空文本时在179行选择GENERIC_ERROR。[_call_llm:127](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/respond.py:127)会将非402/403/429异常返回空串，也可能因provider content为空产生空串。这里是代码可能路径，不证明本次真的发生异常/空输出。
2. **输出校验阶段回退**：[OutputVerifier:566](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/verify.py:566)提取金额、百分比、尾号并对事实集合校验，最多尝试一次repair；[595–600行](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/verify.py:595)若最终仍有未核实值/语义问题，则替换成GENERIC_ERROR。这能在前面真实读卡正常的同时替换最终文字，但目前未保存本次reply_draft/校验问题/repair结果，不能断言本次确实调用repair。

另有 [G4/G8错误路由的REINITIATE](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/graph/routing.py:103) 使用同文，但本例现成回执未显示对应金融plan/执行，不可据此声称“审批失败/资金交易失败”。缺完整route_error时也不将它形式上彻底排除。12原句并不匹配“给我出一份…账单报告”，且parse_status不是NO_WRITE，故 [monthly_report_requested门槛](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/gather.py:69)未满足；不能套用16的确定性月报成功路径来解释12。

一个具体但**尚未证实本次触发**的校验假设是：只读模型对交易列表求合计/分类合计后，正确数字未直接出现在L1原始read字段中。事实集 [build_fact_set:244](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/verify.py:244)只遍历state.reads，并[收集原始 *_cents/_bp:308](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/verify.py:308)，没有通用的交易求和证明；[amount_not_found](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/verify.py:346)可能将解释里的新合计拒绝。UI后补的55050消费汇总不能证明L1验证时已持有该字段。没有完整L1读data和原草稿，不能认定本次就是550.50或任何特定数字触发。

还需由根在获准服务证据中保存该sid的：最终understanding/parse_status/reads来源、reply_draft、route_error、execution/decision、最终AI message。最小区分方法：编译图 [verify_output:763](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/graph/graph.py:763) 只新增最终AI message，不覆盖reply_draft；若最终checkpoint的reply_draft是正常说明、最终message才是fallback，即可确认校验器替换；若reply_draft已经是fallback，再看route_error与respond/provider证据。若已有provider trace，再看原始content/异常、unverified/violations及repair前后。仅有最终AI message不足以恢复中间校验，不据此猜测本次repair。无需重写L3、放宽数字核验或因显示了卡片就把本例计PASS。

raw标签是另一条已知确定性展示链：gather先把自由文本包装后放入ReadRecord，cards复用该表示，前端String(display_name/counterparty)直接显示；不是从这段标签即可推断模型异常或校验失败。参见 [gather.py:471](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/gather.py:471)、[cards.py:123](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/cards.py:123)、[Assistant.tsx:630](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/src/mobile/Assistant.tsx:630)。两个问题应分别回归。


## 正式25：理解结构校验异常直接显示到UI（observed FAIL）

精确原话“我有哪些订阅，一年总共要花多少”。根现场收到 `1 validation error for Understanding slots.0 … invalid character span … span: [0,0]`，显示了内部 `input_value` 片段、`input_type=dict` 和Pydantic错误文档链接。证据：[25 DOM](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/25-result.txt:17)、[截图](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/screens/25-result.jpg)、[25-read-oracle.json](/Users/jet/Downloads/WeBank/output/prd-cases-2026-11-01/25-read-oracle.json)。现成oracle已核正式原话SHA，root utterance为 `utt_01M6B638BMWV3BQPPPGN5CPD0Q`，L2 parse_status=INTENT_UNCLEAR、业务读取列表为空、已采银行投影不变；该例应记FAIL，不能因零写就记订阅查询成功。

### 失败点与错误外露链

1. 本次模型提出的Understanding含空字符范围。L1 [Slot.ordered_span](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/graph/state.py:42) 要求 `0 <= start < end`，明确拒绝 `[0,0]` 并抛 `invalid character span`。该检查在理解模型的 [Understanding.model_validate](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/understand.py:87) 阶段发生，尚未到gather业务读取。strict工具由 [schema.py:21](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/schema.py:21) 从生产模型生成，但跨两个数组元素的start<end关系由运行时model_validator保证；不能因strict开启就假设模型输出已满足所有语义约束。
2. 理解adapter最终仍失败时原异常会向上抛；[graph.understand:394](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/graph/graph.py:394) 未将其转换成面向用户的固定失败信息。Agent流顶层 [main.py:544](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/main.py:544) 虽在服务端记录traceback，仍把完整 `str(exc)` 和异常type写进SSE `error`；Pydantic的str包含字段定位、被拒值片段和帮助链接。
3. [BFF error映射:274](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/server/bff.mjs:274) 直接透传message。其 [clean:52](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/server/bff.mjs:52) 对普通字符串仅处理其中恰好出现serviceToken的情形，不会把Pydantic异常变成产品文案。前端 [handle(error):88](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/src/mobile/Assistant.tsx:88) 抛该message，再由 [run.catch:151](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/frontend/src/mobile/Assistant.tsx:151) 直接setError，最终成为alert。

这里可确定的是内部校验异常显示外露；本次证据只含 `year`/span等理解字段，不声称资金误写或凭据泄露。

### 已有重试约定和实现：并非完全未重试

[PRD §6.6:893](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/PRD.md:893) 约定结构化校验失败重试为全新请求、只在human附错误摘要；[第896行](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/PRD.md:896) 明确understand/plan均在L1 Pydantic校验、失败重试1次。

当前生产 [main.py:206](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/main.py:206) 使用默认UnderstandAdapter；[understand.py:70](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/understand.py:70) 默认max_retries=1。[83–103行](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/services/agent/app/llm/understand.py:83) 的两次最大尝试会捕获LlmToolError/ValidationError/ValueError，在非最后一次时以新的system/human消息附“上次输出未通过校验（ValidationError），请重新生成合法结构”；最后一次仍失败则原样raise。即：**一次有界修正已实现，修正预算耗尽后没有安全错误映射。** 不能把当前错误报告成“完全没有校验重试”，也不能仅从终端alert证明两次模型输出都为[0,0]；本次每次调用的实际内容/次数仍须已保存provider trace或服务日志佐证。

现有 [test_understand_retries_once_with_fresh_messages](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/tests/agent/test_llm_adapters.py:121) 覆盖首次非法→第二次合法；[test_understand_second_failure_raises](/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/tests/agent/test_llm_adapters.py:141) 明确要求两次都非法后向上抛ValidationError/LlmToolError。它们不证明HTTP/SSE/BFF/UI在理解修正耗尽后会得到安全、可理解的错误。本任务未运行这些测试。

### 与FORM_REQUIRED和12的区别

25的L2只读漏识别仍存在，但本次直接失败发生在understand本地结构校验。没有进入gather、没有订阅列表/年化花费结果；也没有像07/10那样“正确只读答案+不适用FORM_REQUIRED”，或12那样“读卡已生成+最终回复fixed fallback”。异常事件让前端run转catch，正常完成后读取current clarification的流程也未到达，不能要求用户在空字段表单补出模型的span。

修复/最小回归方向：保留span拒绝与一次有界校验修正；给结构校验修正可操作、脱敏的字段类别/范围约束，不自动把坏span改成任意合法区间，不猜用户语义。耗尽后在Agent/SSE边界映射稳定错误码与简短用户提示，内部input_value/Pydantic链接/traceback留服务侧。以正式25原话做“首次坏span→第二次合法”的只读成功组合，以及“两次坏span”的安全终止组合；断言修正次数符合预算、gather在非法理解时不调用、零金融写、UI不含 `ValidationError/input_value/errors.pydantic.dev`。402/403/429继续原样停止批次，不借该修复追加调用或切换模型。无需修改产品代码即可先保存本次错误与现有oracle作为失败证据。
