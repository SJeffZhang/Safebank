# S1 独立只读评审

2026-10-23；评审agent `/root/review_s1`；范围8555bf0..060e0f45a2a754d7b1af97c3050f1909b906b742。协调者记录其原始结论及后续核实。

规格：FAIL。质量：REQUEST_CHANGES。P0=0、P1=1、P2=1；不得开PR。

## R1 P1：双listener异常退出没有保证Uvicorn shutdown

main.py:165及167–175：任一监听器异常或外层serve取消，gather(done)抛出后finally直接cancel其他serve任务，然后释放DB/L3。安装版Uvicorn Server._serve依次startup→main_loop→shutdown，无finally；cancel主循环不等于关闭listener、连接、请求任务和lifespan。

评审无网络探针保留真实serve/_serve/main_loop，替换startup/shutdown为可观察状态，第二服务在两者启动后抛出异常，输出：

```
RESOURCE_DISPOSAL listener still serving: [True, True]
RuntimeError synthetic peer failure after both startup
```

现有test_runtime.py:229的替身serve自带finally清理，掩盖了真实Uvicorn行为。真实localhost探针因沙箱EPERM未完成，不得声称真实socket已验证。

修复：统一正常退出、启动失败、运行失败、外层取消的关闭路径；先关闭listener、处理并等待在途请求/lifespan，再释放共享资源。补真实Uvicorn生命周期回归，不能只检查主任务结束。

## R2 P2：Compose healthcheck仍以root运行

compose gateway user:0:0，healthcheck直接python urllib；PID1 setuid不改变后续healthcheck的容器默认user。健康检查也带bootstrap capabilities，违反仅短暂引导阶段持特权边界。

修复：健康检查先清组、永久setgid/setuid10001，再HTTP；独立Linux验收实际healthcheck命令的UID/GID、CapEff/CapPrm=0、NoNewPrivs=1。

## 其他评审结论

- 全局F/A客户撤销接线缺依赖拒绝、GatewayError传播、SQLAlchemyError脱敏503，未发现放行旁路。
- Clarification仅OPEN→RESOLVED/CANCELLED，非法边和竞争语义符合范围。
- AsyncExitStack部分初始化失败清理合理；主要缺口在server退出边界。
- 私钥普通文件0600、不可跟随最终symlink、有界读取、私有tmpfs与永久降权主路径合理，网络隔离保留。
- W1/W4尚未合入的阶段限制记录诚实，不能把替身当真实登录联调。
- 本次评审未改文件或操作Docker；diff --check通过。五条退出命令由协调者亲自复跑。

## 协调者追加的确定问题与接口收敛

- R3：PRD7.8要求连接池≥18，实际只构造不连接DB的probe输出pool_size=5,max_overflow=10，即上限15。S1应显式配置至少18，并为max_executions提供足够余量；测试真实配置参数而非仅mock存在。
- R4：state.transition硬编码table.c.id，execution_jobs的PK为plan_id，authorization_claims的PK为scope_id。保持签名，统一按唯一单列PK寻址，复合PK显式拒绝；authorization_claims注册CLAIMED→CONSUMED、终态无出边，不能绕入口直接UPDATE。
- W4锁初始化接口已确认locks.configure(db:AsyncEngine,max_executions:int)->None，只复用现有engine，不新建池。S1可在存在该hook时集中调用；尚无hook时不伪造锁。
- W4确认assistant JWT也由W1 _issue持久化auth_sessions；无需扩Caller。截止取持久jti期限，不能只用原话期限。

## 修复轮1

待原S1实现agent处理R1–R4及锁初始化接线，覆盖测试后由原评审定点复审；新增修改也纳入该轮。尚未通过。
