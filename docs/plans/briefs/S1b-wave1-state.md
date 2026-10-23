# S1b 波次1持久状态与0002迁移

状态：任务书已准备，尚未实施。用户已批准受控共享补缺；由协调者统一安排独立共享agent串行追加，W1–W4不写迁移。前置S1已合并main；在S2纯函数组件完成后启动，开始前从最新main拉独立worktree/分支`feat/l2-wave1-state`。

## 目标、输入输出与范围

四路检查点均已汇总。输入为0001持久数据、已有W0模型以及`wave1-migration-requirements.md`；输出为兼容已有数据的0002 schema、匹配的tables/enums/readiness、受屏障约束的状态操作。此组件不实现G8或假造业务可执行状态。

先完整读AGENTS、PRD7.2/7.4/7.8/7.10/8.5/9.5、`docs/reviews/L2-execution-state-review.md`、迁移汇总。原有EXECUTING/UNKNOWN通用回退禁令保留；新接口签名先向协调者报告、固定后W3/W4消费。

允许修改：gateway `schemas/enums.py`仅内部ExecutionJobStatus；`state.py`；`tables.py`；`db.py`revision；新增`migrations/versions/0002_wave1.py`；相关`tests/gateway/db/`与新`tests/gateway/wave1_state/`；Makefile末尾专项；CI增加专项；`docs/progress/S1b-wave1-state.md`和验收报告。PRD/ADR由协调者统一记录或明确派发仅相应段落。不得改0001、schema_v1.json、EvalContext、授权矩阵、现有函数签名、W1–W4业务、L3或银行表。缺少业务接线记录待联调，不用成功stub。

## 组件A：增量schema

- evaluations.view_json JSONB nullable；otp_challenges.code_ciphertext Text nullable；execution_jobs.authorization_evidence JSONB nullable、deadline_cap_wall timestamptz nullable。
- nullable只为保留已有0001数据，后续新业务必须填完整值，旧缺证据任务不得凭空恢复提交。不得从其他JSON字段偷存数据。
- job.status CHECK新增FENCING、PAUSED，保持已有终态；不新增PlanStatus、generation或fenced_attempt。该简化依赖每attempt无状态循环，见评审。
- 保留既有roles/owner/append-only audit权限；0002只ALTER/add，不drop表、不清库、不修改历史迁移。
- readiness revision变为0002_wave1并实测旧库拒绝ready、升级后ready；不要仅更新测试常量。

先写真实PG升级回归：空库upgrade两次；0001带session/plan/evaluation/job/OTP/audit代表数据升级并保留；CHECK约束新旧合法状态、无效状态拒绝；列类型和table模型一致；运行角色仍无DDL、audit更新删除权限。用独立tmpfs PG，禁止删除/重建任何Docker卷，禁止在现有业务数据库TRUNCATE。

## 组件B：受控状态操作

- 普通transition对EXECUTING/UNKNOWN→决策态继续拒绝。新增专用操作，必须在同一锁连接短事务检查job=PAUSED、expected attempt、持久deadline=FENCE_AT、expected latest evaluation revision、plan归属和适用状态，再执行条件迁移。先明确哪些实时授权校验归W4调用者，不能把未校验项写成helper已保证。
- 专用CAS分职责冻结签名，不把所有操作都套PAUSED前置：进入FENCING由W4提供明确STATE_CHANGED，helper校验EXECUTING/attempt/revision；接受屏障ACK由W4核验对应fence PREPARED成功回包，helper校验FENCING身份并原子保存PAUSED+FENCE_AT；发布复演决策才要求持久PAUSED/FENCE_AT；恢复新提交由W4持有效客户锁核验实时授权，helper原子保存attempt+1/evidence/deadline并核验cap不变；待对账与终态归档原语保留claim至真实L3终态证明。helper不能独立证明HTTP ACK或客户锁真实持有，调用方须履行对应责任。
- 普通transition不得单独标记屏障成功或升attempt重新开放提交。首次prepare之前复演不需fence；首次成功prepare用attempt=1。不得为未发出的首次提交伪造FENCING/PAUSED。
- 每attempt序列EXECUTING→FENCING→PAUSED；不确定→UNKNOWN→L3终态；PAUSED重新提交必须attempt+1。旧回调CAS不得覆盖新attempt/evaluation。
- 明确哪些边由特殊helper完成，不能通过普通transition任意把EXECUTING标为已获屏障；本组件只测试持久条件，不把本地PAUSED造数当真实L3屏障安全证明。
- 取消/拒绝/过期时，已有未终结job必须进入待对账路径并保留claim。新增共享状态原语应支持该路径；W1/W4具体HTTP和清理由各owner实现。无job普通待批计划可保留现有直接替代。
- DENY/REPLAN和原话消费须服从claim仅在L3终态后消费；若发现评审方案中的状态组合需要补充，先报告具体冲突，不自行开放回退。
- 真实PG验证正确PK、过时attempt/revision/错误deadline/非PAUSED均拒绝、并发仅一个CAS成功、UNKNOWN普通回退拒绝、claims不释放复用。

## 退出与联调边界

`make lint && make test && make test-gateway && make test-gateway-db && make test-wave1-state && make contracts-check`。专项实际启用真实PG、零skip；CI加入相同专项。在本组件中不能声称真实L3 fence/丢包/重启竞争已通过：那些必须在W4执行组件运行评审所列真实HTTP矩阵。

每小组件红绿证据、实际退出结果、未覆盖项和SHA写进报告；只本地提交，不push/PR/merge，不自开agent、不跑make up/down/test-p1。协调者亲自复跑、独立只读review、CI全绿merge commit后，W1/W3/W4同步main联调。
