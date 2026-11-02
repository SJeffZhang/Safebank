# 普通申购 / 新增定时七日预留独立复核

目标 `6ef246322630886901a917cbc73a7a4ab1920a5e`，parent `7b1e57a9d2376153ebd57143f8cfb28bc14daf74`；精确archive `/private/tmp/sbg-demo-repairs-20261031/reserve-review-6ef2463`。只审该单元五文件，未编辑root工作树。评审者先前发现过漏洞并提供外部复现，但未实现该修复，符合独立代码审查。

## 结论

**APPROVE 产品修复**。新增的 reserve 触发与账户hint查找未发现 P1/P2 安全/资金缺陷。原提交的PG/pure测试文件同名会使完整collect冲突；协调者已在root将PG文件重命名为 `tests/gateway/w3b/db/test_form_subscribe_reserve.py`，本人只读cmp确认内容逐字节不变。该重命名当前尚在root工作树，应随集成提交保留；不能把独立6ef不含此修正的完整collect称为已通过。

## 核验

- 仅把已有纯bankcore前后7日投影的触发范围从即时transfer扩至subscribe_product和schedule_transfer。MMF立即扣可用资金，非货币冻结可用资金、新增定时创造未来义务，均通过同一apply_plan与projected_shortfall比较，无另写余额或简化金额公式。
- 只在after总缺口大于before时产生SCHEDULED_SHORTFALL/USER REPLAN，既有前缀归因定位首个越界step。安全申购显示原真实预留；取消有效授权后再申购按实际after义务比较，不凭原先已有扣费造新阻断。没有自动修改用户金额或新增授权。
- B1仍跳过累计reserve；hard DENY仍优先，投影缺失不会覆盖既有适当性拒绝。原本可执行但义务数据不完整时仍503，不因扩范围而放行。
- `_failed_step_max`支持真实fund账户参数account_id，避免subscribe触发hint时KeyError；取值仍是纯投影的max_outflow，DecisionStage原hint许可不变，不给不支持工具自动扩金额。
- scheduler修改只涉及测试：无事件fixture置09:00前一分钟；固定2秒等待改为观测DONE/FAILED/RETRYING加30秒硬限，仍断言DONE和精确target时钟。没有改生产scheduler或放宽业务终态断言。

## 实际验证与边界

1. 精确archive：`PYTHONDONTWRITEBYTECODE=1 .../.venv/bin/pytest tests/gateway/w3a --ignore=tests/gateway/w3a/db -q --tb=short -p no:cacheprovider` → **44 passed in0.29s**。日志 `reserve-pure-review.log`。
2. 自写 `/private/tmp/sbg-demo-repairs-20261031/test_reserve_review_boundaries.py` → **8 passed in0.61s**。覆盖MMF/非货币B1、真实不足资金和account_id最大值/金额不改、适当性hard deny不被缺义务投影覆盖、取消后申购和ONCE七日内外边界。
3. 自写取消正控首次 **1 failed/7 passed** 是夹具只有raw obligations、未派生SubscriptionView，EntityStage按不存在实体拒绝；用生产纯 `_derive_views` 从同一真实raw snapshot派生视图后8全过。没有修改产品代码或绕过EntityStage，保留该夹具更正口径。
4. 该单元四Python文件ruff check及精确diff check通过；root新PG文件与archive旧名文件cmp一致。
5. 协调者报告：实际G19/G4旧码1红（错误进入审批）→修后REPLAN，无approval/execution；reserve与完整时钟真实PG **23 passed in19.19s**。scheduler诊断观测真实原终态2.23秒，支持旧2秒等待不成立。此处是协调者证据，本人未跑DB。
6. 协调者另报告root完成全unit4434pass/1116skip、FE453pass；本审查未复跑全套、不以其替代最终精确发布head门禁。

未运行真实DB、服务API、供应商模型、Docker、浏览器或部署；未修改共享AGENTS/PRD。
