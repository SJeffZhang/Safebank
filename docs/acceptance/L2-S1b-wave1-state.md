# L2 S1b：0002 与受控执行状态验收

2026-10-23，本地分支 feat/l2-wave1-state，基线97d5a33（main a9d72cf + 已评审协调文档）。范围：gateway 四 nullable 列、内部 FENCING/PAUSED、最新 readiness、专用 CAS 与防绕过；不改 PlanStatus、历史0001/schema_v1、L3/银行契约或W1–W4业务。

## 实际退出

全部命令前加 `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH`。

| 命令 | 结果 |
|---|---|
| make lint | exit0，ruff通过，166文件格式通过 |
| make test | exit0，358 passed / 139 skipped，4.29s |
| make test-gateway | exit0，129 passed / 91 skipped，3.48s |
| make test-gateway-db | exit0，17 passed / 零skip，2.12s |
| make test-wave1-state | exit0，105 passed / 零skip，7.90s |
| make contracts-check | exit0，冻结v1 PASS |

每次DB测试使用独立随机容器、显式PGDATA与同路径tmpfs；不挂载数据卷、不访问业务数据库。空库upgrade重复、0001带session/plan/evaluation/approval/job/OTP/claim/audit升级数据完整保留，四列类型/nullable、旧ready拒绝/新ready通过、合法新旧job状态与非法状态CHECK、迁移角色owner、运行角色DDL/TRUNCATE/审计更新删除拒绝均通过。最新metadata有真实PG Alembic drift验证；历史DDL精确比较仅回退明确授权的0002增量，另有冻结JSON SHA保护。

## CAS条件与调用者责任

接口与红绿细节见[进度报告](../progress/S1b-wave1-state.md)。ExecutionIdentity绑定customer/plan/origin/attempt/latest revision；所有操作要求同一有效客户锁连接上的调用者短事务，无内部commit/新连接/HTTP。plan→job→claims锁顺序，条件失败409 STATE_CHANGED；非法程序输入ValueError。

进入FENCING、ACK落PAUSED、W3 guard→append新evaluation→publish、确认后保存n+1/evidence/deadline、UNKNOWN与终态归档分职责。旧attempt/revision拒绝；并发fence/resume各仅一次成功；cap精确不变且新deadline有效不越cap，cap过期不能发布新的可执行授权；旧evaluation不能伪装新复演。旧缺证据/cap的任务不可恢复提交，仍可UNKNOWN对账。等价view保留原approval/evaluation绑定，新PENDING须确认后才能恢复。

普通transition不能造屏障或终态归档；有关联未终结job的plan不能提前归档/取消标签化后遗忘；claim保留至专用真实L3终态入口。取消/拒绝/过期等数据库状态组合均先UNKNOWN；COMMITTED必须匹配plan且EXECUTED结果，CANCELLED允许None并仅归档FAILED，原话CANCELLED保持不变。

上述只证明数据库条件。W4必须证明真实STATE_CHANGED、同attempt PREPARED fence ACK、COMMITTED/CANCELLED响应真实性、实时授权/撤销/epoch/MAC/快照和真实客户锁持有。真实L3 HTTP双向竞争、丢包、进程崩溃、断锁及W1/W3/W4接线未在S1b运行，不宣称协议整体已验收。

## 失败与修复

迁移红3→绿20；fence红12→绿12；publish/resume红21→绿33；未知/终态与普通绕过红41（另3既有拒绝/回滚通过）→专项+PK绿84。cap到期与必须新evaluation各红1后修复。旧库升级测试子进程PYTHONPATH缺失修正后通过。完整单元曾1 failed / 357 passed / 138 skipped，根因为旧DDL测试禁止所有增量；协调者批准仅测试适配，原DDL冻结未改。clone约束排序第一次定点失败后采用deepcopy保留精确DDL，1 passed。详细中间命令/结果见进度报告。

两个最小测试例外由协调者明确批准：test_state_keys中UNKNOWN claim消费改409、独立无job claim保留PK测试；test_ddl_drift保留历史精确漂移保护并白名单回退四列/新CHECK。没有删除或skip失败断言。

实现 checkpoint `4b364968d278b5a74e972b9133a0307fe67031ba`，随后仅同步SHA文档；尚未push/PR/merge；CI已增加专项但远端未运行。协调者复跑与独立只读评审仍待完成。

## 协调者与独立评审确认

协调者在8b151bc完整六退出全exit0：lint166、全量358/139skip（4.14s）、gateway129/91skip（3.46s）、PG17（2.09s）、专项105零skip（7.74s）、contracts PASS。review_s1b_state独立规范PASS/质量APPROVE，无可复现P0/P1/P2；亲自定向17 passed零skip（10.13s）。真实HTTP协议与W1/W3/W4接线仍未验收。随后仅合并协调文档与本确认，业务代码未变；准备推送组件PR，远端CI尚待运行。
