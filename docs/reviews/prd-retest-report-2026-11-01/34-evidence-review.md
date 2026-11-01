# 34 部分货币基金赎回现场独立复核

结论：**CORE_PASS；严格场景结果PARTIAL**。原句赎回已经通过真实助手读取/计划/确认/执行，订单、份额、成本与100000分即时到账正确；面板“未来7天预留0”与仍有效的45元未来扣费不一致，保留为展示残余，不抹去也不误判为赎回资金失败。未以34-business-oracle.json自身CORE_PASS字段代替原始证据。

固定head `8e2810e6764e3fd3e37406b31e694605751e0356`、代际 `demo_20261101131315_aca4156a`，plan `pln_01M6BNFB84VHMFP0113T64RCVN`，step `stp_redeem_mmf`，cus_ls。六份receipts代际/head一致，均RR+READ ONLY，跨库不是原子联合快照。

## 原句数量与成功读取来源

DOM原句“赎回安心货币1000份”；独立UTF-8 SHA256=`8ccbfbe73cb3c0a55adb26b9953585531482764d0b2bb32187493605ff7cf5e7`，与canonical34及本轮typed原话`utt_01M6BNEY9NZ4AXW7S3MAZ3S0QM`相同。原话span[6,10)逐字是1000，L2normalized_scope的units=1000.0000，计划未用持仓总10000替代用户部分赎回数量。

成功持久化的当前`get_holdings`读为`src_01M6BNF3TQH8HVFJEQS1AWX45A`：sid=ses_01M6BNEXB2XBCJ50SDRHNZS5QR，args={account_id:null,product_id:prd_mmf}，cus_ls版本1，created_at=13:41:00.375749Z；早于13:41:07.972939Z的plan。计划account_id和product_id分别明确引用该read `/holdings/0/account_id`、`/holdings/0/product_id`，units另从用户原话span取。此前也有get_accounts、list_products6项，未把失败/不存在的持仓尝试冒作已读取。完整响应正文未保存，本复核可验真实read记录/来源绑定及L3持仓，不声称独立展开了原JSON pointer正文。

## 确认前零已采业务写与绑定

34-before-bank和34-preconfirm-before-bank全部已采银行语义投影完全相等（只排除采集墙钟）；MMF仍10000份/成本1000000/预留0，活期账面1200000、原33 pending200000、可用1000000。该plan尚无L3execution_intent、plan_execution、订单、transfer、operation；gateway计划PENDING_APPROVAL、approval PENDING/confirmed_at为空且没有当前execution_job。

真实YELLOW确认面板列安心货币1000.0000份、预计本次模拟10/15 09:00即时到账、活期可用10000→11000元和v1风险揭示。root记录用户最终确认；本复核不从DB/DOM推断物理点击者身份。

提交后当前plan/job均EXECUTED，L3唯一当前execution_intent attempt1/COMMITTED、唯一plan_execution及唯一redeem_product step EXECUTED。approval `apv_01M6BNFBA27P7BNHY75PJ4TRB1`由PENDING→USED，confirmed_at=13:42:51.631907Z；L3evidence_summary准确引用此approval及原approved_evaluation_id，origin=agent/risk=YELLOW，当前scope/utterance已CONSUMED到同plan。

`confirmed_view_hash=final_view_hash=855c7164a3c0e012299cd0f1c6005e14265824c0cb0530a68f6236a63642e1d9`，等于实际approval.view_hash。plan_hash在gateway/approval/L3均为`89b57a54d0d8b0047e4f015520cb4fb5dee7c122b0ac0bf99ebed8ca85387eb5`。draft去arg_sources保留depends_on后的独立canonical SHA256=`63c64a166e02f085ab172065d0ff88fe81d5bb79c202dca310304983a45b2e30`，吻合gateway steps_hash及L3request/steps digest。

## L3订单、持仓与账本

