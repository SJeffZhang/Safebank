# S1e：无 evaluation 旧任务的受控对账兼容

2026-10-23，分支 feat/l2-orphan-reconcile，BASE c381a427b27893dcd2924bd5ba332fa6d4413262。唯一需求 docs/plans/briefs/S1e-orphan-jobs.md；沿用 S1b 的执行屏障/claim 边界，不修改既有冻结接口、迁移、schema、enum 或 W1/W4 业务。AGENTS/PRD/ADR 由协调者同步。

## 组件 1：孤儿 UNKNOWN

输入：同一有效客户锁连接上的调用者短事务，OrphanExecutionIdentity(customer_id, plan_id, origin, attempt)、预期 live job 状态、reason。输出：plan/job 持久 UNKNOWN 或409，claim及提交字段不变。依赖：独立tmpfs PostgreSQL。失败条件：身份/attempt/status不匹配、任何evaluation存在、claim/scope不完整、写入部分生效或旧证据/cap被补造。程序非法输入/无事务ValueError。

专属fixture从未创建evaluation。红测命令：`PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/wave1_state/test_orphan_unknown.py -q --tb=line`，实际27 failed/零skip（3.17s），均因新增identity/helper缺失。仅实现新UNKNOWN接口后，同命令27 passed/零skip（3.11s）。

## 组件 2：配对终态归档

固定只接受plan/job均UNKNOWN，继续验证最新evaluation明确不存在及完整CLAIMED；COMMITTED必须匹配真实EXECUTED结果，CANCELLED可None并归档FAILED，归档后才消费claim；取消/替代原话保持原状态。HTTP真实性、响应绑定、实时锁持有均是W4调用方责任。

## 组件 3：并发与调用边界

验证mark/终态竞争、evaluation出现的双向竞争、savepoint隔离撤销、事务rollback、普通屏障/复演/恢复不能接受孤儿身份绕过revision。W1真实HTTP撤销和W4真实L3对账不在本组件内实现或冒称验收。

协调者进一步确认scope边界：复用现有_claims，重复scope同样409；不放宽历史畸形数据。当前未push/PR/merge、未自开agent、未up/down/test-p1或删除重建任何卷；只使用既有显式PGDATA+tmpfs夹具。


组件2红测命令：`PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/wave1_state/test_orphan_terminal.py -q --tb=line`：29 failed/零skip（3.40s），新增归档函数尚不存在。实现仅复用旧终态结果校验与最终消费的私有代码，保留现有公共签名及旧guard；随后配对专项+既有test_terminal共84 passed/零skip（5.14s）。成功归档两种终态、None/真实失败结果、取消/替代原话保留、坏结果/身份/状态/claim/evaluation拒绝均通过。

并发/事务测试命令：`... pytest tests/gateway/wave1_state/test_orphan_races.py -q --tb=short`：10 passed/零skip（2.60s）。含并发mark/归档各仅一次、UNKNOWN幂等、真实数据库同连接故障注入在初查后插入eval以证明最终NOT EXISTS生效、两顺序plan锁竞争、后来直接SQL新增eval阻断孤儿归档、savepoint隔离预期409保留撤销、整体rollback。

边界测试新增真实PG证明：有evaluation时误把OrphanExecutionIdentity传普通fence/publish/resume会触发缺revision的程序异常；补强现有私有_locked_execution只接受ExecutionIdentity，不改变冻结签名或降级逻辑。实际边界命令 `... pytest tests/gateway/wave1_state/test_orphan_boundaries.py -q --tb=short`：5 failed / 18 passed（2.42s），均为误传新增身份后已有evaluation路径的缺revision AttributeError；严格类型guard后全状态专项203 passed/零skip（12.52s），含新增空cap/evidence终态归档。重复scope按协调者确认复用现有_claims严格409；不放宽畸形旧行。


## 完整退出准备

边界复核后的普通沙箱`.venv/bin/ruff check . && git diff ...`因.ruff_cache临时文件写权限拒绝而exit2，后续diff未运行；没有伪称lint通过，完整退出将按已授权提升权限运行。该失败是本地缓存写限制，不是自动审批拒绝。未修改禁止文件，已记录该环境失败，随后提升执行完整六退出。

W1的savepoint回归是本地真实DB调用模式证明，未调用真实G3/G14；W3竞争用合规writer见证行锁后状态重查，未冒充真实append_evaluation联调。真实HTTP身份/ACK/终态与持锁由W4后续证明；若HTTP期间出现evaluation，旧orphan回包不得补当前revision，必须重新取得普通身份并重新查询/reconcile。


最终输入自审：新增identity的origin传list时红测1 failed/6 passed/21 deselected（0.22s），因set成员判断抛TypeError；仅新增类先检查str后转为约定ValueError。顺带移除测试未使用select import（提升lint曾F401，不曾视为通过）。全状态专项随后209 passed/零skip（11.07s）。

协调者要求确认非法持久scope JSON受控409；对两孤儿接口分别覆盖JSON null/bool/number/string/object及数组中的非字符串/空字符串/嵌套值。先跑 `... pytest tests/gateway/wave1_state/test_orphan_boundaries.py -k malformed -q --tb=line`，实际10 failed / 10 passed / 28 deselected（2.31s），其中JSON null/bool/number与嵌套list/dict抛原set() TypeError；孤儿shape guard后同命令20 passed / 28 deselected（2.32s）。只在孤儿私有guard验证array和非空string元素，然后复用原_claims严格重复/归属/状态校验；不扩大普通路径修复，不自动补数据。


## 完成项与最终六退出

