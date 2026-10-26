# L2 W4c 验收：G8 执行、G5 状态、STATE_CHANGED 屏障与 UNKNOWN 恢复

- 分支：`codex/w4c-execute`（基线 main `9b8c65a`）
- 任务书：`docs/plans/briefs/W4c-execute.md`（含协调者裁定 C1–C8）；逐任务记录：`docs/progress/W4c-execute.md`
- 日期：2026-10-26

## 交付范围

| 接口/机制 | 行为 | 证据（全部真实 tmpfs PG + 真实 bank-backend，无 FakeL3） |
|---|---|---|
| 复演 `replay` / `replay_with_context` | 新建 EvalContext、取新快照、以 EXECUTE_REPLAY 重跑全部阶段 | `test_replay.py` |
| 迁移 `0003_plan_draft` | `plans.plan_draft JSONB NULL`；G4 同事务写入；旧行 NULL 的计划 G8 fail-closed 503 | `test_plan_draft.py`（含 0002→0003 升级保留旧行） |
| G8 ALLOW | 复演 plan_hash 与 G4 同口径比对；写事务内 plan→EXECUTING、登记 claims、建 job、审计；事务外 prepare/execute；终态归档消费 claims/原话 | `test_g8_allow.py`（真实冻结卡，只执行一次） |
| G8 CONFIRMED | 加锁取已确认审批，过期/归属校验，复演面板 view_hash 逐字节等于审批；审批 CONFIRMED→USED | `test_g8_confirmed.py`、`test_approval_fixture.py` |
| 回包不明 | job 已持久 EXECUTING 后的不明/业务错误/非 EXECUTED 一律 UNKNOWN（claims 保留）+ 202 | `test_g8_unknown.py` |
| ADR-008 屏障 | 仅 prepare 成功且 execute 明确 STATE_CHANGED：FENCING → 同 attempt fence prepare（deadline=FENCE_AT）→ PAUSED → 新快照复演 → 发布新决策 → 409 `details.decision` | `test_g8_fence.py` |
| 屏障后恢复 | 新 request_id 再调 G8：复演等价时经 `resume_paused_execution` 以 attempt 2 执行，不重复 claims/job | `test_g8_resume.py`（含「读取记录过期 → REPLAN → 拒绝恢复」安全用例） |
| UNKNOWN 内联对账 | 只调 L3 query/reconcile，绝不重提 prepare/execute | `test_g8_reconcile.py`、`test_g5_and_replay.py::test_g5_reconciles_unknown` |
| 并发/迟到/撤销/origin | 并发只执行一次；执行中撤销会话则中止；终态迟到返回 replayed；origin 先于一切检查 | `test_g8_races.py` |
| G5 | A 限本 sid、F 限本客户；返回状态、最新决策、执行结果 | `test_g5_and_replay.py` |
| request_id 重放 | 键为具体路径 `POST /plans/<plan_id>/execute`；终态原样重放且永不覆盖；旧 request_id 不得触发新 attempt | `test_g5_and_replay.py` |

## 协调者亲自复跑（HEAD `95225bc`，全部 exit 0）

| 命令 | 结果 |
|---|---|
| `make lint` | 354 files |
| `make test` | 2702 passed / 740 skipped |
| `make test-gateway` | 2018 passed / 647 skipped |
| `make test-gateway-db` | 17 passed |
| `make test-wave1-state` | 231 passed |
| `make test-w1` | 1688 passed |
| `make test-w3b` | 33 passed |
| `make test-w4b1` / `test-w4b2b` / `test-w4b2c` | 57 / 58 / 56 passed |
| `make test-w4c` | **35 passed** |
| `make test-shared-runtime` | 79 passed |
| `make contracts-check` | PASS |
| `actionlint` | 通过（CI 已加 `make test-w4c`） |

## 变异核验（协调者，均已还原）

| 故意改坏 | 结果 |
|---|---|
| 不插入 authorization_claims | test_g8_allow 3 failed |
| 去掉 EXECUTED 幂等返回 | 1 failed |
| UNKNOWN 分支改回 503 | test_g8_unknown 1 failed |
| 去掉 CONFIRMED view_hash 比对 | 1 failed |
| 审批不迁到 USED | 1 failed |
| fence prepare 换成伪造的 PREPARED 回包 | test_g8_fence 1 failed |
| 恢复后仍用 attempt 1 | test_g8_resume 2 failed |
| 用伪造 CANCELLED 代替真实 reconcile | test_g8_reconcile 1 failed |
| 去掉写事务内的撤销复核 | 撤销竞态用例 1 failed |
| origin 检查不前置 | F 读取已执行计划用例 1 failed |
| 去掉旧 request_id 恢复守卫 | 1 failed |
| dedup 键改回字面模板 | 2 failed |

## 已知问题与未覆盖

- 进程崩溃后重建 gateway 的真实恢复测试（裁定 C5 的第二项）未做；当前恢复只经 G5/G8 内联触发，启动扫描与后台恢复归 W5。
- 长期栈 gateway 库仍为 0002，合入后全栈阶段需协调者 `make up` 迁移到 0003 并做 P2 真实三层验收。
- W4b2b 已知 P2-4（supersede 与 G6/G7 锁序）按 W3b 统一锁序处理，本组件未新增反向锁。
- app_form（F 发起）计划的创建尚未实现，F 执行路径只验证了「不能执行/读取 agent 计划」。
