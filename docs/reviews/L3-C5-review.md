# L3-C5 风险问卷与测评 独立评审

- 范围：`git diff origin/main...119e2e7`
- 评审方式：只读；每读完一个文件/跑完一个命令即追加结论
- 文件预算：6 个（risk.py、risk_questionnaire.py、state.py、0006 迁移、reads.py、test_risk_exec.py）

## 结论

**APPROVE**（无 P0/P1；1 条 P2 非阻塞，见下）

## P0

（无）

## P1

（无）

## P2

1. **get_risk_profile 混用两个来源，summary 与事实表可能不同源** — `services/bank-backend/app/reads.py:346-359`
   - `level`（L351）与 `is_valid`（L354-358）取自 `customers` 摘要（`customer.risk_level` / `customer.risk_valid_until` 字符串），而 `assessment_id`（L350）/ `assessed_at`（L352）/ `valid_until`（L353）取自最新事实行 `state.assessments[0]`。
   - 生产提交路径两者同事务写入（`state.py:477-505`），因此一致；但既有种子直接写 `customers.risk_*` 而不建事实行（`evaluation/runner/seed.py:451-453`、`tests/evaluation/corpus/generate.py:507-517`，均为本次 diff 之外的既有代码，已 grep 确认）。此时 get_risk_profile 会返回 `level=3`、`is_valid=True`，同时 `assessment_id=None`、`valid_until=None`——「有效」却没有对应测评事实，违反「风险摘要与真实最新测评同源」。
   - 建议：`level`/`is_valid`/`valid_until` 全部从 `state.assessments[0]`（最新事实行）推导；无事实行时 `level=None`、`is_valid=False`，使「无事实即无效」自洽。不阻塞合并（生产提交路径不受影响）。

## 已核对项

1. **bankcore 评分** `packages/bankcore/src/safebank_bankcore/risk.py`（纯函数，无 IO）：
   - `apply_risk_assessment` 不接收 score/level 参数，answers 仅为 (qid, oid) 二元组，调用方无法注入 score/level；假 question_id 被 `Unknown question_id` 拒绝。
   - `len(answers) != 6` 拒绝缺题/多题，重复 qid 拒绝，无效 option_id 拒绝；rq_v1 恰 6 题 + 无重复 + 全合法 ⇒ 六题各一次。
   - 分档经 `SCORE_RANGES` 查表，无匹配即报错，不静默兜底。
   - 有效期 `_add_calendar_months`：月进位正确，闰日/月末取 `min(day, monthrange)`，与 PRD「闰日取月末」一致；`tests/bankcore/test_risk.py` 显式测 2024-02-29+12m→2025-02-28 及评分边界 6/24。

2. **问卷契约** `packages/contracts/src/safebank_contracts/risk_questionnaire.py`：
   - rq_v1 六题 q01–q06，每题 a/b/c/d，分值 1–4；`SCORE_RANGES` = {1:(6,9), 2:(10,13), 3:(14,17), 4:(18,21), 5:(22,24)} 与 PRD 分档一致；`VALIDITY_MONTHS=12`。
   - `packages/contracts/src/safebank_contracts/tools.py`：W12 的 `Assessment{questionnaire_id, answers}` 无 score/level 字段，`Answer.option_id` 仅 a/b/c/d，6 个不同 question_id 校验——调用方无法自报 score/level。

3. **接线** `services/bank-backend/app/state.py` + `packages/bankcore/src/safebank_bankcore/banking.py`：
   - `apply_risk_assessment`（banking.py）经 `score_risk`（bankcore）自行评分，ValueError → `CONTRACT_INVALID` 拒绝；assessment_id 由 `derive(plan_id, step_id, "asm")` 生成。
   - `persist` 的 `InsertAssessment`（state.py:477-505）在**同一连接/事务**内插入 `risk_assessments` 事实行并 `UPDATE customers` 摘要（risk_level=等级整数、risk_assessed_at、risk_valid_until）；`state.py:299` 对其他客户把 risk_* 全部置空，快照不泄露他客风险摘要。
   - 同 plan 幂等：DB `UniqueConstraint("plan_id")` + `plan_executions` 去重，`test_twenty_retries_single_fact_row` 实测 20 次执行仅 1 行事实 + 1 次执行。

4. **读接口** `services/bank-backend/app/reads.py`：
   - `get_risk_questionnaire`（L332-345）返回静态 rq_v1，选项只含 id/label（**不泄露分值**），`validity_months=12`。
   - `get_risk_profile`（L346-359）与 `get_profile`（L201-217）风险摘要均只读本客户（`state.customers[customer_id]`；assessments 在 `load_bank_state` 中按 `customer_id` 过滤，state.py:376-381）。
   - 唯 P2-1 的混合来源问题（见上）。

5. **迁移 0006** `services/bank-backend/migrations/versions/0006_risk_assessments.py`：
   - `down_revision = "0003_obligations"`（正确续链）；历史迁移 0001/0002/0003 不在本 diff 中，未改动。
   - 表列 + CHECK（score 6-24、level R1-R5、valid_until>assessed_at、questionnaire_id<>''）+ `UniqueConstraint(plan_id)`。
   - 追加触发器 `forbid_mutation()` BEFORE UPDATE OR DELETE（append-only）；权限 `REVOKE ALL … FROM bank_app, bank_ro` 后仅 `GRANT SELECT,INSERT TO bank_app` / `GRANT SELECT TO bank_ro`；`downgrade` 前向不可逆。
   - `db.py` readiness 版本号同步到 `0006_risk_assessments`。

6. **测试真实性与断言**：
   - `tests/obligations_data/test_risk_exec.py`：真实 tmpfs PG（obligations_db）+ 真实 uvicorn HTTP + 真实 `/internal/plans/prepare`、`/internal/plans/execute`；断言具体（分数 16→R3、事实行数=1、摘要值 `risk_valid_until=="2027-10-15"`、operation 事件数、20 次重试幂等、非法答案 FAILED/CONTRACT_INVALID 且零残留、有效期四态含边界「到期日当天仍有效」、纯函数预演与持久化重载一致）。
   - `tests/obligations_data/test_risk_schema.py`：0006 版本号 + tableowner=postgres（运行时角色非 owner）、UPDATE/DELETE 被触发器拒（sqlstate 23514）、score 6/24 与 level R6 边界拒、plan_id 唯一、FK 拒不存在客户、运行时不可 DDL/TRUNCATE、0003 数据升级 0006（跑两次）后保留且新表可写。
   - 无放宽断言；版本号断言的更新（test_database.py / test_0002_upgrade.py / test_obligations_data.py）仅为 0003→0006 的机械替换。
   - 新增 `tests/bankcore/test_reads_profile.py`：get_profile 风险摘要的 `date` 导入回归（内存构造，不依赖 PG）。