完成：独立不可变OrphanExecutionIdentity；仅最新evaluation明确不存在的UNKNOWN及配对终态CAS；同连接短事务、plan→job→claims锁序；最终UPDATE含NOT EXISTS及owner/status/attempt；证据/cap/deadline/attempt保留；完整严格CLAIMED、重复/非法scope受控409；UNKNOWN幂等；终态后消费claim并保留CANCELLED/SUPERSEDED原话。共享旧终态结果校验/消费私有代码，所有已有冻结函数签名与ExecutionIdentity保持不变，普通路径没有缺revision降级。

2026-10-23最终六条按顺序完整执行，全部exit0。每条均显式前缀 `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH`，无自选目标替代：

| 精确目标 | 实际结果 |
|---|---|
| make lint | ruff check通过；174 files already formatted |
| make test | 392 passed / 300 skipped，4.43s |
| make test-gateway | 163 passed / 252 skipped，4.18s |
| make test-gateway-db | 17 passed / 零skip，2.76s |
| make test-wave1-state | 229 passed / 零skip，12.71s |
| make contracts-check | PASS: contracts v1 match frozen schemas |

单元入口未开启PG/秘密权限专用条件时如实显示skip；上述两个真实PG目标显式启用GATEWAY_DB_TESTS=1，均零skip、无xfail。状态专项包括原S1b的105项及S1e新增124项（含8个纯identity测试与116个真实PG案例）。未运行的其他专用目标未被上述计数冒充。

## 未覆盖与冻结边界

W1真实G3/G14 HTTP撤销联调由W1 owner完成；本组件只证明savepoint隔离预期CAS冲突可以保存epoch/revoked_at/CANCELLED，以及正确孤儿CAS可同事务持久UNKNOWN。W4仍负责真实客户锁、HTTP真实性/身份与COMMITTED/CANCELLED对账，未执行真实L3恢复/丢包/断锁矩阵。W3真实writer接线待其owner，测试仅证明共享plan行锁后的状态重查模式；若evaluation在HTTP期间出现，不能把旧orphan回包临时补revision。

无迁移、表、enum、PRD/ADR、W1/W4业务、L3/银行契约或CI工作流修改；现有CI已运行test-wave1-state，新用例自然纳入。仅state.py及专属测试/证据文档。所有Postgres沿用既有显式PGDATA+tmpfs夹具，未up/down/test-p1、未删或重建任何卷。无新依赖、无subagent、无push/PR/merge。

本地六退出通过；实现与本报告一起形成checkpoint，具体SHA由交付消息与git log给出。协调者亲自复跑、独立只读评审、远端CI及合并均尚待完成，不据本地green声称已发布。


提交前额外AST只读核对：首次校验脚本对无返回注解的旧函数调用ast.dump(None)导致TypeError（脚本错误，未修改生产/测试）；给None显式处理后重跑，existing_function_signature_changes=[]，ExecutionIdentity_ast_unchanged=True。所有旧函数签名及原identity AST逐项与BASE一致。staged diff --check通过，暂存清单仅上述允许8文件，禁止路径无改动；随后仅本地提交。


## 修复轮 1：独立评审唯一 P2，空 scope + 零 claims

2026-10-23，修复BASE5842d44703b43811daa402b2afde08e63d1d1d28。已完整读取根repo `docs/reviews/L2-S1e-orphan-jobs.md` 并核源码：孤儿shape guard接受[]，原_claims两个空集相等，导致mark与archive都通过；现有missing_scopes测试保留原claim，未覆盖这个组合。接受该P2，普通路径不扩大修改。

先仅新增两个真实PG回归，分别以EXECUTING plan/job调用mark、UNKNOWN plan/job调用CANCELLED归档；scope_ids=[]且删除该plan全部claims，调用前原话ACTIVE。要求409 STATE_CHANGED，完整plan/job/claims/utterance字段snapshot不变。红测精确命令：`PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/wave1_state/test_orphan_boundaries.py -k empty_scopes_and_zero_claims -q --tb=short`：2 failed / 48 deselected（2.49s），两入口均DID NOT RAISE GatewayError，符合缺口复现。

生产修复仅在_locked_orphan_execution现有shape guard增加空列表拒绝（or not scopes）；不改_claims、普通路径、冻结签名、schema或其他业务。同一精确红测命令转绿：2 passed / 48 deselected（1.84s）；两例均确认409 STATE_CHANGED，完整plan/job/claims/utterance snapshot不变，ACTIVE原话未消费。


修复轮1要求的三文件回归精确命令：`PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/wave1_state/test_orphan_boundaries.py tests/gateway/wave1_state/test_orphan_unknown.py tests/gateway/wave1_state/test_orphan_terminal.py -q --tb=short`：116 passed/零skip（5.69s）。

随后完整按顺序重跑六退出，每条前缀均为 `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH`，全部exit0：

| 修复轮1精确目标 | 实际结果 |
|---|---|
| make lint | ruff check通过，174 files already formatted |
| make test | 392 passed / 302 skipped，4.29s |
| make test-gateway | 163 passed / 254 skipped，3.82s |
| make test-gateway-db | 17 passed / 零skip，2.19s |
| make test-wave1-state | 231 passed / 零skip，13.20s |
| make contracts-check | PASS: contracts v1 match frozen schemas |

只有孤儿guard的空列表拒绝、两个专属PG回归和本修复日志三文件改动；普通路径、签名、schema、W1/W4及L3均不变。临时PG继续显式PGDATA+同路径tmpfs，无up/down/test-p1、无删或重建卷。仅本地冻结，未push/PR/merge、未派agent；待协调者亲自重跑与原reviewer定点复核唯一P2。真实HTTP/L3及调用者持锁真实性边界沿用初始报告，未宣称本轮覆盖。
