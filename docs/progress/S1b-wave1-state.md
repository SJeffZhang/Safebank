# S1b 波次 1 持久状态进度

2026-10-23，专用分支 feat/l2-wave1-state，起点 97d5a33。AGENTS/PRD 指定章节、任务书、迁移清单、执行状态评审与 ADR-008 已读取。AGENTS 由协调者统一汇总，本 agent 不改业务接线。

## 组件 A：增量 schema

输入：冻结 0001 及带代表数据的旧库。输出：四 nullable 列、内部 FENCING/PAUSED、0002 revision/readiness。依赖：真实 PostgreSQL 16 tmpfs，历史迁移不变。验收：metadata 漂移、空库重复升级、旧数据保留、状态 CHECK、新旧 readiness、运行角色权限。失败条件：缺列/类型不符/数据丢失/权限放宽/旧库 ready。

红测：`.venv/bin/pytest tests/gateway/wave1_state/test_schema.py::test_wave1_metadata_requires_incremental_schema -q`：1 failed（revision 仍为 0001_gateway，预期红）；未提升执行产生 pytest cache 权限警告，随后真实 PG 命令提升运行。真实 PG 红测 3 failed（旧 revision/缺列/旧库 ready，符合预期）。实现后 19 passed / 1 failed：旧库升级测试子进程缺 PYTHONPATH，修正测试启动环境后，`PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/wave1_state/test_schema.py tests/gateway/db -q`：20 passed / 零 skip，5.79s。

## 组件 B：专用状态 CAS

输入：调用者已持有效客户锁且已开启短事务的同一连接、发送时绑定的身份/attempt/revision。输出：条件成功更新或 409，完整 claim 保留直至真实 L3 终态。签名已向协调者提报，等待冻结；不更改既有函数签名。

W4 须证明 STATE_CHANGED、屏障 PREPARED ACK、HTTP 结果身份、实时授权及客户锁真实持有；共享 helper 只验证数据库持久条件。本地造 PAUSED 仅验证数据库前置条件，不能证明 L3 屏障协议。

## 尚未覆盖

真实 L3 HTTP、丢包、崩溃、迟到请求与客户锁断连矩阵均由后续 W4 实测。本组件不操作 bank/L3、W1–W4 业务，不 push/PR/merge，不调用 make up/down/test-p1，不删除或重建任何 Docker 卷。


## B 接口冻结与职责

协调者批准：ExecutionIdentity(customer_id, plan_id, origin, attempt, revision) 为不可变 dataclass。以下函数均要求调用者已在同一真实客户锁连接开启短事务，不内部 commit、不换连接、不发 HTTP。持久身份/status/attempt/latest revision 不符为 GatewayError(STATE_CHANGED,409)；非法程序调用（无事务/无时区/非法 identity）为 ValueError。

- begin_fencing(conn, *, expected) -> None：调用者先证明成功 prepare 后明确 STATE_CHANGED；helper 检查 EXECUTING、归属/attempt/revision、证据/cap 完整和 claims。
- acknowledge_fence(conn, *, expected) -> None：调用者核实该身份的 PREPARED fence ACK；helper CAS FENCING→PAUSED 并保存 FENCE_AT。
- guard_paused_execution(conn, *, expected) -> None：W3 在同事务先锁旧 latest、追加新 evaluation，之后 publish 用新 latest revision；不能事务外拆开。
- publish_paused_decision(conn, *, expected, to, approval_id=None, now) -> None：只发布持久 PAUSED/FENCE_AT 的复演决策，校验匹配 Decision、审批归属/状态/有效期；不改 job 原 evidence/attempt。
- resume_paused_execution(conn, *, expected, authorization_evidence, deadline_wall, deadline_cap_wall, approval_id, now) -> int：返回新 attempt，原子保存 n+1/evidence/deadline，原 cap 非空且精确相等；调用者另证实时授权/撤销/epoch、MAC、快照、真实客户锁。
- mark_execution_unknown(conn, *, expected, from_status, reason) -> None：未终结任务失效/不确定先入 UNKNOWN，claim 保留。
- archive_execution_terminal(conn, *, expected, from_status, l3_status, result: ExecutionResult|None) -> None：调用者证明真实 COMMITTED/CANCELLED；COMMITTED 必须匹配 plan_id、EXECUTED 结果；CANCELLED 可 None，只归档 FAILED。归档才消费 claim，保留 CANCELLED 原话。

B1 红测 `... pytest tests/gateway/wave1_state/test_fence.py -q --tb=short`：12 failed（新接口缺失）；实现后 12 passed/零 skip，2.09s。并发两个 fence 仅一个成功；identity/attempt/revision、空 cap/evidence、错误 deadline、非 PAUSED、claim 已消费均拒绝。

