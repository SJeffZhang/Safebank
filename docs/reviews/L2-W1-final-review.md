# W1 检查点终审（HEAD 1722995，origin/main aa42a0c）

2026-10-24；独立只读评审。评审范围 `git diff 44ded2f..1722995`（17 提交超前 / 11 落后），并核对 origin/main 新增（S1f-demo-route-mount 等 11 条 docs 提交）的合并风险。未改任何代码；未运行 make up/down/test-p1/test-integration；未碰 Docker 数据卷；未读 .env/.secrets。定点验证：`.venv/bin/pytest tests/gateway/w1/test_parser.py tests/gateway/w1/test_review_r1_parser.py tests/gateway/w1/test_predicates.py`（102 passed）与 `test_auth_matrix.py test_pin.py test_pin_files.py test_models_forms.py test_w1_runtime_resources.py`（1427 passed）。PG 用例因本环境 docker 被拒未能亲跑，已按源码 + 测试文件逐项核对（协调者已另行复跑完整五退出：test-w1 1687 passed / 0 skipped）。

## 结论：APPROVE

无 P0 / P1。撤销追加任务、冻结边界、安全、测试质量与 G22/S1f 合并风险五项均核对通过。仅有 2 条 P2 代码质量观察与 1 条已披露的共享边界，不阻塞合并。

## 逐项核对

### 1. 撤销追加任务（W1-revocation-deferred.md）

全部满足，且有真实 PG 测试逐条覆盖：

- **savepoint + 有界重读**：`utterance/lifecycle.py:37-53` `_transition_pending`（3 次 `begin_nested` + 重读，只捕 `GatewayError(STATE_CHANGED,409)`）；`lifecycle.py:158-191` `_invalidate_plan`（3 次重读，`except GatewayError` 仅收敛 `STATE_CHANGED/409`，其余重抛）。`_invalidate_plan` 内只可能产生 `_conflict()=STATE_CHANGED/409`（`state.py:242-243`），`state.transition` 的非法边 `ValueError` 与 SQL/连接异常均不被捕获、直接外抛——符合「ValueError/SQL 不吞成 409」。
- **稳定坏 scope/claim 隔离**：`_invalidate_plans`（`lifecycle.py:194-221`）对坏 plan 仅 `pending.append` 后继续其余 plan/approval；`state.py:812-822` orphan 路径 shape guard + `_claims` 对空/重复/object scope 与缺/额外/非 CLAIMED claim 返回 409。`_invalidate_plan` 稳定冲突（`_plan_snapshot == before`）返回 True（pending），不改 job/claim。
- **固定 409 语义**：`lifecycle.py:20-26` `deferred_error()` details 严格为 `{authorization_revoked:true, cleanup_pending:true}`；`api/sessions.py:140-156` 与 `api/auth.py:111-125` producer 内同事务 `audit.chain.append(conn,...)` 后返回 `(409, body)`，`request_idempotent` 缓存并同事务提交；事务外 `lifecycle.py:29-34` `raise_cached_error` 只重建 code/message/details（不含旧 trace），由 middleware 用本次 trace 生成统一错误体。
- **不发布替代授权**：`service.py:374-379`（correction）与 `service.py:460-464`（typed/hint/action/slots 写 scope）在 pending 时提前 `return Revocation`，不插新 utterance、不签 JWT、不写 used_by、不 RESOLVED；`service.py:514-515` 的 `used_by` 只在成功路径写。
- **新写 scope 必重扫 live jobs**：`lifecycle.py:94-95` `supersede()` 无条件 `_invalidate_plans`（注释明确「Recheck even after an earlier request has already superseded every root」），故 replaced=False 时也不跳过。
- **前置错误不吞**：`service.py:405-421` hint/action 来源不存在/已消费/过期分别 404/409/409 直抛；`test_revocation_deferred.py:437-499` `test_prevalidation_failure_does_not_commit_revocation` 断言 error details != deferred details 且旧原话仍 ACTIVE。
- **旧 logout JWT 401 / 新 JWT 重放只回缓存**：`auth/deps.py:101-118` shared dependency 对 F/A customer JWT 先 `check_revocation`；`auth.py:74` 锁内再检；`test_revocation_deferred.py:130-177` 覆盖旧 JWT 401、新登录后重放原 request_id 回缓存 409 且新 sid 不撤销、不同 body `IDEMPOTENCY_CONFLICT`。
- **故障注入**：`test_revocation_deferred.py:502-588` 用 SQLAlchemy 钩子在撤销/audit insert/dedup update/commit 前注入 `SELECT 1/0`，断言 503 且三者共同回滚；`test_revocation_cas_races.py:206-266` `pg_terminate_backend` 真实断连 503、不假成功。

### 2. 冻结文件与所有权（common §2）

