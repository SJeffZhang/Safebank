# 实际商户扣费授权到期边界独立复核

目标 `fba93ee979baa93ddc4ca8992a22a4818033ab9e`，parent `8d0ac42446fb9b15f6e25262f44e40a145babed3`。源码来自精确archive `/private/tmp/sbg-demo-repairs-20261031/expiry-review-fba93ee`，只复核该提交六文件增量。评审者未实现新增到期guard；之前本人所写merchant metadata不在本审查范围，未自审其安全结论。未编辑root或作者工作树。

## 结论

**APPROVE**。本次未发现需要修改的P1/P2问题。新guard对真实周期due时间检查ACTIVE授权是否已结束，等于end_at仍允许，过期使用原BLOCKED/MANDATE_NOT_ACTIVE终态，没有新增授权来源或改变资金路径。

## 核验

- 到期比较与due均经原 `_business_time` 归一到Asia/Shanghai；按具体账单due时间执行，同一绝对时刻的UTC/+08表示不改变判定。ACTIVE状态不能单独使过期授权恢复许可。
- 过期分支先于ledger/post_transfer，沿原被拒扣分支只推进本期billing cursor并追加run；effects、账户、transactions、transfers均不增。等于截止时刻正控仍走原真实转账，未把inclusive边界改成提早拒绝。
- replay继续由同billing+due的实际run去重，拒扣/成功都不会重复执行。新增PG通过真实EventService检查账本ID集合、余额、游标、source version及终态旧版本重放，源码断言没有靠返回文字冒充资金事实。
- 旧events_api测试从已不再生产的SCHEDULED_TRANSFER过滤改为用本次真实transfer_id找账并明确断言P2P_EXTERNAL；不足资金检查同时包含canonical两类与旧历史类，不减少资金负控。

## 本人实际验证

1. 精确archive `tests/bankcore/test_events.py` + `tests/bankcore/test_merchant_mandate_expiry.py`：**15 passed in0.11s**，日志 `expiry-pure-review.log`。
2. 自写 `/private/tmp/sbg-demo-repairs-20261031/test_merchant_expiry_review_boundaries.py`：**6 passed in0.08s**。end_at位于due前一微秒/相等/后一微秒 × UTC或原+08表示；实际applier断言正确终态、拒扣零资金变化、游标推进和重复调用无效果。日志 `expiry-review-boundaries.log`。
3. 四个修改Python文件ruff check、精确提交diff check均通过。

未运行真实DB、API、模型、Docker、浏览器或部署。协调者真实证据：旧pure两例1fail/1positive、旧PG1fail；修后新增PG及整个events_api **11 passed in4.50s**。本评审未独立重跑。保留原acceptance的历史fixture更正：首次green把既有种子账本误当空、后混比全表/客户scope，最终改为同scope前后比较；不把这些fixture失败计为产品红测。最终集成精确head完整CI仍由协调者执行。
