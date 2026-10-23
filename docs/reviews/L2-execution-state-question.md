# 执行状态独立架构审查任务

本文件是问题与证据清单，不是已批准的新协议。2026-10-23，协调者；用户已批准受控补共享缺口，W4在G8前停留检查点。

## 必读与现状

- PRD §7.4、§7.8、§7.10、§8.5、§9.5、§10.2。
- main `services/gateway/app/state.py`：EXECUTING仅到UNKNOWN/EXECUTED/FAILED，注释认为重评仅发生在进入执行之前；execution_jobs无等待新审批状态。
- main `services/bank-backend/app/execution.py`：prepare/execute/reconcile共用intent行锁；execute版本/clock/解析digest不一致抛STATE_CHANGED并回滚，intent仍PREPARED。query PREPARED不证明不存在迟到execute。
- prepare允许PREPARED时attempt恰好+1并保存旧evidence；COMMITTED/CANCELLED不可复活；same attempt绑定旧evidence和deadline，deadline_cap不变。reconcile会写不可逆CANCELLED，无法继续同plan重批。
- W4进度及实现：`/Users/jet/.codex/worktrees/safebank-w4-execution/WeBank/docs/progress/W4.md`；W3 repo：`/Users/jet/.codex/worktrees/safebank-w3-decision/WeBank/services/gateway/app/plans/repo.py`。
- 字段清单：`docs/plans/briefs/wave1-migration-requirements.md`。

## 请给出的结论

1. 最小可行状态设计：明确收到L3 STATE_CHANGED、响应丢失/超时UNKNOWN、重新审批期间、重启恢复分别如何处理；不得只给所有EXECUTING通用回退边。
2. 证明如何阻止旧attempt迟到提交。单个STATE_CHANGED响应只证明该请求未提交，是否还需要attempt fence？评估锁丢失后另进程恢复与旧请求竞争；不得把query PREPARED/404当未执行证明。
3. 若只靠现有接口可以完成，给具体事务与HTTP顺序、claim持久占用、旧approval失效、新approval确认、immutable cap和evidence一致性。不允许未确认的新审批证据冒充批准。若需要最小内部契约扩展，明确为什么不可避免及所有需改文件/PRD/迁移，先提案不实现。
4. G4同plan新revision与G8反复调用如何保持request_id幂等及原origin；取消/登出在等待重批期间如何安全终结已有L3 PREPARED intent并消费claim。
5. 列具体真实PG与HTTP竞争验收：旧attempt迟到、advance/reconcile竞争、重批前重启、COMMITTED抢先、超时不得回退、状态CHECK与条件UPDATE一致。

只读评审，不改代码、不运行全栈、不创建/清理Docker卷。引用真实文件行号，对未确定部分明确说明；不要用未实现的替身证明协议安全。
