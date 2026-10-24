# W4b2b Approval Endpoints Implementation Plan

> 用户已指定本机 Claude Code CLI / DeepSeek V4 Pro 实现与独立评审、自主推进；
> 协调者只任务/文档/亲验/发布，不编业务代码，不用自身 subagent。

**Goal:** 完成审批库实例适配（让审批创建/校验使用 GatewayRuntime 持有的密钥快照，不依赖全局 `approval.service.configure`），以及 G9 GET /approvals/{id}（PlanView）、G6 POST /approvals/{id}/confirm 非 OTP 路径（黄级；红级审批 fail-closed 拒）、**G7 reject**、**G17 列表**。所有写路径经 `state.transition` 与 `audit.append_tx`，request_id 幂等。

**Architecture:** W4b1 纯库已完成 PlanView/HMAC/审批创建；W4b2a 已完成运行时密钥快照（两 app 共享 `app.state.approval_hmac_key` / `otp_hmac_key`）。本组件（W4b2b）完成端点与适配；W4b2c 后续完成 G10 OTP 发送与红级 confirm OTP 校验。本组件不做资金执行（G8 是 W4c）；红级 confirm 只产生明确的 409 `OTP_REQUIRED`，不静默放行、不降级为黄级。

**Tech Stack:** 现有 Python 3.12 / FastAPI / SQLAlchemy / Pydantic，冻结依赖，不新增包。

**Spec:** PRD §7.4（原话与授权）、§7.10（审批与执行）、§9.1（通用契约）、§9.5（G5–G10、G17）、§9.8（错误码）；ADR-006/007/008；W4b1 验收与评审；W4b2a 验收与评审；W3-W4-integration-order；wave1-common。

## Global Constraints / ownership

**只写：**
- `services/gateway/app/api/approvals.py`（替换现有 stubs）
- `services/gateway/app/approval/adapter.py`（新文件：从 `request.app.state` 取密钥快照，调 `configure()` 或在每次调用传入；确定方案见下）
- `tests/gateway/w4b2b/`（新目录：纯函数测试 + `db/` 子目录真实 PG 测试）
- `tests/gateway/w4b2b/conftest.py`
- `docs/progress/W4b2b-approval-endpoints.md`

**不写**（冻结）：
- `approval/service.py`、`approval/planview.py`、`approval/__init__.py`（冻结，不新增参数或修改签名）
- `state.py`、`tables.py`、`migrations/**`、`runtime.py`、`config.py`
- `auth/matrix.py`（矩阵已注册全部路由，冻结）
- `schemas/**`（请求/响应模型重用现有 `schemas/` 或内联定义；PlanView 已冻结）
- `contracts/**`、`bankcore/**`、`bank-backend/**`
- `Makefile` 末尾只追加 `test-w4b2b` 目标；`.github/workflows/checks.yml` 仅 **coordinator** 追加 CI 一行，不在此 worker 范围
- `compose`、`scripts/keys.py`、`PRD`、`AGENTS`、依赖

**不读密钥文件、不读 `.env`/`.secrets`、不启动全栈/长期卷/Docker、不 push/PR/merge。** 真实 PG 仅已有独立 tmpfs fixture。

## Design decisions

### Instance adapter（密钥快照 → 库调用）

现状：`approval/service.py` 使用全局 `_config`（`configure()` 设置后 `_require_config()` 读取）。W4b2a 已将 `approval_hmac_key` / `otp_hmac_key` 安装在 `app.state` 上，两个 app 共享同一 runtime 同一密钥对象。

**方案**（协调者已裁定）：新增 `approval/adapter.py`，提供一个轻量 helper：

```python
async def approval_from_request(request) -> _ApprovalService:
    """从 request.app.state 取密钥快照，调 configure() 并返回可调用的服务引用。

    因两 app 共享同一 GatewayRuntime、同一密钥对象，configure() 可安全调用；
    幂等重复调用不改变配置。"""
```

`_ApprovalService` 是轻量包装，提供 `confirm(conn, approval_id, plan, evaluation, view_hash, risk_ack, now)`、`reject(conn, ...)`、`get_view(conn, ...)`、`list_approvals(conn, ...)`。

**不做**：修改 `approval/service.py` 全局签名或增加每函数 key 参数；为「两个不同 runtime」设计隔离——当前生产只有一个 runtime。

### 调用方矩阵（不变，已在 matrix.py）

| 接口 | 矩阵条目 |
|---|---|
| G9 `GET /approvals/{id}` | `F_APPROVE` — 只有 approve scope 的 app JWT 可读 |
| G6 `POST /approvals/{id}/confirm` | `F_APPROVE` — 同上 |
| G7 `POST /approvals/{id}/reject` | `F_APPROVE` — 同上 |
| G17 `GET /approvals` | `F_APP` — app scope 可列表，不含面板详情 |

