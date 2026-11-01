# X2 只以实际有效月费派生新定时

基线 `03435f00e4da236a85fb425ceac404931cc6c5a5`（共用subscription_savings已交付）；分支fix/x2-effective-monthly-fees。只修改gateway provenance、对应纯回归与本记录；不修改helper、G9、cancel applier、S1、L3或共享AGENTS/PRD。

## 缺陷与最小修复

原cancelled_monthly_fees只sum ACTIVE MONTHLY SubscriptionView。纯Entity→Provenance→Intent实际运行：cap19899低于月费19900时，真实bankcore merchant返回BLOCKED/MANDATE_AMOUNT_EXCEEDED、无transfer/effects，但仍把19900认作derived并允许该scope；end_at在next_due前一微秒同样仍认derived。协调者新增G4 PG旧码亦4负控失败/2有效正控通过，日志由协调者保存，本人未跑数据库。

现对每个已匹配取消步骤的月费调用已交付共用`subscription_savings(state,instruction_id,customer_id)`。未知/覆盖不足(None)或明确无效/零月额拒绝整个正金额派生；不舍弃某个取消项后求部分和，不自动改零或改用户金额。再核对实际mandate对应raw billing的MONTHLY、billing/account/merchant/amount与所选view一致，避免支持WEEKLY折算的报告helper扩大X2每月限定或让失配view跨源账户派生。通过后仅sum已核实月额，保持完整模板、全部cancel步骤、去重、本人储蓄、同源账户、MONTHLY与原read/step来源规则。

## 红绿与夹具更正

- 首个旧码纯测：2 failed/2 passed，cap/end无效误认derived，有效end=None/恰好等于due正控保持。
- 扩展并校准完整raw日历/覆盖的最终13条测试，复制到精确基线archive重跑：**10 failed/3 passed in0.50s**，所有失败均是旧码误认derived；包括缺raw/覆盖、inactive、失配周期/账户/billing及两取消项中一无效仍整额派生。`/private/tmp/sbg-demo-repairs-20261031/x2-effective-final-red.log`。
- 修复后新文件及原派生两文件：**21 passed in0.71s**。相关W1/W2/W3b/W4b1纯范围最终**2007 passed/23 skipped in5.66s**，排除w1/w2/w3b的db目录；23项为原有未启用DB测试，不算通过。日志`x2-effective-green.log`、`x2-effective-regression.log`。
- 原正控fixture的raw next_due默认10/18与view11/01不一致，且raw covered仅A但BankState.versions含A/B。现同步真实period/anchor/weekday/time/next_due与完整覆盖，未放宽产品helper。第一次同步误把已转换datetime再fromisoformat造成fixture TypeError，修正类型；另缺raw在Entity已保守拒绝，缺raw provenance负控先取有效resolved steps后检查该阶段边界。均不计业务红测；最终精确旧码复跑上段13条无fixture错误。
- 新回归使用生产纯`_derive_views`，不手填正节省；invalid/valid都断言原计划金额保持不变。四个修改Python的ruff check/format与diff check通过。

本人未运行DB/API/provider/Docker/浏览器/部署。协调者已独占扩展`tests/gateway/w3b/db/test_current_authorization.py`的cap/end × NO_WRITE/SCOPED真实G4回归；集成本单元后在其隔离PG环境串行重跑该文件并保留2有效正控，再独审/最终完整CI。不得以本纯测代替这些门禁。
