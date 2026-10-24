# W4b1 PlanView / Approval Binding 独立评审

- 候选：`baa1efccaf929c1fe849440f8c8ef3e78c1b36c6`（相对 `eb65c02`）
- 结论：**REQUEST_CHANGES**（P0 无；P1 三条）
- 验证：静读 `approval/planview.py`、`approval/service.py`、`state.py`、`tables.py`、`schemas/*`、`policy/registry.py`、W3a 输入槽；亲跑非 DB 测试 `test_planview.py` + `test_binding.py` = 27 passed。DB 专项由协调者亲跑，我未 docker。
- 冻结区核对：diff 仅 `approval/**`、`tests/gateway/w4b1/**`、`docs/progress`、Makefile 末尾 `test-w4b1`、checks.yml p1-postgres 追加一行、`test_probes.py` 旧 `NotImplementedError` 断言最小改为缺配置 `GatewayError(503)`——与协调者批准一致，无偷改 `state/tables/migrations/runtime/config/contracts`。

## P1

### P1-1 create_for_evaluation 不校验 `evaluation.plan_id == plan_id`（跨计划/跨客户评估绑定）
- 证据：`services/gateway/app/approval/service.py:215-226` 只比对 view 与 plan/evaluation 的身份字段（plan_id/session_id/origin/risk、view.evaluation_id==evaluation["id"]），**没有** `evaluation["plan_id"] == plan_id`；`tables.py:202` 的 `approvals.evaluation_id` 外键只引用 `evaluations.id`（无 (plan_id,id) 复合约束）；`service.py:312-319` 更新 view_json 的 WHERE 含 `evaluations.plan_id == plan_id`，错配时静默 0 行。
- 失败场景：调用方传入 plan=A、evaluation=E（E.plan_id=B）且自行重算 view.view_hash（`content_hash` 只证明 view 自洽），全部身份检查通过，插入一条 `(plan_id=A, evaluation_id=E)` 的审批行；同时 E 的 view_json 不更新（0 行）。执行期 `state._approval`(state.py:462-479) 会因 `approved.plan_id != plan["id"]` 拒绝，但库契约应在创建期失败，不能靠下游兜底。
- 修复：在 215-226 增加 `evaluation["plan_id"] != plan_id` 即 `ValueError`（并可顺带比对 `evaluation["risk"]` 与 `decision["decision"]` 的一致性、`evaluation["findings"]/["preflight"]` 与 view 内容，防止持久化 view_json 与评估行偏离）。

### P1-2 同一 evaluation 并发两个初始创建退化为唯一索引 500，而非幂等同 ID
- 证据：`service.py:232-245` 的 `existing` `SELECT … FOR UPDATE` 在 `service.py:249-252` 的 session 行锁**之前**执行；READ COMMITTED 下对空结果 `FOR UPDATE` 不锁任何行，两个并发事务都读到 None，随后在 session 行锁上串行化，第二个 INSERT 命中 `tables.py:401-406` 的部分唯一索引 `one_usable_approval_per_plan`(plan_id, PENDING) → `IntegrityError` 500。测试仅顺序幂等（`tests/gateway/w4b1/db/test_creation.py:328-349`），无并发用例。
- 失败场景：两个并发的 G4 调用对同一新 evaluation 调 `create_for_evaluation`，一个成功、一个 500，违反 docstring「Same evaluation never creates a second approval」的幂等契约。
- 修复建议：把 `existing` 查询移到 session 行锁之后（锁 session 后再查/再插），或对同 evaluation 的并发用现有唯一索引兜底时捕获冲突并在同一事务内重读返回既有 ID；需真实 PG 并发测试。**待协调者亲跑反例**：两个 `asyncio` 独立事务并发调用同一 evaluation，断言第二个返回相同 `approval_id` 而非 IntegrityError。