L1 A+assistant **绝不能**调任何审批接口；矩阵已保证——4 条路由只允许 `F_APP` 或 `F_APPROVE`，无 `A_ASSISTANT` 条目。端点内仍需校验 `customer_id` 归属（JWT sub）。

### view_hash 精确匹配与错配/过期/错 customer/错 sid/已终态

所有修改接口（G6/G7）必须：
1. `request_id` 幂等（`request_idempotent`，事务内不发 HTTP）
2. 事务内从 DB 锁读审批行、plan 行、evaluation 行
3. 校验 `approval.customer_id == request.state.caller.customer_id`
4. 校验 `approval.sid` 与会话未被撤销
5. 校验 `approval.status` 为 PENDING
6. 校验 `approval.expires_at_wall > now_wall`
7. 传入 `view_hash` 与 `approval.view_hash` 逐字节相同，否则 409 `VIEW_HASH_MISMATCH`
8. G6 额外：黄级 `risk_ack=true`（红级直接 409 `OTP_REQUIRED`，不放行）
9. 状态迁移经由 `state.transition`
10. 审计经由 `audit.append_tx`（新事务，不含 HTTP）

### 状态迁移

| 操作 | 审批迁移 | plan 迁移 |
|---|---|---|
| G6 confirm（黄级） | PENDING → CONFIRMED，写 `confirmed_at_wall` | PENDING_APPROVAL → CONFIRMED |
| G7 reject | PENDING → REJECTED | PENDING_APPROVAL → REJECTED |

Plan 迁移使用 `state.transition`（合法边：`PENDING_APPROVAL → CONFIRMED`、`PENDING_APPROVAL → REJECTED` 均已在 `PLAN_TRANSITIONS` 与 `APPROVAL_TRANSITIONS` 中）。

确认后**不执行**——执行是 W4c 的 G8，不在此组件。

### 错误码

全部来自 `safebank_contracts.codes.Code`（`packages/contracts/src/safebank_contracts/codes.py`）：

| 场景 | code |
|---|---|
| approval 不存在或不属于客户 | `OWNERSHIP_MISMATCH` / `NOT_FOUND`（404） |
| approval 已终态（非 PENDING） | `STATE_CHANGED` |
| view_hash 不匹配 | `VIEW_HASH_MISMATCH` |
| risk_ack 缺失（黄级） | `RISK_ACK_REQUIRED` |
| 红级审批 attempt | `OTP_REQUIRED`（W4b2c 前统一拒绝） |
| approval 已过期 | `APPROVAL_EXPIRED` |
| 会话已撤销 | `AUTHORIZATION_CANCELLED` |
| 并发 / 请求体不同 | IDEMPOTENCY_CONFLICT / EXECUTION_IN_PROGRESS |

**不新增 code**——现有的全部够用。G17 只查询，不需要新 code。

### G17 列表

```
GET /approvals?status=PENDING&cursor=&limit=
→ {items: [{approval_id, plan_id, session_id, status, risk, expires_at_wall}], next_cursor}
```

- `status` 参数可选，取 `ApprovalStatus` 或 `ALL`（默认 PENDING）
- `risk` 从 evaluations 表 JOIN 读取（或 approvals+plans→evaluations）
- 按 `customer_id` 过滤（JWT sub）
- 按 `created_at_wall DESC` 排序（PRD 未规定，取创建时间降序）
- 分页用 `cursor`（`(created_at_wall, id)` 稳定排序）

### request_id 幂等

G6/G7 使用 `idempotency.request_idempotent`，route 为 `"/approvals/{id}/confirm"` 或 `"/approvals/{id}/reject"`。同事务内完成所有校验与迁移；事务中不调用 HTTP。

### 审计

G6/G7 成功后，在**单独事务**中通过 `audit.append_tx` 写入审计事件。事件类型：`APPROVAL_CONFIRMED` / `APPROVAL_REJECTED`。失败时不写（依赖幂等缓存重放）。

### 真实 PG vs 纯函数

- **纯函数**（内存 test）：view_hash 比对、risk_ack 逻辑、红级拒绝、错误码选择、PlanView 字段完整性
- **真实 PG（tmpfs fixture）**：全链路 G6/G7/G9/G17 在真实 DB 事务中；锁/归属/状态迁移/幂等/过期/错配/会话撤销/已终态拒绝/审计追加/G17 分页与过滤

不 mock `state.transition` 或 `audit.append_tx`。

### 红级 confirm 的 fail-closed 哨兵

因为 G10 OTP 发送在 W4b2c 才实现，本组件收到红级 confirm 请求时：
- 读取 `approval_id` → 获取对应的 evaluation → `risk == RED`
- 检查是否有已成功的 OTP challenge（`otp_challenges` 表）
- 如果 `otp` 字段为空/null：返回 409 `OTP_REQUIRED`（不会放行、不会降级为黄级确认）
- 如果 `otp` 有值：也返回 409 `OTP_REQUIRED`（本组件不验证 OTP——W4b2c 做）

