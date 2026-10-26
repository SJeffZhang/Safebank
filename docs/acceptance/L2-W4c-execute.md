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

## 协调者亲自复跑（合入 main `51c3b90` 后的最终树，全部 exit 0）

| 命令 | 结果 |
|---|---|
| `make lint` | 364 files |
| `make test` | 2727 passed / 743 skipped |
| `make test-gateway` | 2018 passed / 650 skipped |
| `make test-gateway-db` | 17 passed |
| `make test-wave1-state` | 231 passed |
| `make test-w1` | 1688 passed |
| `make test-w2` / `test-w3a` / `test-w3b` | 246 / 45 / 33 passed |
| `make test-w4b1` / `test-w4b2b` / `test-w4b2c` | 57 / 58 / 56 passed |
| `make test-w4c` | **38 passed** |
| `make test-shared-runtime` | 79 passed |
| `make test-obligations` | 30 passed（1 条既有 FK 环 SAWarning） |
| `make test-agent-db` | 34 passed |
| `pytest tests/evaluation/corpus -q` | 25 passed |
| `make contracts-check` | PASS |
| `actionlint` | 通过（CI 已加 `make test-w4c`） |

## 独立评审

- R-W4c-r1（DeepSeek，只读）：REQUEST_CHANGES，无 P0；8 项核对全部通过；P1-1 恢复路径吞掉 STATE_CHANGED、P1-2 fence 写死 attempt、P2-1 PAUSED+非可执行决策误入恢复、P2-2 孤儿 PAUSED 无归档（归 W5）、P2-3 对账终态未写本次 request_id。
- 修复轮 1：P1-1/P1-2/P2-1/P2-3 全部修复（`0478105`、`dbb5426`），各配红测与变异核验。
- R-W4c-r2 定点复审：**APPROVE**，4 条全部 ADDRESSED，无新问题（报告 `docs/reviews/W4c-execute-review.md` 末节）。

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
| 去掉 P2-1 的非可执行决策拦截 | 1 failed |
| 去掉 P2-3 的对账终态去重写入 | 1 failed |
| dedup 键改回字面模板 | 2 failed |

## 已知问题与未覆盖

- 进程崩溃后重建 gateway 的真实恢复测试（裁定 C5 的第二项）未做；当前恢复只经 G5/G8 内联触发，启动扫描与后台恢复归 W5。
- 长期栈已在合并后迁移到 `0003_plan_draft`，六服务 healthy、网络/HTTP集成通过（见下节）。完整 live P2 三层黄级转账仍未验收。
- W4b2b 已知 P2-4（supersede 与 G6/G7 锁序）按 W3b 统一锁序处理，本组件未新增反向锁。
- app_form（F 发起）计划的创建尚未实现，F 执行路径只验证了「不能执行/读取 agent 计划」。


## 合并后交接核对（2026-10-26，Codex）

- [PR #31](https://github.com/SJeffZhang/Safebank/pull/31) 于北京时间 21:23:28 合并，merge `00aca15b7928ccfa222b85a6757d941a7b68425e`；root 与 main-baseline 均在该提交，接手时工作区干净。
- 精确候选 `79229ae2864066623ca7046458a642880ffdd433` 的 [push CI](https://github.com/SJeffZhang/Safebank/actions/runs/40328430495) 与 [pull_request CI](https://github.com/SJeffZhang/Safebank/actions/runs/40328499146) 均 completed/success，各自 unit、p1-postgres 全成功；PR CI 的 test-w4c 及其他门禁步骤实际执行成功。
- **前任协调者实测，本次读取原始日志核对**：`../Fintech2026-wt/.ds-logs/main-00aca15-regression/` 含合并后组合回归、`make-up.log` 与 `test-integration.log`。全量 2727 passed/743 skipped、Gateway 2018/650 skipped、W4c 38；真实网络/凭证/DB角色、HTTP→PG/prepare/transfer/outbox及实际 L3 重启幂等恢复均 PASS。各套件重叠，不能相加；这些不是本次重跑所得。
- **Codex 本次亲跑**：主 checkout 的 `perl -e 'alarm 300; exec @ARGV' make test-w4c` → **38 passed，26.53s，exit 0**。首次收集因 root 旧虚拟环境缺 PyJWT 失败；离线按锁文件同步四个缺失依赖后重跑通过。第一次直接 uv sync 触及默认受限缓存而退出，随后指定 Makefile 同款 `UV_CACHE_DIR=/private/tmp/sbg-uv-cache` 离线同步成功；未改锁文件。
- **Codex 只读部署复核**：`postgres-bank` 中 `gateway.alembic_version` 为 `0003_plan_draft`；六服务 running/healthy；`safebank_pg-bank`、`safebank_pg-agent` 的 CreatedAt 均仍为 `2026-10-23T11:29:55Z`。未重建/删除卷、未重新启动长期栈。最初误向 postgres-agent 的不存在 gateway 库查询，后按 `scripts/gateway_database.py` 确认位置并查询正确库，无数据写入。
- **进度页**：前任已推送 `7bd1ccc`（W4c #31/main 回归）及 `77064c4`（E0b 暂停）；本次 curl 读取[线上 JSON](https://sjeffzhang.github.io/safebank-progress/data/progress.json)确认 W4c=done、PR=31、回归=00aca15、E0b 仍暂停，因此不重复发布。
- 更新 AGENTS §3/§5/文件地图与本验收文档，消除仍称 W4c 待开 PR、L1 C1c2/PG/输出未完成的旧状态。未变更业务代码或接口，无设计变更。
- E0b 收尾/live、try-agent、前端/BFF与定时任务继续暂停；完整 P2、W5/W6不在本次收尾中启动。
