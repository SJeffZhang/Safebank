# 执行状态独立架构审查

2026-10-23，review_execution_state，只读源码与PRD审查，无数据库/HTTP竞争实测。协调者记录并采用其最小设计，实际验收通过前不得声称协议已实现。

结论：保留L3 HTTP契约，以同attempt缩短deadline形成持久屏障；新增L2内部作业状态及受控条件迁移，不能给EXECUTING/UNKNOWN通用回退边。

## 现有L3语义与安全证明

`services/bank-backend/app/execution.py:42`：prepare/execute/reconcile共用intent行锁。`:108`：same attempt、same evidence/cap可把deadline更新为min(旧值,请求值)。`packages/contracts/src/safebank_contracts/plan.py:234`仅要求deadline<=cap，同attempt过期deadline可接受。execute持intent锁后检查attempt/evidence/deadline；attempt+1仅PREPARED可更新且cap不变。

仅在当前execute明确STATE_CHANGED、当前attempt已成功prepare后，调用内部fence_current_attempt：原customer/plan/attempt/hash/steps/evidence/cap不变，prepare的deadline_wall传固定过去时刻FENCE_AT（1970-01-01T00:00:00Z）。不能借未确认新审批证据升attempt占位。

- 旧execute先提交：fence返回COMMITTED，查询并归档真实结果，不生成新审批。
- fence先提交并返回PREPARED：旧execute因过期拒绝；旧same-attempt prepare的min不能恢复期限。
- 新批确认后才attempt+1重开deadline，仍不超过原cap。
- 旧fence/execute晚于n+1抵达，attempt不符拒绝，不能修改n+1。

保证来自L3持锁的持久更新与成功回包，不来自时间等待或query PREPARED/404猜测。首次prepare丢包不能走屏障重批。

## 最小状态与事务序列

仅ExecutionJobStatus新增FENCING、PAUSED，不增加PlanStatus。

| 情况 | plan / job | 行为 |
|---|---|---|
| 提交中 | EXECUTING / EXECUTING | 发送已持久化当前attempt |
| 明确STATE_CHANGED | EXECUTING / FENCING | 只发送当前attempt过期deadline屏障 |
| 屏障PREPARED ACK已落盘 | EXECUTING / PAUSED | 可以复演，禁止直接execute |
| 重批等待/已确认 | PENDING_APPROVAL或CONFIRMED / PAUSED | claim保留，L3期限仍过期 |
| 结果不明/锁丢失 | UNKNOWN / UNKNOWN | 查询/reconcile，不回审批、不升attempt |
| L3 COMMITTED/CANCELLED | EXECUTED或FAILED / 对应终态 | 归档并消费claim |

持同一有效客户锁连接，数据库短事务与HTTP分开：

1. 检查身份、原origin、scope/approval/cap，占claim，持久当前attempt/evidence/deadline/job EXECUTING，再prepare/execute。
2. 明确STATE_CHANGED后CAS EXECUTING,n→FENCING,n。此时不先撤审批、不释放claim；事务外发旧attempt屏障。
3. COMMITTED查询归档；PREPARED ACK后CAS FENCING,n→PAUSED,n，同时deadline=FENCE_AT。只有提交成功才复演。
4. 新快照重跑①–⑧，追加evaluation。view_hash等价保留原审批，证据关联新evaluation和approved_evaluation；不同则旧审批SUPERSEDED，新审批PENDING，不能先prepare未确认新证据；DENY/REPLAN不恢复deadline。
5. 下次G8持锁重核实时撤销/epoch/claim/cap/审批/新快照，CAS PAUSED,n→EXECUTING,n+1，同时存新evidence/deadline后才HTTP。新deadline=min(新审批期限,原cap)，不能用续签JWT扩cap。

普通transition仍拒绝EXECUTING→决策态。新增共享专用操作，在同事务检查job=PAUSED、l3_attempt=expected、deadline=FENCE_AT、plan/latest revision/approval归属/授权有效，再允许EXECUTING→PENDING_APPROVAL/EVALUATED/CONFIRMED/DENIED/REPLAN。不能只改通用字典。

屏障独立内部路径，不调用“执行deadline必须大于now”的正常业务校验。

## 故障与最小持久字段