这是一个**哨兵拒绝**，确保没有请求能绕过 OTP。

## Interfaces / fixed choices

### G9 GET /approvals/{approval_id}

```
请求: F+app approve scope
返回: PlanView（evaluations.view_json 的已存储内容）
      或 404 (OWNERSHIP_MISMATCH/NOT_FOUND)
```

- 从 `approvals` 读审批行 → 校验 `customer_id` → 引用 `evaluations.view_json` → 直接返回
- 不重新调用 `build_view`（面板内容是确认时不变的快照）
- 审批 SUPERSEDED/USED/REJECTED/EXPIRED 仍可读（历史面板），但 LOCKED 不可读
- `view_json` 中 approval_id/status/expires_at_wall 已是真实值（W4b1 service.py 已持久化）

### G6 POST /approvals/{approval_id}/confirm

```
请求: F+app approve scope
body: {request_id, view_hash, otp?: string, risk_ack: bool}
返回: {approval_id, plan_id, status: "CONFIRMED"}
```

- `request_idempotent` 包裹
- 校验顺序：approval 存在/归属 → status=PENDING → 未过期 → view_hash 匹配 → 会话未撤销 → risk_ack（黄级必须 true，红级拒）
- 单事务内：`state.transition(approval PENDING→CONFIRMED)` + `state.transition(plan PENDING_APPROVAL→CONFIRMED)` + `UPDATE approvals SET confirmed_at_wall=now, risk_ack=...`
- 事务提交后：`audit.append_tx(APPROVAL_CONFIRMED)`
- 红级请求：直接 409 `OTP_REQUIRED`（不区分 otp 是否提供——W4b2c 前统一拒）

### G7 POST /approvals/{approval_id}/reject

```
请求: F+app approve scope
body: {request_id, view_hash}
返回: {approval_id, plan_id, status: "REJECTED"}
```

- 同样 `request_idempotent` 包裹
- 与 G6 相同前置校验（除 risk_ack）
- 单事务内：`state.transition(approval PENDING→REJECTED)` + `state.transition(plan PENDING_APPROVAL→REJECTED)`
- 审计：`audit.append_tx(APPROVAL_REJECTED)`

### G17 GET /approvals

```
请求: F+app scope
query: ?status=PENDING|CONFIRMED|REJECTED|ALL&cursor=&limit=
返回: {items: ApprovalListItem[], next_cursor}
ApprovalListItem: {approval_id, plan_id, session_id, status, risk, expires_at_wall}
```

- JOIN `evaluations` 取 `risk`：`SELECT approvals.*, evaluations.risk FROM approvals JOIN evaluations ON approvals.evaluation_id = evaluations.id WHERE approvals.customer_id = $1`
- 可选 `status` 过滤（枚举值或 ALL）
- 分页游标：`(created_at_wall DESC, id DESC)`（或正序后再反转；PRD 未规定方向，取最新在前）

### 适配器 adapter.py

- 不含业务逻辑
- 从 `request.app.state` 读两 key + profile + wall_now
- 每次端点调用时，如果 `_config` 为空或 profile/key 与当前不同，调 `configure()` 更新
- 因两 app 共享同一 runtime、key 不变，`configure()` 最多调用一次
- `otp_hmac_key` 暂不使用（G10 在 W4b2c），但 adapter 持有以备后续

## Task checklist（先红后绿）

### Task 1: adapter + G9 纯函数单元

Files: `approval/adapter.py`、`api/approvals.py`（仅 G9）、`tests/gateway/w4b2b/test_g9_get.py`

- [ ] 红测：无凭证/仅 app 令牌（缺 approve scope）调 G9 得 403；approval 不存在得 404；approval 属其他客户得 404；调用方 customer_id 非 approval.customer_id 得 404
- [ ] 红测：G9 成功返回 PlanView 全部字段；SUPERSEDED/USED/REJECTED/EXPIRED 审批可读；LOCKED 不可读
- [ ] 跑 `.venv/bin/pytest tests/gateway/w4b2b/test_g9_get.py -q` 确认红
- [ ] 实现 adapter + G9 handler（用 `request.app.state` wiring），不用 `request_idempotent`（G9 只读）
- [ ] 定点绿并本地 commit

### Task 2: G6/G7/G17 端点（全链路真实 PG）

Files: `api/approvals.py`（G6/G7/G17）、`tests/gateway/w4b2b/db/conftest.py`、`tests/gateway/w4b2b/db/test_confirm.py`、`test_reject.py`、`tests/gateway/w4b2b/db/test_list.py`