`git diff --name-only 44ded2f..HEAD` 无任何冻结文件（state.py/tables.py/migrations/main.py/config.py/policy/registry.py/idempotency.py/errors.py/audit/chain.py/schemas/packages/bank-backend/pyproject/uv.lock/requirements/docker-compose/PRD.md）。唯一 `AGENTS.md +2` 为协调者日志（执行者署名「Codex（协调者）」），`.github/workflows/checks.yml +1` 为已授权的 `make test-w1` 一行，`Makefile +4` 为末尾追加 `test-w1` 目标——均符合授权。

### 3. 安全

- `auth/tokens.py:40` `secrets.compare_digest` 常量时间比较服务令牌；`pin.py:72-77` argon2 `verify` 常量时间、未知客户走 dummy hash 同价路径。
- `tokens.py:68-121` `verify_jwt`：`algorithms=["EdDSA"]`、`issuer`/`audience`/`options.require` 齐全；手动拒绝 list aud（`isinstance(aud,str) and aud==expected_aud`）、`scope`/`utts` 必须为字符串列表。
- origin/customer_id 不来自请求体：`schemas/identity.py:20-26` `Caller.origin` 由凭证推导；`deps.py` 从 JWT sub 取 customer_id；`api/sessions.py`/`api/auth.py` 全程用 `request.state.caller.customer_id`，login 的 body customer_id 仅用于「出示身份 + PIN」本身。
- 无 fail-open：`tokens.py:257-258` `check_revocation` 仅在 `caller.customer_id is None`（纯服务令牌路由）早退；`runtime.py` 缺 DB/L3/clock/私钥均 503。

### 4. 测试质量

- PG 真实：`db/conftest.py` 复用 W0 临时 PG（tmpfs、随机端口、`--rm`），`support.py:33` 真实 engine + 真实 locks.configure + 真实 runtime，仅 L3 用 `FakeL3`（已按任务书交付 3 登记）。`test-w1` 目标显式 `GATEWAY_DB_TESTS=1`，158 条 PG 收集、0 skip。
- 断言非空洞：`test_revocation_deferred.py`/`test_revocation_cas_races.py`/`test_live_job_revocation.py` 均逐字段 SQL 断言（job 状态/attempt/retry_count/deadline/cap/claim/evidence 不变、`verify_chain` 审计链、epoch 恰增一、审计事件恰一份），并断言 `assistant_token`/`decision` 不出现于 deferred 错误体。
- 未把 fake 当生产：`support.py` 与进度文档明确「L3 仅契约替身」「真实资金/HTTP fence/reconcile 归 W4」，未冒称。

### 5. G22 / S1f 合并风险

- W1 未改 `main.py`/`matrix.py`；`tests/gateway/w1/` 无任何 `demo-customers`/`demo_mode`/`eval_mode` 引用，`test_auth_matrix.py` 只测 `resolve_caller`（不触达 handler 404）。`auth.py:149-160` `demo_customers` 的 handler 404 分支在 S1f 条件挂载后成为死代码但不冲突；进度文档（W1.md 末段）已明确披露该共享边界。风险低，无阻塞。

## 发现

### P0

无。

### P1

无。

### P2（不阻塞，建议后续清理）

- **P2-1 correction 双次清理**：`service.py:376` 对 correction 先 `supersede_session`（内含 `supersede`→`_invalidate_plans`），`service.py:461` 又因 `parsed.scope.action_kinds` 再次 `supersede(keep=())`→`_invalidate_plans`。两次对同批 plan 重复 `_invalidate_plan`（幂等、只多几次 SELECT），与设计报告「避免 correction 双次清理」的指导不完全一致。建议 correction 分支跳过第二次 `supersede` 或仅复用其结果。
- **P2-2 `intent_lexicon.yaml` 死键**：`question`/`periods`/`correction` 键被定义但 parser 从未读取（`parser.py` 只用 actions/predicates/phonetic/grammar/cancel；改口用 `CORRECTION_PREFIX` 正则、period 硬编码）。疑问句拒绝实际靠整句 fullmatch 语法（`test_parser.py:43-46,113-115` 已覆盖），非正确性缺陷，但死配置易误导维护者。

## 未确认（非发现，记录）

- 若 plans 行 `origin` 被直接篡改为非 agent/app_form，`state.ExecutionIdentity.__post_init__`/`OrphanExecutionIdentity.__post_init__`（`state.py:231-239,751-762`）会抛 ValueError，进而中止整笔撤销事务（500 回滚）而非将该 plan 隔离为 pending。与「ValueError 不吞」一致、fail-closed，但单条坏行会阻塞整客户撤销；实际 origin 恒由 L2 写入，概率极低。
- 本环境未亲跑 PG 用例（docker 被拒）；上述 PG 结论基于源码与测试文件核对，最终以协调者复跑的完整五退出（test-w1 1687/0 skip）为准。
