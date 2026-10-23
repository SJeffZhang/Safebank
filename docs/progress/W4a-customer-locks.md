# W4a 客户锁组件

2026-10-23；分支feat/l2-customer-locks，基线main691cf88。原W4分支3856a4c冻结未动，不提取审批/OTP/G8。

## 小组件契约与门禁

输入为集中生命周期现有AsyncEngine/max_executions及非空客户ID集合，输出固定customer_lock(customer_ids)专用AsyncConnection。锁键依PRD使用hashtextextended('cus:' || id,0)，排序去重、5秒超时、信号量。数据库短事务与HTTP间隙共享原会话。

已向协调者报告并获同意：新增async assert_lock_owner(conn: AsyncConnection)->None及LockOwnershipLost。owner记录原driver identity/backend PID；连接实例事件阻止失锁后rollback/自动重连续写或commit；guard发送前核对原会话。失锁后本attempt交UNKNOWN/reconcile，不自动重获续跑；guard不能消除check-to-send窗口，本组件不宣称HTTP竞争已验收。

先红后绿：先验证缺实现的503行为，再验证真实PG锁互斥/设置/取消/池复用，最后复现kill后rollback可重连的风险并封闭。验收五命令make lint、make test、make test-gateway、make test-gateway-db、make test-customer-locks；专项必须真实PG零skip。失败、未覆盖、SHA逐项记录。

## 红绿记录（实施中）

- 缺实现红测：`pytest tests/gateway/customer_locks/test_contract.py -q` → 1 failed，实际NotImplementedError而预期配置缺失503。最小实现后18项配置/参数测试通过。
- 真PG失锁红测：终止原backend→首条SQL失败→rollback→旧owner重新SELECT成功，`test_killed_owner_cannot_rollback_reconnect_and_keep_writing` → 1 failed，DID NOT RAISE GatewayError。加入原driver/PID与实例begin/statement/commit guard后该回归转绿。
- 首轮专项出现1 failed/27 passed：测试裸SELECT绑定整数被asyncpg推断为text；改测试SQL为显式CAST(integer)，未放宽生产逻辑。
- 提前close红测：原AsyncConnection.close还池时未释放会话锁，cleanup对已关闭实例invalidate又抛ResourceClosedError。加入按原DBAPI身份过滤的pool.checkin保护后，专项33 passed（7.42s）。
- 增补实际并发提前close与正常退出交错测试，准确触发 `RuntimeError: deque mutated during iteration`（1 failed），证实每owner增删共享pool dispatcher不安全。已报告协调者，待确认稳定的pool生命周期dispatcher设计后修复。
- 静态检查发现测试长行/unused变量并修正；未改生产金融规则。

## 最终实现与接口

- `configure(db: AsyncEngine,max_executions:int)->None`复用集中runtime现有engine，仅创建进程Semaphore并在该现有pool安装一个稳定checkin dispatcher；重复configure不重复安装，不新建池。正整数严格排除bool/float/str。
- `customer_lock(customer_ids: Iterable[str])->AsyncIterator[AsyncConnection]`返回原签名和真实AsyncConnection。验证非空ID集合、去重排序；SQL严格为 `pg_advisory_lock(hashtextextended('cus:' || :cid,0))`。专用连接跨多个短事务持锁，5秒lock_timeout，超时409 EXECUTION_IN_PROGRESS。结束按PRD pg_advisory_unlock_all，恢复进入前的lock_timeout值（并非硬编码RESET默认）；取消/异常时回滚未提交事务。
- `assert_lock_owner(conn: AsyncConnection)->None`为协调者已批准的新增异步发送前guard；原会话roundtrip校验backend PID及全部预期单bigint advisory锁，不换连接，不提交调用者已有事务；在事务间隙自行开闭只含SELECT的小事务。
- `LockOwnershipLost`继承GatewayError，Code.DEPENDENCY_UNAVAILABLE、503。锁owner保存原DBAPI/asyncpg driver identity、backend PID和不可复位lost位。连接实例begin/before_cursor_execute/commit监听阻止失锁后的SQL、新事务和有事务提交；rollback不能复位。首次发现断连的DBAPIError若传播出上下文会归一化为LockOwnershipLost；被业务内部捕获后，后续SQL/新事务/guard仍拒绝。无有效事务的commit本身只是SQLAlchemy无操作，不会提交数据。
- `Connection.close()`无ConnectionEvents通知，因此稳定pool dispatcher在checkin按原DBAPI身份匹配，只invalidate该record。提前close时原物理backend在还池前被丢弃；`dbapi=None`表示SQLAlchemy已经丢弃物理连接，只清owner关联。无全池dispose/invalidate。每次owner关联存于pool record.record_info私有键，所有退出路径清理；不同engine/其他owner不受影响。
- 每个pool的单dispatcher保持pool生命周期；不在dispatch过程中增删共享listener。Connection实例监听在finally解绑，owner的record关联同样清除。失效连接不作为仍持锁连接归池；原owner与新owner严格分离。
- 未被调用者观察的提前close/失锁在上下文退出时抛LockOwnershipLost；已报告的失锁不会在退出时重复覆盖调用者错误。

