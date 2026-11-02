# OTHER 定时转账双客户 scope / version 独立复核

源目标 `350e2a6eaf1c8a750af28437965cb74eb2748ec0`（parent `799df5b2826bec0c1660d410256563d4c20bbb4f`），root映射 `8d0ac42446fb9b15f6e25262f44e40a145babed3`。精确源码archive `/private/tmp/sbg-demo-repairs-20261031/peer-review-350e2a6`；本人只读diff确认源/root的events.py与新纯测试无差异。仅审该提交三文件，不自审本人之前的交易分类/累计修复；评审者未实现本单元。

## 结论

**APPROVE**。没有发现需要修改的P1/P2问题。已闭合真实OTHER定时缺peer装载与版本屏障的问题，仍需最终集成精确head CI及规定现场验收。

## 核验

- scope仅由真实当前客户standing_instruction、同客户payee、目标account实际归属构造；callback的额外expected_versions不能引入客户/账户，不用缺失版本的当前值代填。外部target=null及本人to_account继续单客户；他人payee或不存在实际目标拒绝。
- OTHER在event job行锁后取clock FOR SHARE，再按customer_id升序FOR UPDATE锁所有实际涉及state_versions；源/peer缺行、缺expected、旧expected或旧clock均在load/persist前STATE_CHANGED。与现有计划执行的clock屏障/排序客户锁一致，没有跨HTTP持锁。
- version fence后重读instruction/source/payee/directtarget/实际targetowner绑定，变化立即失败；装载双客户state后仍运行原bankcore事件applier和persist。追加独立负控证明锁间源账户、to_account、目标owner变化均不load或写账。
- 仅实际SUCCESS且有真实transfer_id时双方version各+1；BLOCKED、FAILED_INSUFFICIENT只推进源义务游标/源version。既有OTHER金额、真实收款账户及账本逻辑未替换成假响应。终态重放先返回原execution，允许旧/缺新版本重放而不重复写账或增版本。
- S1正常路径仍先fresh transfer snapshot/原grant再评估，并由_pinned传入全部涉及版本；此单元没有在L3生成授权或缺peer expected时假成功。REMINDER/fund/D4分支不受新的scheduled scope分支影响。

## 本人实际验证

1. 精确archive运行作者列出的纯范围：

   ```text
   PYTHONDONTWRITEBYTECODE=1 .../.venv/bin/pytest tests/bankcore \
     tests/obligations_data/test_scheduled_peer_runtime_unit.py \
     tests/obligations_data/test_reminder_runtime_unit.py \
     tests/obligations_data/test_reminders.py -q --tb=short -p no:cacheprovider
   ```

   → **526 passed in0.79s**，日志 `peer-pure-review.log`。
2. 自写 `/private/tmp/sbg-demo-repairs-20261031/test_peer_review_boundaries.py` → **8 passed in0.30s**：锁后源/直接目标/owner变化、缺真实peer version/account、实际PostgreSQL SQL FOR SHARE与排序FOR UPDATE、外部路径不因callback多键扩scope、终态旧版本重放不再写。日志 `peer-review-boundaries.log`。
3. 精确archive两个修改Python文件ruff check通过；源提交diff check、root映射相应文件无差异通过。

上述SQL AST内存测试不能替代真实事务回滚/并发死锁验证，本人没有运行DB、API、模型、Docker、浏览器或部署。

## 协调者真实PG证据

- 旧产品检查点真实 **3 failed /3 passed**：OTHER实际BLOCKED/NOT_FOUND、peer旧/缺version不拒绝；已有OWN/EXTERNAL及BLOCKED正控通过，夹具已先断言payee/实际目标/客户存在，不将不存在ID当业务红测。
- 协调者报告root `8d0ac42` 最终该六条真实PG **6 passed in3.77s**；覆盖双方真实账本/余额和version、终态重放、peer缺旧版本全回滚、BLOCKED只增源、OWN/EXTERNAL不改。未由本人独立复跑。

## S1 实际 snapshot / pin 链路追加复核

追加独立纯测试 `/private/tmp/sbg-demo-repairs-20261031/test_s1_peer_snapshot_review.py`，在精确350e archive运行 **2 passed in0.51s**。调用生产 `_re_evaluate`，经 `EvalContext.bank_state` 发送真实合成transfer steps；直接调用生产backend snapshot端点函数（无HTTP）及其中真实 `resolve_scope`、`scope_for`、`load_bank_state`，最终经实际SnapshotStage和 `_pinned` 取版本。SQL结果来自内存行，未手工构造快照的versions。源11、实际peer47、无关客户99：OTHER实际pinned为 `{cus_a:11,cus_b:47}`；EXTERNAL为 `{cus_a:11}`，均排除无关客户。证明正常S1步骤能令生产resolver/loader实际选入peer版本，而非仅direct L3 PG手工传两版本。

本追加仅隔离快照契约：将本轮run_pipeline替换为实际SnapshotStage，其他policy决策及执行由既有规则/协调者PG覆盖；没有冒称完整S1真实服务验收。首次内存SQL evaluator缺`IS NULL`表达式导致2个fixture错误，补齐该SQL表达式后2绿，未修改任何生产文件。日志 `s1-peer-snapshot-review.log`。