B2 红测 `... pytest tests/gateway/wave1_state/test_resume.py -q --tb=line`：21 failed（publish 缺失）；实现后 fence+resume 合计 33 passed/零 skip，3.03s；出现一个测试 model_copy 风险枚举字符串 serializer warning，待改为真实枚举消除。测试覆盖先 guard/append/publish、attempt+1、旧 ACK 拒绝、cap 不可变、审批归属/状态/时效、DENY/REPLAN 保留 claim。

发现 S1 旧 PK 测试用普通 transition 消费 UNKNOWN job 的 claim，与 ADR-008 冲突。协调者授权最小测试例外：UNKNOWN 消费改 409，并用独立无 job claim 继续验证 PK。普通路径将保护 claim 消费、有关联 job 的终态归档；无 job 的生命周期仍可用。B3 红测：41 failed / 3 passed（未知/终态接口缺失与普通消费绕过暴露），实现后专项+授权例外PK测试84 passed/零skip，9.16s。已消除B2风险枚举serializer warning。随后新增过期cap与‘必须新evaluation’两项红测均各1 failed（原逻辑未拒绝），实现后专项+db+PK合计115 passed/零skip，11.31s。

普通 transition 保护：job 不能直接归档终态；有关联未终结 job 的 plan 不能普通进入执行/拒绝/失败/过期/替代等；有关联 job 的 claim/授权原话不能普通消费。普通 EXECUTING/UNKNOWN 回决策态仍非法，普通路径没有 FENCING/PAUSED 成功边。无 job 普通消费保留，真实 PK 重复消费仍409。普通 job UNKNOWN 更新也统一 plan→job 锁顺序。

真实PG B3矩阵覆盖四种未终结job × 执行/待批/拒绝/REPLAN/过期/替代等plan标签，全部先UNKNOWN保留claim，再由真实L3结果的调用方证明入口归档。CANCELLED原话不变；COMMITTED结果匹配与重复归档、旧attempt/revision拒绝、事务回滚均通过。此处仅验证数据库条件，不声称验证真实L3终态真实性。



## 完整退出与既有测试适配

初次六退出：lint 166 文件通过；make test 为 1 failed / 357 passed / 138 skipped（8.44s），唯一失败 `tests/gateway/test_ddl_drift.py::test_frozen_ddl_matches_metadata`。根因为旧测试要求冻结 0001 DDL 等于最新 metadata。协调者批准最小测试例外：复制 metadata，核验并移除明确四列、还原 job CHECK 后仍与历史 DDL 精确比较；历史 JSON SHA256 额外保护，不替代比较。实际诊断仅 evaluations/execution_jobs/otp_challenges 三条 CREATE TABLE 变化。首次诊断忘记 PYTHONPATH 导致 ModuleNotFoundError，补齐后成功；首次 clone 使用 to_metadata 造成约束文本排序不同，定点测试仍1 failed，读 unified diff 后改 deepcopy 保留创建顺序，定点1 passed。历史0001与schema_v1无改动。中间代码生成后的 ruff check 曾报告 E501/B023/未使用导入，已逐项格式化、绑定 loop lambda 并清理；最终 lint 全绿。

补齐新 PENDING 审批不能恢复、确认后才 n+1 的数据库条件回归（定点1 passed，1.72s），然后重新完整跑六条，全部 exit 0：

| 命令（均加 PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH） | 实际结果 |
|---|---|
| make lint | ruff check PASS；166 files already formatted |
| make test | 358 passed / 139 skipped，4.29s |
| make test-gateway | 129 passed / 91 skipped，3.48s |
| make test-gateway-db | 17 passed / 零 skip，2.12s |
| make test-wave1-state | 105 passed / 零 skip，7.90s |
| make contracts-check | PASS: contracts v1 match frozen schemas |

单元入口未启用真实 PG/秘密权限专用条件时相应测试明确 skipped；上述两个真实 PG 目标已启用，零 skip。额外授权 PK 测试此前与专项/db合计115 passed（11.31s），没有以 skip 冒充通过。CI 仅增加 `make test-wave1-state`，未在远端运行、不预称 CI 通过。

所有临时 PostgreSQL 显式 PGDATA=/var/lib/postgresql/data，精确路径 tmpfs；只销毁本测试创建的无卷容器。没有运行 make up/down/test-p1，没有操作既有业务库或删除重建任何 Docker 卷。

## 冻结与下一步

当前业务与测试已完成本地六退出，待本地 checkpoint SHA；只本地提交，协调者亲自复跑和独立评审后才可 PR。W1/W3/W4 必须按本报告接口接线，W4 后续完成真实客户锁、HTTP fence/迟到/丢回包/崩溃/reconcile矩阵；此处本地PAUSED造数不构成这些协议验收。
