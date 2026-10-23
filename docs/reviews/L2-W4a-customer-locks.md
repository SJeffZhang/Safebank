# W4a 客户锁独立评审

2026-10-23，review_w4a。HEAD d582752，base a9d72cf。结论REQUEST_CHANGES，2项P2，无已发现P0/P1。

1. R1（locks.py:265）：finally清理阶段rollback/unlock/恢复设置断连仅discard后原样抛，绕过LockOwnershipLost归一。独立真实PG在unlock_all前暂停并用另一连接pg_terminate_backend原PID，得到InternalError(connection_invalidated=True)。连接关闭且后继可获锁，无漏锁，但恢复层可能漏分类。须归一连接失效，保留取消及已有业务错误规则。
2. R2（locks.py:33）：同engine同max重复configure重置信号量。max=1持A后重配，B不同客户立即入锁突破上限。当前生产只在启动配置，故触发前提是执行期重配，不是正常生产资金绕过。应同engine/同参数幂等，活跃/等待请求期间拒绝engine或参数变更。

评审亲自39 passed/零skip（7.44s），并验证清理取消传播CancelledError、关闭原连接且后继可获锁；首次沙箱端口权限失败，正常提升后成功。仅现有tmpfs PG，无改文件。核查永久失效/部分获取/提前close/pool/监听器及3项fake Engine测试适配。未验收G8/HTTP竞态/UNKNOWN/远端CI。

协调者此前已亲自六退出全0：lint161、all356/75skip、gateway127/27skip、PG17、locks39、shared-runtime51（含Linux），不能替代上述缺陷回归。原owner修复轮1中，未开PR。

## 修复定点复核

原模型额度中断未出复审结论。新独立review_w4a_fixes审d582752..e4023ed，spec+quality APPROVE，无新增P0/P1/P2，R1/R2及协调者额外None-checkin问题均ADDRESSED。实际26 passed/19 deselected/0 skip（3.40s）；SQLAlchemy锁定源码机制已核对，自然ID复用未由评审重现。同步main94e40fe仅CI/Makefile冲突保留全部目标，业务/测试无diff，协调者同步版六退出全绿，详见验收。
