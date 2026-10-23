# W3/W4接续的真实依赖门禁（2026-10-24，准备稿）

依据旧检查点代码实查，不改变函数签名/共享状态协议；W2完整合入后才派发。当前只有计划，未启动W3/W4实施。

## 核对事实

- W3检查点3bcbe8e的evaluation/repo.py在append_evaluation内调用W4的planview.build_view；G4的api/plans.py在需要审批时调用approval.service.create_for_evaluation。当前main这两个接口仍NotImplementedError。
- W4检查点3856a4c已有面板/审批初稿，但依赖后续已合的0002/view_json、runtime密钥、现行客户锁与屏障语义，不能把旧locks.py覆盖现行W4a实现；旧disclosure_token额外wire已被协调裁定删除。
- 因此“先把完整W3合入，再开始任何W4”会使真实G4门禁缺依赖；不能用fake builder/approval通过后宣称真实G4完成。

## 最小接续顺序

1. **W3a预演/工作流/决策**：W3从最新main同步，先核diff/共享结果槽，接W2和S3a真实snapshot/reserve，完成纯阶段与真实PG read边界。完整G4仍待下面W4面板，不伪称W3全组件通过。
2. **W4b面板/审批/OTP先决组件**（W4a客户锁已完成，勿重用该名称）：从旧W4检查点提取其owner范围、同步最新main；保护现行locks与state，不覆盖旧版本。实际验收build_view/创建审批/view_json/确认与OTP状态机，移除未批准的disclosure_token响应。预演输入可用准确EvalContext边界fixture；明确不算G8资金执行。独审/PR/四CI后合入。
3. **W3b G4持久化/复演/hint**：在已合W4b上用真实W1 G3→W2来源链→W3流水线→真实build_view/create_for_evaluation，零替身审批的真实PG/HTTP门禁，完成请求幂等、预算/原话消费、F FORM、审计失败与复演。完整W3独审/PR/四CI后合入。
4. **W4c执行/UNKNOWN恢复**：依赖W3b与现行ADR-008/S1b/S1e专用CAS，实施G8/G5与恢复工作器，真实HTTP+PG迟到竞争/屏障/取消/失锁/attempt/evidence不可变矩阵。完整W4独审/PR/四CI后合入。

共享代码一次只开一条，交叉输入仍按wave1-common。若W3a发现build_view所需结果槽存在真实冲突，先写缺口由协调者固定，不复制W4实现到W3、不更改EvalContext或通过默认空预演绕过依赖。每个子组件都先红后绿、协调者亲验、DeepSeek独审后发布，不能以计划替代实现。具体任务启动前在各旧任务书上追加准确白名单和退出命令。