### P1-3 _supersede_session 会 SUPERSEDE 同会话其它计划已 CONFIRMED 且背后有 live job/claim 的审批，绕过 ADR-008 屏障
- 证据：`service.py:156-189` 按 `sid` 无条件把所有 `PENDING/CONFIRMED` 审批转 SUPERSEDED（仅排除 `except_evaluation_id`），不检查这些审批对应 plan 的 `execution_jobs`/claims；`service.py:262-268` 的 live-job 检查只查**新 plan 自己**的 `execution_jobs.plan_id == plan_id`，不看同会话其它 plan。`state.transition` 对 approvals 表走 `_protect_transition`(state.py:642-668) 的 `else: return` 分支——approvals 无 plan 锁、无 live-job 检查（仅 execution_jobs/claims/utterances/plans 有保护）。
- 失败场景：同会话 plan A 已 CONFIRMED 且执行中（`execution_jobs` EXECUTING/UNKNOWN，审批仍 CONFIRMED，直到 W4c 归档才 USED）；用户同会话发起新 plan B 需审批，`create_for_evaluation(B)` 把 A 的 CONFIRMED 审批 SUPERSEDED。A 后续 `resume_paused_execution`→`_approval`(state.py:429-484) 要求 status==CONFIRMED 会 409，合法执行无法恢复；违反「已有 EXECUTING/UNKNOWN 及 CLAIMED 不能被重批/释放，按 ADR-008 拒绝或保留」。
- 修复：`_supersede_session` 对每条待 SUPERSEDED 的审批，先按其 `plan_id` 查 `execution_jobs.status`，若为 LIVE_JOB_STATUSES 则跳过并保留（或整体 409），不得 supersede 有 live job/未消费 claim 的 CONFIRMED 审批。

## P2

- **P2-1 幂等快速返回绕过撤销/live-job，且 existing 无状态过滤**：`service.py:246-248` 命中 existing 即 `return existing["id"]`，跳过 `service.py:254-257`(session revoked) 与 `262-268`(live job) 检查；`232-245` 查询不带 status 过滤，可能返回 SUPERSEDED/USED 的旧审批 ID 当有效。建议：existing 命中后仍校验其 status 属于 PENDING/CONFIRMED，并复检 session 未撤销。
- **P2-2 versions 校验接受 bool 与负数**：`service.py:106-109` 用 `isinstance(value, int)`（`True` 通过）且无 `>=0` 检查，与 `clock_version` 的 `type() is not int` + `<0`（104-105）不一致；HMAC 会把 `true`/`-1` 与真实 `1` 算成不同 MAC。建议 `type(value) is int and value >= 0`。
- **P2-3 cancel_*/submit_risk_assessment 缺失实体时显示裸 ID，而非 fail-closed**：`planview.py:137-150`(cancel_scheduled 找不到 instruction 只显示 instruction_id)、`209-220`(cancel_subscription 同)、`221-225`(submit_risk_assessment 直接显示 questionnaire_id)，与 transfer/card/fund/split 的 `raise ValueError` 不一致，违反「不能仅显示裸 ID」；且 `test_planview.py` 未覆盖这三个工具。建议缺失时 fail-closed 或补显示名。
- **P2-4 未校验 plan 状态与 evaluation 最新 revision**：`service.py` 全程不读 `plan["status"]`，也不校验 `evaluation["revision"]` 为该 plan 最新 revision（仅靠下游 `_locked_execution` 兜底）。docstring 声称「caller owns plan status transition」，故 plan 状态可接受委托，但最新 revision 建议在库内校验，避免陈旧评估造出无效审批。

## 未确认（未实测，不列为发现）
- `redeem_product` 的 `args["units"]`（Decimal 或字符串）写入 `PlanViewField.value: str` 的 Pydantic 强制转换行为未实测（test_planview 只覆盖 subscribe）。
- `_approval_deadline`(planview.py:346-357) 对 `auth.utterances` 缺某 utterance_id 时静默跳过其 expiry 上限——依赖 G4 路由先行校验 `utts` 子集，未见本库兜底。

## 已核通过项
- content_hash 覆盖可见语义、envelope 排除且 HMAC 独立绑定、`test_envelope_changes_do_not_change_hash`/`test_each_visible_semantic_change_changes_hash` 覆盖到位；大整数无 float（`canonical` 用 `json.dumps` 非 float，`_yuan` 纯整数）。
- `configure` 显式 key/profile/aware wall，缺配置 503、短 key/无效 profile/naive wall 拒绝，未用 policy_version 替 profile、无默认/随机 key；`verify_binding` 用 `hmac.compare_digest` 重算不信任传入 MAC；时区归一化 MAC 测试通过。
- DENY/REPLAN/ALLOW 分支、撤销会话、customer 不匹配、live job 阻塞新审批、缺 payee/card/disclosure fail-closed、原话 expiry 上限均有测试。

---

## 修复轮 1 复核（候选 `dd43c1baf7d09230c9df0e8baff6d7dd733841be`，相对 `baa1efc`）

- 结论：**APPROVE**（P0 无；P1 无）。三 P1、四 P2、两未确认全部逐条修复，无新 P0/P1。
- 亲跑：`.venv/bin/pytest tests/gateway/w4b1/test_planview.py tests/gateway/w4b1/test_binding.py -q` = **34 passed**；`tests/gateway/test_probes.py -k create_for_evaluation` = 1 passed。DB 52 专项与全量由协调者亲跑，我未 docker，不据自报判定。