## 最终红绿闭环

稳定dispatcher后并发close/正常退出回归通过；新增提前close未被查询时上下文静默成功红测（DID NOT RAISE LockOwnershipLost），通过独立reported位使首次未报告失锁在退出抛503，之后全部39项通过。记录原owner的lost位永不被rollback清零。

已实测：未配置503；非法IDs/max值；PRD同键互斥、去重及排序；多客户反向输入无死锁；不同客户并行与Semaphore上限；部分获得后timeout/任务取消释放；正常/异常/取消三种退出不污染池；非默认lock_timeout恢复；多短事务；终止backend后rollback不能重连续写；有未提交实际sessions INSERT时终止backend后commit被阻止且PG无该行；显式invalidate；guard验证原PID/全部锁且不提交调用者事务；提前close丢弃原物理会话；新owner同客户可获锁；不同owner/engine隔离；None checkin分支；record复用无残留；实例监听解绑；重复configure只一个pool dispatcher；实际并发close/listener清理。

## 共享测试的最小授权例外

首次 `make test` 实际3 failed / 243 passed / 75 skipped：共享runtime的三个生命周期单测用仅有dispose的Engine fake，真实locks.configure需要AsyncEngine.pool。已报告并获协调者明确白名单例外，仅改 `tests/gateway/shared_runtime/test_runtime.py`：三个使用Engine fake的单测显式引用非autouse `fake_engine_lock_hook` fixture，并断言传入同一db与max_executions。保留原真实pool容量/18连接/hook失败等测试；不修改生产runtime，不加hasattr测试兼容分支，不让fixture覆盖真实PG测试。

## 最终验收命令（2026-10-23，均exit 0）

