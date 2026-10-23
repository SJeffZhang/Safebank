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
