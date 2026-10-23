# S1e 无评估旧任务的受控对账兼容

前置S1c合并后串行实施，独立feat/l2-orphan-reconcile从最新main；复用已附S1b worktree，仅共享owner实施，W1不改state。输入：schema允许的live plan/job+CLAIMED，但该plan零条evaluation；输出：可持久UNKNOWN及真实L3终态后的安全归档。不能让W1撤销因构造revision失败而整体回滚，不能伪造evaluation或扩展普通提交许可。

先读AGENTS、PRD7.4/7.10、ADR-008、S1b报告及state.py。只允许state.py新增接口/最小私有复用、tests/gateway/wave1_state专属回归、进度/验收文档；无迁移/表字段/enum/其他业务修改，无新依赖。现有ExecutionIdentity与所有冻结函数签名不变，不支持revision=0/None，不让_locked_execution自动降级。

固定新增接口：
- OrphanExecutionIdentity(customer_id,plan_id,origin,attempt)，不可变，origin仅agent/app_form，attempt正整数。
- mark_orphan_execution_unknown(conn,*,expected:OrphanExecutionIdentity,from_status:ExecutionJobStatus,reason:dict)->None。
- archive_orphan_execution_terminal(conn,*,expected:OrphanExecutionIdentity,l3_status:COMMITTED|CANCELLED,result:ExecutionResult|None)->None，固定plan/job都已UNKNOWN。

两接口均要求有效客户锁下同连接的调用者短事务，不内部commit/换连接/HTTP。按plan→job→claims锁，核customer/plan/origin/attempt/预期status；明确零evaluation，最终条件UPDATE仍带NOT EXISTS。claims完整CLAIMED且与去重scope_ids一致，旧空evidence/cap可保留，但绝不能作为重新提交许可。先全校验后写，条件失败409 STATE_CHANGED，程序非法输入/无事务ValueError。

mark仅接受EXECUTING/FENCING/PAUSED/UNKNOWN的live job，plan不得EXECUTED/FAILED；只持久UNKNOWN/reason/更新时间，提交字段原样保留，不消费claim，完整校验后UNKNOWN可幂等。archive仅UNKNOWN：COMMITTED必须匹配plan、真实EXECUTED提交结果/时间/成功步骤；CANCELLED允许None，否则只能FAILED且无成功步骤。原子plan/job终态后才消费claim，CANCELLED/SUPERSEDED原话保持原状态。HTTP真实性、响应身份和客户锁由W4调用者证明。

真实PG先红后绿：四live状态+旧空证据；所有错身份/attempt/status/claim/scope无部分写；有任一evaluation两个接口拒绝；普通fence/resume/publish不能借orphan绕revision；并发mark（同旧状态仅一次）、UNKNOWN幂等、双终态归档只有一次消费；迟到/旧attempt/坏结果/PREPARED/404不消费；eval先提交拒绝缺失CAS，mark先持plan锁的合规writer后续须重查拒绝，直接SQL后来新增eval则orphan归档拒绝；事务rollback；两终态都保留CANCELLED原话。

W1集成责任：预期CAS竞争用savepoint隔离，重新读取后选择普通/孤儿路径，不能让预期409回滚应生效的epoch/logout撤销；真正SQL/连接故障不能吞成成功。HTTP期间若eval出现，不给旧orphan回包临时拼新revision，重新读取普通身份并重新查/reconcile再归档。scope_ids缺失/额外claim等畸形旧行不自动补数据、不放松claim校验，先报告。

退出make lint/test/test-gateway/test-gateway-db/test-wave1-state/contracts-check；专项真实PG零skip。沿用现有CI专项，不需新workflow。协调者亲自复跑、独立只读审、修复后立即push组件PR，精确head全CI成功merge commit。只本地提交，不push/PR/merge、不自开agent/up/down/test-p1/删重建卷。报告docs/progress/S1e-orphan-jobs.md准确失败/结果/调用者边界，不宣称真实HTTP恢复已验收。