### 逐条核证
- **P1-1 关闭**：`service.py:286-325` 改为先 `with_for_update` 锁 plan→evaluation→session→job 再读权威行；`db_eval["plan_id"] != plan_id`→`ValueError`（312）、`risk/decision/findings/preflight` 与 view 逐项比对（313-320）、`revision == max(revision)` 否则 409（321-325）；view_json 更新 `rowcount != 1` 抛错（410-419）。红绿 `test_evaluation_of_another_plan_rejected_atomically` 断言错配 0 新行且 view_json 未写。
- **P1-2 关闭**：`existing` 复查移到 plan/session 串行锁之后（`service.py:350-365`），锁序 plan→evaluation→session→job→existing。`test_concurrent_same_evaluation_returns_same_id` 是真双连接并发（T1 持锁未提交、T2 阻塞于 plan `FOR UPDATE`，`assert not task2.done()` 证阻塞，commit 后 `id1 == id2` 且单行）——非 fixture 放松/模拟。
- **P1-3 关闭**：`_supersede_session`(service.py:198-240) 先把所有候选审批按 `plan_id` 锁 `execution_jobs` 检查 live job，任一命中即 409（第一遍循环），**第二遍才 transition**，无部分 SUPERSEDE；job→approval 锁序与执行路径一致。`test_live_job_of_other_plan_blocks_supersede` 断言旧 plan CONFIRMED+EXECUTING+CLAIMED 原样、新 plan 零新行。
- **P2-1 关闭**：`existing` 命中后经 `_existing_approval_id`(183-195) 校验 status∈{PENDING,CONFIRMED}、`expires_at_wall<=now`→APPROVAL_EXPIRED、`view_hash` 一致；SUPERSEDED/USED/EXPIRED/REJECTED/LOCKED 各以对应 Code 409 拒；且 existing 复查已移到 session 撤销(334)/live-job(345) 检查之后，不再绕过。
- **P2-2 关闭**：`service.py:123-127` versions 改 `not isinstance(key,str) or not key or type(value) is not int or value < 0`，bool/负值/空键全拒；`test_binding_rejects_bool_and_negative_versions`。
- **P2-3 关闭**：`planview.py` cancel_scheduled/cancel_subscription 缺实体 `ValueError`，`submit_risk_assessment` 因 BankState 无问卷元数据 fail-closed；`test_cancel_*_missing_fails_closed`/`test_submit_risk_assessment_fails_closed`。
- **P2-4 关闭**：plan status 限 `_ELIGIBLE_PLAN_STATUSES`{EVALUATED,PENDING_APPROVAL,CONFIRMED}（`service.py:43-47,299`）+ 最新 revision 409；`test_terminal_plan_status_rejected`/`test_stale_evaluation_revision_rejected`。
- **未确认项关闭**：`redeem_product` `str(units)` 归一 Decimal/"1.0000"→"1.0000"（`planview.py:239-241`，`test_redeem_units_rendered_as_plain_decimal_string`）；`_approval_deadline` 缺 utterance_id `ValueError`（`planview.py:356-358`，`test_missing_authorization_utterance_fails_closed`）；`build_view` 经 `_wall_now`、`create_for_evaluation` 经 `_wall` 动态 naive wall 拒（`test_naive_wall_clock_fails_closed`）。
- **test_probes 仅 import 排序**：diff 仅 `errors` 提到 `evaluation` 之前（I001），无业务断言改动。
- **冻结区**：`git diff --stat baa1efc dd43c1b` 仅 approval/planview.py+service.py、tests/gateway/w4b1/**、test_probes.py、docs/progress、Makefile 末尾、checks.yml 一行；无 state/tables/migrations/runtime/config/contracts/bankcore 改动。

### 残留（非阻塞，供 W3b/W4c 集成注意）
- `_supersede_session` 只查 live job 不直接查 `authorization_claims.status=='CLAIMED'`；当前代码库无任何 `authorization_claims.insert`（grep 全仓确认），有 live job 即蕴含 claim 被持，故等效。W3b/W4c 若引入「先 claim 后建 job」的窗口，须同步此处增加 claim 检查。
- `db_eval["findings"]/["preflight"]` 与 view 的严格相等是新契约：要求 W3b `append_evaluation` 以与 `build_view` 完全一致的 `model_dump(mode="json")` 落库；非空 findings 的 DB 往返目前无测试（种子用空 findings），建议 W3b 联调补一条非空 findings 用例。
- `_existing_approval_id` 幂等返回不重算 `binding_mac`（行未变、confirm 路径重验），属可接受；naive wall 在 `configure` 与每次 `_wall`/`_wall_now` 双重校验。
- 锁序 plan→evaluation→session 与 W1 撤销的 session→plan 存在理论上的顺序差，但生产由 W4a 客户 advisory lock 串行化（state.py docstring「caller owns customer lock」），非本组件缺陷。

---

## 修复轮 2 复核（候选 `c317f82e4487183e1359c08c515aa4aa0fed7ad6`，相对 `dd43c1b`）

- 结论：**APPROVE**（P0 无；P1 无；P2 无）。协调者亲证三项残余全部关闭，无新发现、无回归。
- 亲跑（非 DB）：`.venv/bin/pytest tests/gateway/w4b1/test_planview.py tests/gateway/w4b1/test_binding.py -q` = **34 passed**；`tests/gateway/test_probes.py -k create_for_evaluation` = 1 passed。DB 57 项为实现者自报，须协调者真实 PG 亲跑，不据此判定。

### 逐项核证
- **① CLAIMED 无 job 不再被 supersede**：`_has_claimed`(service.py:232-248) 只按 `status=='CLAIMED'` 判定；`_supersede_session`(277-296) 对每条候选审批先锁 job 再 `_has_claimed`，任一命中即整体 409、不 transition（检查与 transition 分两遍循环，无部分 SUPERSEDE）；`create_for_evaluation`(411-414) 对**本 plan 自身**独立检查未消费 claim。`test_orphan_claim_without_job_blocks_supersede` 断旧 CONFIRMED+CLAIMED 无 job 全原样、新 plan 零新行；`test_consumed_claim_does_not_block_supersede` 断 CONSUMED 不误伤（仍 SUPERSEDE 旧审批）。锁定顺序 job→claim 与执行路径 plan→job→claim 一致，无 ABBA。
- **② existing 坏 MAC/错归属不再返回原 ID**：`_existing_approval_id`(183-229) 用锁定权威 `db_plan`/`db_eval`/`existing` 重建 `ApprovalBinding`（customer/sid/plan_hash/evaluation/view_hash/期限 + 配置 profile/key），先逐字段归属核对（201-211）再 `verify_binding`(225) 重算恒时 MAC；篡改 MAC/plan_hash 均 `APPROVAL_BINDING_MISMATCH 409`。合法幂等仍同 ID、不延时（199-200 只查不写）、不改 view_json。`test_existing_approval_with_tampered_mac_rejected`、`test_existing_approval_binding_field_mismatch_rejected`。`APPROVAL_BINDING_MISMATCH` 为既有 contracts 枚举（codes.py:63，本轮未改冻结区）。
- **③ 锁序统一 session-first**：`create_for_evaluation`(349-357) 先锁 session 再 plan→evaluation→job→claim→existing→supersede，与 W1 `cancel_session` 的 session→plan 一致，消除 plan↔session ABBA；state 执行路径仍 plan→job→claim→approval 且不锁 session，故无新环。`test_create_lock_order_is_session_first` 真双连接：T1 持 session 再锁 plan，T2 create 阻塞于 session 且不持 plan 锁，证无死锁（旧 plan-first 会 40P01）。P1-2 并发幂等仍由 session 锁串行化（同 sid 同 plan 同 eval），旧多 plan supersede 语义不变，无回归。
- **原发现回归**：三 P1、四 P2、两未确认的修复全部保留（`git diff --stat dd43c1b c317f82` 仅 service.py、tests/gateway/w4b1/db/test_creation.py、docs/progress，无 state/tables/migrations/runtime/config/contracts/bankcore/planview 改动，probe 未再动）。

### 未覆盖 / 边界（如实）
- 我无法 docker，DB 57 项（含新增 5 项真实 PG）由协调者亲跑；非空 findings 的 DB 往返仍无用例（种子用空 findings），留 W3b 联调。
- 「本库统一 session-first」在生产由 G3/G4 入口持同客户 `customer_lock` 串行化兜底；本库自身不验证/不声明该前置，文档如实（docs/progress 边界段）。跨表共享锁序未来延展按 session→plan→job→claim→approval。
- runtime/G6/G7/G9/G10/G17/OTP→W4b2、G8→W4c、完整 G4→W3b、`submit_risk_assessment` 面板→P4，未当本组件完成。