| 事实 | 独立核验 |
|---|---|
| 订单 | 只新增ord_da1229a7-f39b-5a16-b23c-c8541814b18b，cus_ls/acc_ls_chk/prd_mmf，type=RED/status=SETTLED，units1000.0000，NAV1.0000，amount100000，reserved_cost100000，当前plan/step。confirm_at/pending_transfer_id/expires_at/failure_code为空；settle_at=2026-10-15 09:00+08。数据库trade_date=10/14 16:00Z是上海10/15午夜，不能当错误交易日。 |
| 部分持仓 | units10000.0000→9000.0000；cost1000000→900000；reserved_units0/reserved_cost0保持。赎回成本按可用成本比例100000，未卖出全部持仓。 |
| 唯一转账 | trf_2f78c706-bb38-56d9-9307-18fadbddca34，FUND_REDEEM/POSTED/100000，acc_fund_prd_mmf→acc_ls_chk，当前plan/step，无pending/event。只有一条对应客户IN/POSTED FUND_REDEEM流水tx_b16051b6-c454-56de-a384-e5bba7e20158。 |
| 活期 | credits_posted1200000→1300000；posted余额1300000，debits_pending原200000不变，可用1000000→1100000。储蓄及其余客户账户不变。 |
| 内部对手方 | acc_fund_prd_mmf.debits_posted增100000，余额0→−100000；这是允许负余额的INTERNAL结算账户，形成同金额借贷，不是客户透支。 |
| 唯一operation | evt_98093d03-9540-5987-bcac-c9d71a7a5580，FUND_REDEEM/100000/target prd_mmf/当前plan与step。order、transfer及operation的UUIDv5 derive均独立重算吻合。 |
| 守恒 | 全库posted借/贷15298860→15398860、各增100000仍相等；pending借/贷仍200000。 |
| 原33保留 | ord_023d2566-6a94-510d-8746-fe7571f1038f仍SUB/PENDING_CONFIRM/prd_stable/amount200000/units0；其原pending transfer trf_2125ba5b-a46d-564d-8a82-f67c87e9927d、确认/到期job及所有字段不变，未被34提前确认/释放/重复冻结。 |
| 其他业务 | 仅acc_ls_chk和对应MMF内部结算账户变化；其他15账户、卡、指令、billings、grants、风险、payees、拆账、产品/商户、event_jobs/event_executions/instruction_runs/clock等已采投影不变；cus_ls state_version1→2。 |

MMF本次已同事务到账，无需clock。当前真实分支含原33 pending200000，available=1100000；若之后38保持seed时钟、会员费4500且无其他变化，派生上限是**1095500分**，不是无33分支的1295500，也不是旧34失败分支995500。本报告不执行38。

## 展示残余：未来7天预留0

pending/result DOM都显示0；preconfirm真实evaluation和执行复演中的`preflight.scheduled_reserve_cents`也确实为0，不是单纯前端遗漏后补0。

银行时钟仍10/15 09:00+08。李四si_ls_video及si_ls_music均ACTIVE；各自账单仍active=true，10/20 06:00的3000分与10/22 06:00的1500分均落在未来7日窗口，合计**4500分=45元**，本例未取消任何代扣。这里45元指未来应付费额，**不是**赎回后可用余额；后者为1100000分。

精确head `gateway/app/pipeline/preflight.py:88`初始化PreflightResult；`97`的affects_reserve仅transfer/subscribe_product/schedule_transfer，未含redeem_product；纯赎回不会进`109`的_reserve，`context.py:118`默认scheduled_reserve_cents=0一路由`decision.py:152`传出，`frontend/src/mobile/Approval.tsx:241`直接贴“未来7天预留”显示0。与28纯取消留下默认0为同一范围问题，本例无取消前后费用差异，若展示真实既有7日义务应为4500分。

因此严格案例保留PARTIAL，不能将其上调为全项PASS。可支持的缺陷是把“本计划未跑预留投影”的默认0展示成了真实未来义务数；没有证据表明因此发生越权、资金短缺或MMF入账失败。赎回只增加客户可用现金，原33冻结及45元义务都未被改写。本任务不修代码、不回写旧报告或结果状态。

## 验证边界

未调用任何服务/模型/数据库、未推进clock，也未测试重复redeem、UNKNOWN/失败恢复或非货币赎回。collector只保留read摘要/哈希与L3evidence_summary，完整read正文、authorization_evidence/epoch/evidence_history及outbox未采，不宣称全库逐列或完整证据逐字节独验。所有结论基于本轮实际原始文件和已读精确源码，仅新写本报告。

## 收据哈希

| 文件 | SHA256 |
|---|---|
| receipts/34-before-bank.json | `85c7cb870d236a5b7536adfecca77b1d7912677f0578e8bf070d3ada12304df2` |
| receipts/34-preconfirm-before-bank.json | `27e45bdffa7cdbb86f2f5ab1f419caf84ba4ca8af11201390a634402f5045186` |
| receipts/34-after-bank.json | `fe385e34921fb445d6bd1d36af26c14de92dc8feeecca7bf85d039a317e7927e` |
| receipts/34-before-gateway.json | `e2fe8e7de4c7650563b8649704c64fdc86c6b553ed20ec1ed4dcee41afd01904` |
| receipts/34-preconfirm-before-gateway.json | `3b64621ebf63ffe853d249a7406c12c4e6cb965d157b5d85f5614ae06c2f95a3` |
| receipts/34-after-gateway.json | `c200933402e21c57261666f86d96904fb10e012e12317f849b193c13bdae8b90` |

辅助：34-business-oracle.json、screens/34-pending.txt、screens/34-result.txt；业务oracle声明已由上述原始差异独立重核。
