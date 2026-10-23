# W4a 客户锁先决组件

状态：待派原W4 agent；不由协调者编写业务。为W1真实运行验收，先把W4已经实现的客户锁分离为可独立审核/合并的小组件；W4审批/执行仍沿原任务书推进。分支`feat/l2-customer-locks`，worktree由协调者从最新main建立。

输入：集中runtime传入AsyncEngine、max_executions；客户ID集合。输出：按固定签名`customer_lock(customer_ids)`取得唯一专用AsyncConnection，排序持有session级锁并允许短事务/事务外HTTP，退出释放；依赖故障不得继续当作持锁。

先读AGENTS、PRD7.8末段、7.10、W4原任务书、`L2-execution-state-review.md`锁失效部分。可参考原W4检查点3856a4c的locks.py与锁测试，但仅提取本组件，不搬审批或OTP、G8替身。

允许修改：`services/gateway/app/locks.py`、专属`tests/gateway/customer_locks/`、Makefile末尾test-customer-locks、CI对应真实PG目标、`docs/progress/W4a-customer-locks.md`。runtime已有configure hook不改；其他冻结文件不改。无新池/新依赖/迁移、不动前端/L1。

必须落实：

- `configure(db:AsyncEngine,max_executions:int)`只绑定同engine与进程Semaphore，max_executions正整数。
- PRD明确SQL键为`pg_advisory_lock(hashtextextended('cus:' || id, 0))`，5秒lock_timeout，客户ID去重排序。原W4检查点用了另一hashtext双参数namespace，需修成PRD并真实验证同键互斥。
- 专用连接涵盖短事务和HTTP间隙，调用者可多次begin；正常/异常/取消退出均释放已获锁，恢复连接设置，连接有疑问即invalidate不返池复用。
- **永久锁所有权失效**：锁连接断开后，本次owner不得经rollback/自动重连继续写入或提交。保持既有返回/函数签名，可增加用于G8发送前核验的明确helper；先报告签名给协调者。必须验证原backend身份/失效，不以重新获得另一连接冒充原锁。异常归一化规则及调用者责任写清。
- 不在本组件写G8恢复逻辑，失锁后的UNKNOWN/reconcile由后续W4执行组件处理；这里证明owner不可继续，不能宣称HTTP竞争已验收。

先红后绿验收真实PG：未配置503、非法ID/并发值、不同输入顺序同锁序、同客户超时409、多客户反序无死锁、不同客户可并行、max_executions限流、部分获得后异常/任务取消释放、退出不污染池、持锁多短事务、后台终止pg_backend后owner永久失效（含rollback后再操作与发送前guard）、原owner失效后新请求可获锁。

`make lint && make test && make test-gateway && make test-gateway-db && make test-customer-locks`，专项必须真实PG零skip。CI加入专项目标。独立review与协调者亲自复跑后单独PR，全部CI绿再merge。W1随后同步并移除fake lock做真实登录/撤销/竞争测试；W4原分支同步时由原owner解决重复实现，以已评审版本为准。

只本地提交，不push/PR/merge；不运行make up/down/test-p1；PGDATA显式tmpfs，不删除/重建任何卷；不得自开agent。每小组件结果、未覆盖项和SHA准确记报告。