| 命令 | 实际结果 |
|---|---|
| `make lint` | All checks passed；159 files already formatted |
| `make test` | 246 passed / 75 skipped，4.17s；未启用的PG/Linux项保留skip，不据此冒充真实验收 |
| `make test-gateway` | 127 passed / 27 skipped，3.50s；真实PG另用下列专项目标实际启用 |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db` | 17 passed，3.13s，零skip |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-customer-locks` | **39 passed，7.59s，零skip**（18参数/配置单测+21实际PG测试） |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-shared-runtime` | **51 passed，9.00s，零skip**；含原真实PG、18连接、7种Linux秘密权限与healthcheck。原有超时关闭测试按预期输出ASGI CancelledError traceback，pytest通过，不是本组件新错误 |
| `git diff --check` | 通过 |

全部PG使用既有显式PGDATA tmpfs夹具、唯一命名临时容器与随机端口，不创建/删除/重建数据卷。运行只用后台Docker CLI；未运行make up/down/test-p1，未动现有持久卷。首次无授权沙箱ruff缓存写入失败，后以正常任务授权升级执行；没有自动审批拒绝。

CI仅在 `.github/workflows/checks.yml` 既有p1-postgres job新增 `make test-customer-locks`，不改变其他门禁；远端未推送，不能声称CI已通过。

## 文件与边界

修改：`services/gateway/app/locks.py`；新增 `tests/gateway/customer_locks/{conftest,test_contract,test_postgres}.py`（直接复用已有真实PG夹具）；Makefile仅末尾追加test-customer-locks；CI仅加目标；上述明确授权的共享runtime测试适配；本进度文件。AGENTS/PRD由协调者汇总，本worker未改。

没有复制审批/OTP/G8、没有新依赖/迁移/池、没有修改runtime生产接线。原W4分支3856a4c保持冻结不动。

## 限制与后续

本组件没有G8、UNKNOWN/reconcile或HTTP提交竞争实现。发送前guard只证明检查时的原连接所有权，不能消灭check-to-send间隙；失锁后同attempt必须由后续W4协议进入UNKNOWN/reconcile，禁止自动重获锁续跑或用elapsed time猜执行失败。调用者必须在同一专用连接按小事务写入，事务外发HTTP，并在新提交前调用guard。连接只按项目固定asyncpg直连PostgreSQL部署验证，未宣称支持其他驱动/事务连接代理。

等待独立只读评审、协调者亲自复跑、真实CI全绿后才可合并。当前仅本地提交，无push/PR/merge、无子agent。实现提交SHA由后续本地记账条目记录。

本地实现SHA：`f52649dcfab95dd37e6bc1469711d68442ceab20`；提交后工作树干净。只读核实原`feat/l2-execution`仍HEAD `3856a4c`，未修改。本条为仅文档记账。

## 同步最新main后冻结（2026-10-23）

按协调者指示合入精确main `a9d72cf`（S2 PR #5），同步提交`8277764`。唯一冲突为Makefile末尾两方新增目标；保留main的test-reserve和本组件test-customer-locks以及所有其他目标，未修改业务代码或原W4分支。

同步后完整六门禁均实际exit0：make lint通过（161 files already formatted）；make test **356 passed/75 skipped**（4.17s，含S2新增110项）；make test-gateway **127 passed/27 skipped**（3.45s）；make test-gateway-db **17 passed零skip**（2.91s）；make test-customer-locks **39 passed零skip**（7.59s）；make test-shared-runtime **51 passed零skip**（8.52s，原有ASGI取消测试预期traceback仍存在，测试通过）。后三者均使用后台Docker CLI及显式tmpfs隔离夹具。

本轮仅同步冲突及结果记账，代码冻结，未push/PR/merge远端；立即结束本轮，留独立review槽给协调者。原W4分支继续保留不动。

## 独立评审修复轮1：R1/R2（2026-10-23）

评审REQUEST_CHANGES两项P2。先读并核对源码，再分别真实红绿；本轮只修改locks.py、专属PG测试及本文，未改runtime/表/迁移/CI/其他组件。

### R1 清理阶段断连及异常优先级

根因：原finally中的except BaseException只discard后原样抛，未采用body路径的断连归一规则，并会以cleanup错误覆盖已经传播的业务异常/CancelledError。

真实红测 `pytest .../test_postgres.py -k cleanup_disconnect_classification -q --tb=short`：**9 failed**。用连接实例事件在rollback、unlock_all、set_config发送前暂停，另一实际PG连接终止原backend，再放行；分别覆盖body正常、已有业务异常、已取消。实际得到asyncpg InternalClientError / SQLAlchemy InternalError，而非预期LockOwnershipLost或原业务/取消。

修复优先级（经协调者确认）：

- 没有body异常时，cleanup DBAPI connection_invalidated或原物理会话已失效，可靠discard后归一为LockOwnershipLost(503)。其他非断连SQL/编程错误保留原类型，连接仍丢弃。
- cleanup自身CancelledError原样传播；discard阶段新CancelledError亦保留取消信号。
- body已传播业务异常或CancelledError时，cleanup断连/SQL/二次discard异常不覆盖原异常；每次仍尝试实际invalidate，finally解绑实例监听并清owner关联。

同一9项真实kill矩阵随后 **9 passed**。另验证unlock/settings实际SELECT 1/0仍保留DBAPIError/22012；rollback/unlock/settings清理中发生新取消保留取消消息；两项二次discard故障测试先完成真实物理invalidate，再有意抛二次错误，原业务对象/取消均保留。二次错误是明确测试故障注入，不称为实际PG自然错误。所有路径均验证连接关闭、owner record无残留及同客户新owner可获锁。

### R2 配置幂等与在途请求边界

根因：每次configure都会重建Semaphore。真实PG下max=1的A仍持锁时重复同参数配置，B不同客户立即进入，突破并发上限；仅统计已yield owner也无法保护等待Semaphore或pool连接的请求。

修复前六项针对性红测 **6 failed**：同参数重配突破上限、活跃owner变更未拒绝、Semaphore等待者捕获的旧对象被替换、pool等待者正常/取消边界、取消Semaphore等待者后的配置边界。

现在同engine且同max直接返回，保留原Semaphore及dispatcher。不同配置在任何已捕获资源的客户锁请求存在时抛RuntimeError，且检查先于修改engine/Semaphore/候选pool dispatcher。请求计数在首次await（Semaphore/pool排队）之前同步增加，覆盖完整清理和关闭，在最外层finally减少；不以_owners数量代替。在同一服务事件循环内使用，不支持跨线程同时重配。

修复后包括原engine隔离回归的定向测试 **7 passed**。保持空闲时可切换engine/max及每pool稳定单dispatcher；活跃时跨engine切换的旧测试已按新明确约束改为拒绝，空闲切换仍实际验证。取消Semaphore/pool等待者后均可空闲重配；前owner刚退出、等待者尚未恢复取得连接的间隙仍拒绝变更。未新增池、线程或依赖。

### 修复轮1完整退出（最终源代码，全部exit0）

| 命令 | 实际结果 |
|---|---|
| make lint | All checks passed；161 files already formatted |
| make test | 356 passed / 97 skipped，4.46s |
| make test-gateway | 127 passed / 49 skipped，3.51s |
| make test-gateway-db | 17 passed零skip，2.67s |
| make test-customer-locks | **61 passed零skip，9.21s**（18参数/配置单测+43真实PG；原39项+本轮22项） |
| make test-shared-runtime | **51 passed零skip，8.80s**；原ASGI取消测试仍有预期CancelledError traceback |
| git diff --check | 通过 |

后三项沿用后台Docker CLI、显式tmpfs和随机临时PG。普通test/gateway的skip较上轮各增加22，原因是新加PG项未在普通命令启用；在专项61项全部实际启用零skip，没有把skip作通过。本轮静态检查曾发现重缩进长行和取消异常未显式chain，已仅按ruff修正字符串换行与raise ... from cleanup_error，再执行上述最终六门禁。

范围保持仅R1/R2和对应回归；原W4分支继续不动。等待原独立review定点复审与协调者亲自复跑，不开PR、不push、不合并远端。修复SHA由后续本地记账记录。

修复轮1实现SHA：`b8dd8edf06196fded205bbe665a7d9ff4d127053`（本地，未push）。提交后工作树干净，本条只记账并冻结等待复审。