prepare丢包、fence回包丢失、ACK后PAUSED落盘前崩溃：UNKNOWN，不重批不升attempt，reconcile终态。重启看到EXECUTING/FENCING也同样保守对账；看到已持久PAUSED才能继续暂停/复演。reconcile锁超时仍UNKNOWN，COMMITTED归档，CANCELLED才确定未执行。

无需generation/fenced_attempt列，条件是每个n严格无状态循环：EXECUTING→FENCING→PAUSED，不确定→UNKNOWN→终态；PAUSED继续必须n+1。l3_attempt+expected status+latest evaluation revision构成CAS。HTTP回调携带发送时标识，不读取最新job再套旧结果；n+1发送前必先持久EXECUTING,n+1。旧n ACK不能CAS覆盖n+1。若实现放宽同attempt重入，必须重审并补generation，不能继续沿用简化证明。

必需新列仍为authorization_evidence、deadline_cap_wall；已有deadline_wall保存FENCE_AT。PAUSED本身代表屏障ACK已持久化。

W4 locks.py交出裸连接：断连后本次owner必须永久失效；不得rollback自动重连后假装仍持session advisory lock。所有后续写入/发送前检查原锁连接身份，不换连接续跑；必须真实断连测试。

## W1/W3/W4联动与request_id

- W3 evaluation/repo.py:19追加evaluation需执行复演模式的PAUSED/attempt/revision guard。G8内部同plan新revision不是重新调用G4；G4重复request_id返回原Decision，G5/G8给最新状态。replay沿用持久origin/plan_id。
- W4 approval/service.py:147的EXECUTING/UNKNOWN禁止创建检查保留，先通过受控迁移再创建，不删除安全检查。
- G8按(customer,原origin,plan_id,request_id)绑定；已返回STATE_CHANGED的旧request_id不能在确认后重新发下一attempt，必须新request_id。持久缓存已完成409/202，G5给最新状态；未完成由job恢复。不能把HTTP放进request_idempotent.produce事务。
- W1 cancel/supersede/logout检查未终结execution_job。持客户锁先短事务提交epoch/会话撤销、审批失效，将需清理job/plan登记UNKNOWN且保留claim；事务外reconcile再归档。后台清理不依赖用户仍登录。CANCELLED原话保持CANCELLED，消费claim，不非法变CONSUMED。
- 有PAUSED job的计划不能只SUPERSEDED后遗忘L3 intent；没有job的普通待批计划可直接替代。
- 执行后REPLAN若需改steps：plan不可变、旧claim不可释放给新plan。旧任务保持暂停，用户新typed/hint生成新scope后reconcile旧任务，或cap到期清理。新G4不得复用占用scope；不引入未经批准的scope转移语义。

## 实施范围与强制验收

共享schemas/enums、state、tables、0002；W4执行/恢复/locks/approval；W3 repo/replay；W1生命周期清理接线；PRD7.2/7.4/7.8/7.10/8.5/9.5与ADR。无需L3请求响应或银行表变更。L3同attempt过期prepare行为是本协议依赖，修改须重审。

真实PG+真实HTTP可控竞争验收，不能用mock代替：

1. execute持锁先提交→fence COMMITTED，只归档一次；反序fence先提交→迟到execute零资金写，旧prepare不恢复deadline。
2. n+1后迟到n fence/execute均拒绝，n+1 evidence/deadline不变。
3. prepare丢包query404、execute在途queryPREPARED均不回审批；reconcile阻止迟到请求。
4. fence提交丢回包、ACK后本地落盘前杀进程→UNKNOWN且不升attempt；PAUSED落盘后重启仍暂停。
5. n+1持久后HTTP前终止锁连接→恢复reconcile，迟到请求不复活；旧n ACK在n+1后CAS零行。
6. n+1 prepare/reconcile双向竞争，终态不可复活。
7. 暂停期间取消/登出/G7拒绝/原话替代/cap到期，审批失效、intent终态、claim不复用。
8. cap不变，新批不扩期，屏障不被正常deadline校验阻挡。
9. G4/G8 request_id重复、origin错配、旧审批、同scope双plan；枚举/CHECK/专用条件更新一致，通用EXECUTING/UNKNOWN回退拒绝。
10. 金额检查真实plan_executions/账本/operation_events/证据，不只看HTTP状态。

协调裁定：采用该最小方案，先共享状态/迁移独立组件，再W4执行恢复和跨组件联调；源码审查不等于运行通过。具体任务书将记录失败条件与组件退出命令。