- [ ] 红测 G6：黄级 confirm 全部校验（approval 存在/归属/PENDING/未过期/view_hash/会话活跃/risk_ack=true）→ CONFIRMED；以上每一项篡改/缺失对应的 409/404
- [ ] 红测 G6：红级 confirm（`risk=RED`）无论是否带 otp 都 409 `OTP_REQUIRED`
- [ ] 红测 G6：同 request_id 重复 confirm 幂等返回原结果；不同 request_id 但同 approval 第二次 confirm 409（已 CONFIRMED）
- [ ] 红测 G7：reject 全部校验 → REJECTED；已 CONFIRMED/REJECTED/SUPERSEDED/EXPIRED 审批拒绝 reject
- [ ] 红测 G17：空列表、status 过滤、分页、多 approval 排序
- [ ] 红测：G6/G7 成功后审计行存在（读 audit_events 表）；失败不写审计
- [ ] 真实 PG 红测（`GATEWAY_DB_TESTS=1`，独立 tmpfs，先 truncate 再 seed 最小数据）
- [ ] 实现 → 绿 → 本地 commit

### Task 3: Acceptance

- [ ] `make setup` / `make lint` / `make test` / `make test-gateway` / `make test-gateway-db` / `make test-w4b2b` / `make test-w4b1` / `make test-w1` / `make test-w2` / `make test-w3a` / `make test-obligations` / `make test-shared-runtime` / `make contracts-check` 全跑
- [ ] **新 `make test-w4b2b` 目标**：`GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w4b2b -q`，零 skip/xfail
- [ ] CI 需求说明（由协调者在发布时向 `checks.yml` 的 p1-postgres 追加）：
  ```
  - run: make test-w4b2b
  ```
  只在已有 job 末尾追加一行，不改 job 名。
- [ ] 进度写实际红绿/PG 与纯函数边界、未实现 G10/红级 OTP/执行/通知、后续 W4b2c→W3b→W4c
- [ ] 本地 commit 署名 DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>，冻结停止，待协调者亲验/独审

自审：本组件不创建新表/列/错误码/全局状态；所有写路径经 `state.transition`；红级 fail-closed 不放行；不执行 L3；不含 HTTP 调用。
## 协调者裁定（2026-10-24，与上文冲突时以本节为准）

- **D0 实例适配（否决设计稿中的 `wire_approval(request)` → 每请求调用全局 `configure()`）**：请求路径中不得调用或依赖 `approval.service.configure` 或任何模块级可变密钥状态。审批的创建、校验、确认改为显式实例：由 GatewayRuntime 持有一个用其密钥快照构造的审批对象（或把 key 作为显式参数传入纯函数），端点从 `request.app.state` 的 runtime 取用。两个 runtime 并发处理请求时互不串用，这一点必须有红测证明：两个 app、不同 key、asyncio.gather 并发确认，各自只认自己 key 生成的 MAC。现有 W4b1 调用点与测试按此迁移，行为不变。全局 `configure` 若无其他调用者，可以删除；若保留，只能用于测试，并在 README 注明。
- **D1** G6/G7 成功时，plan 同步 `PENDING_APPROVAL → CONFIRMED / REJECTED`，与审批状态的迁移在同一短事务内完成，只经 `state.transition`；确认后**不执行**。
- **D2** G9 返回持久化的 view_json。如果 `status` 或 `expires_at_wall` 在 view_hash 的哈希域内，不得覆盖其中的值，状态判定只看 approvals 行。不在哈希域内的字段，可以用 approvals 行的当前值。worker 必须先读 W4b1 代码确认哈希域，再写进进度文件。
- **D3** G17 的 risk 通过 JOIN evaluations 取得，确认。
- **D4** risk_ack：只有面板 `risk_ack_required=true` 时，`risk_ack=false` 才返回 409 `RISK_ACK_REQUIRED`；`risk_ack_required=false` 时允许 false（依据 PRD §5「申购须勾选」、PlanView 的 `risk_ack_required` 字段）。否决「黄级一律要求 risk_ack」。
- **D5 不新增迁移**：G17 按 approval_id（ULID，时间有序）倒序，cursor 用 approval_id。不补 `created_at_wall` 列。
- **D6 不新增错误码（contracts 冻结）**：W4b2c 之前，红级（`otp_required=true`）审批的 G6 一律返回 503 `DEPENDENCY_UNAVAILABLE`，DB 不变、审批仍为 PENDING。不得放行，也不新增 `OTP_REQUIRED`。
- 其余（调用方矩阵、view_hash 精确匹配、错配/过期/跨 customer/跨 sid/已终态/旧原话拒绝且 DB 不变、request_idempotent、审计 append_tx）按上文执行。新增 `make test-w4b2b`（Makefile 末尾），并在 checks.yml 的 p1-postgres job 追加一行运行它；该专项真实 PG 零 skip。
