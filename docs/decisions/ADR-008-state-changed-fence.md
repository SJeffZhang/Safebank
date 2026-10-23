# ADR-008：STATE_CHANGED后先封闭旧attempt再复演

2026-10-23，采纳；设计已独立只读审查，尚未完成实现或真实HTTP竞争验收。依据用户受控共享补缺授权、PRD安全不变量和[执行状态评审](../reviews/L2-execution-state-review.md)。

明确STATE_CHANGED不能证明其他迟到execute不会提交。复用L3既有同attempt prepare的deadline=min语义，在原证据与不可变cap下缩短到FENCE_AT；只有PREPARED ACK及L2 PAUSED均持久后才允许复演/重批。COMMITTED以真实结果归档；回包不明和失锁均UNKNOWN，仅对账。没有新L3 wire字段。

内部ExecutionJobStatus新增FENCING、PAUSED，不增加PlanStatus。每attempt严格无环，继续必须attempt+1；CAS绑定status、attempt和evaluation revision，HTTP回调不得套用最新job身份。专用共享状态原语检查持久屏障与归属，实时授权由W4调用者核验；不放开通用EXECUTING/UNKNOWN回退。

0002_wave1增量保存view_json、OTP密文、authorization_evidence和deadline_cap_wall，保留0001数据；旧空证据不得恢复提交。取消、拒绝、替代、登出和过期须对已有job对账，claim只在L3终态后消费。G8旧request_id不能在重新批准后触发新attempt。

先独立迁移/状态组件，再W4执行恢复与W1/W3接线。强制验收包括execute与fence两顺序、旧attempt迟到、丢回包/崩溃、数据库锁连接断开、取消/拒绝/登出及cap不扩期，检查真实PG账本和证据。详细矩阵见评审，不以mock或本地PAUSED造数冒充协议验收。
