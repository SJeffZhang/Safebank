# W4b1 PlanView and Approval Binding — Progress

实现者：DeepSeek V4 Pro via Claude Code（本机无头 CLI）。分支 `codex/w4b1-planview`。

## 范围与冻结

只写 `services/gateway/app/approval/**`、`tests/gateway/w4b1/**`、本进度、Makefile 末尾 `test-w4b1`、CI `p1-postgres` 追加该专项（协调者已批准）。共享 state/locks/tables/migrations/runtime/config/main、schema/contracts/bankcore/L3/依赖/PRD/AGENTS 均未改。

**范围说明（一处必要的测试修正）**：`tests/gateway/test_probes.py::test_create_for_evaluation_is_not_implemented` 是 W0 的旧占位探针，断言本接口抛 `NotImplementedError`；W4b1 实现后该接口缺配置时按契约 fail-closed 抛 `GatewayError(DEPENDENCY_UNAVAILABLE, 503)`，旧断言必然失败。已将该探针最小改为断言 503 fail-closed（`tests/gateway/test_probes.py:42`）。业务代码未受影响。

## 交付文件

- `services/gateway/app/approval/planview.py`：`build_view(ctx)->(PlanView,str)`、`content_hash(view)`。纯函数，不写 DB/HTTP。内容 hash 覆盖 plan/sid/origin、步骤顺序（目标姓名+尾号/本行标识、金额、memo、频次、业务到期日）、逐步余额、风险、不可逆、揭示全文与版本、risk_ack/otp；approval/evaluation ID、展示 status、审批 expires_at_wall、hash 本身为可变 envelope，不进内容 hash。金额全程整数分（`_yuan` 仅整除/取模）。
- `services/gateway/app/approval/service.py`：`configure(approval_hmac_key,*,profile,wall_now)`（≥32 字节 key、合法服务端 profile、aware wall，缺配置 503，绝不 policy_version 替 profile/默认随机 key/从请求读 key）；`ApprovalBinding` + `binding_mac`/`verify_binding`（HMAC-SHA256 覆盖 customer/sid/plan_id/plan_hash/evaluation_id/versions/clock_version/policy_version/profile/view_hash/expires_at_wall，constant-time 重算比对，expires 归一化 UTC）；`create_for_evaluation(conn,*,plan,evaluation,view)->str|None`（ALLOW 返回 None，DENY/REPLAN 拒，REQUIRE_APPROVAL 建 PENDING；同 evaluation 幂等不延时；同 sid 旧 PENDING/CONFIRMED SUPERSEDED；live job 拒；会话撤销/客户不一致拒；view_json 落真实 approval_id/status/期限，不用 apv_unassigned）。caller 持有事务，库不 commit/rollback、不调 L3/HTTP。
- `tests/gateway/w4b1/conftest.py`、`test_planview.py`（16）、`test_binding.py`（11）、`db/conftest.py`、`db/test_creation.py`（12 真实 PG）。
- `Makefile` 末尾 `test-w4b1`；`.github/workflows/checks.yml` `p1-postgres` 在 `test-w3a` 后追加 `make test-w4b1`（job 名未改）。

## 红→绿与退出命令（本机实际运行）

| 命令 | 结果 |
|---|---|
| `make test-w4b1`（真实临时 PG） | 39 passed（零 skip/xfail） |
| `make lint` | All checks passed；278 files already formatted |
| `make test-gateway` | 1955 passed, 490 skipped |
| `make test-gateway-db` | 17 passed |
| `make test` | 2267 passed, 550 skipped |
| `make test-w3a` | 45 passed |
| `make test-w2` | 246 passed |
| `make test-obligations` | 30 passed（既有 FK 环 SAWarning） |
| `make contracts-check` | PASS: contracts v1 match frozen schemas |

真实 PG 只使用 `tests/gateway/db/conftest.py` 的独立 tmpfs 临时库（`GATEWAY_DB_TESTS=1`），每测前以 admin 角色 TRUNCATE 该临时库；未触碰长期业务卷、密钥文件、未 make up/全栈、未 push/PR/merge、未派 agent、金额无 float。

## 覆盖的关键断言

- 面板字段：已有收款人/本人互转的目标姓名+尾号+本行/他行标识、整数金额、memo；卡 last4、定时周期与起止、AA 参与人、基金揭示（product_id/version/全文）。
- hash：仅 envelope（approval/evaluation/status/期限）变化 hash 相同；金额/目标/步骤顺序/memo/余额/风险任一变化 hash 不同；大整数 `9_999_999_999_999_999` 分不失真、输入 step.args 不变。
- 缺实体（收款人/卡）与缺揭示 fail-closed（ValueError）。
- 配置：短/非 bytes key、非法 profile、naive wall、非 callable wall 拒；create 未配置 503。
- 绑定：逐字段篡改/伪 MAC 拒；时区归一化后同一瞬间 MAC 相同。
- 真实 PG：PENDING 创建并落 view_json（真实 apv id）；ALLOW None 零插入；DENY/REPLAN 拒；同 sid 新审批 SUPERSEDED 旧审批、不同 sid 不误伤；同 evaluation 幂等不延时；5 分钟上限/过期拒；撤销会话 409、客户不一致 404；live job 409 且不释放 claim；tamper binding 字段 verify 失败。

## 边界与未实现（不留待伪装）

- 生产 runtime 接线、`G6/G7/G9/G10/G17` 确认/拒绝/OTP/查询端点、OTP 状态机均属 **W4b2**；本组件只提供显式配置的库 API，缺配置 fail-closed，未声称生产 G6/OTP/G4 已接通。
- `G8` 执行、`customer_lock` 集成、审批复核与 authorization_evidence 消费、prepare/execute/reconcile 属 **W4c**。
- `create_for_evaluation` 的「会话有效期」仅按 `revoked_at_wall` 判定；会话无独立 expires 列，原话有效期 cap 由 `build_view` 从 `ctx.authorization` 取 min。若后续 PRD 明确会话级有效期，需共享 owner 补充。
- 完整 G4 由 **W3b** 在其上以真实 W1→W2→W3→build_view/create_for_evaluation 零替身联调验收。

**不自称 P2 完成。** 后续顺序：W4b2（runtime/确认/OTP）→ W3b（真实 G4）→ W4c（执行/UNKNOWN 恢复）。
