# W4b2b Design Notes：事实与缺口（只读代码核查）

2026-10-24。只读核查现有代码，不修改任何文件。

## 1. 关键文件定位

| 文件 | 行号 | 事实 |
|---|---|---|
| `approval/service.py:63` | `_config: _ApprovalConfig \| None = None` | 全局模块级 _config 变量 |
| `approval/service.py:66-83` | `configure(approval_hmac_key, *, profile, wall_now)` | 全局 setter，校验 key≥32byte、profile∈VALID_PROFILES、aware wall。当前无任何调用方 |
| `approval/service.py:86-89` | `_require_config()` | 取 `_config`，None 时抛 503 `DEPENDENCY_UNAVAILABLE` |
| `approval/service.py:307-495` | `create_for_evaluation()` | 已实现审批创建，会调 `_require_config()`（319 行） |
| `api/approvals.py:1-13` | 全为 stubs | `router.get("/approvals")(stub(...))` 等 5 条路由，全部占位 |
| `auth/matrix.py:74-78` | MATRIX 已注册 | 4 条审批接口全部登记，与 G 编号一致：G9=F_APPROVE、G6=F_APPROVE、G7=F_APPROVE、G17=F_APP |
| `runtime.py:85-125` | `_validate_approval_keys()` | 校验 approval/OTP key 有效，返回 bytes pair |
| `runtime.py:128-151` | `GatewayRuntime` | `approval_hmac_key: bytes` 与 `otp_hmac_key: bytes`，均 `repr=False`，`install()` 装到 `app.state` |
| `tables.py:176-228` | evaluations、approvals、otp_challenges | 三表齐全，无需新列 |
| `schemas/planview.py:34-50` | `PlanView` Contract | 字段：approval_id、plan_id、evaluation_id、session_id、status、origin、risk、steps、preflight、findings、disclosures、risk_ack_required、otp_required、expires_at_wall、view_hash |
| `state.py:87-105` | `APPROVAL_TRANSITIONS` | PENDING→CONFIRMED/REJECTED/EXPIRED/SUPERSEDED/LOCKED 合法；CONFIRMED→EXPIRED/SUPERSEDED/USED 合法 |
| `state.py:42-85` | `PLAN_TRANSITIONS` | PENDING_APPROVAL→CONFIRMED/REJECTED/EXPIRED/SUPERSEDED/EXECUTING 合法 |
| `state.py:171-205` | `transition()` | 校验 TRANSITIONS 字典，调 `_protect_transition`，UPDATE WHERE status IN(from_statuses)，rowcount≠1 抛 409 |
| `state.py:429-484` | `_approval()` | 在执行路径中锁审批行、校验 approval_id/plan/customer/sid/plan_hash/status/view_hash/expiry/approved_evaluation |
| `state.py:632-639` | `LIVE_JOB_STATUSES` | EXECUTING/FENCING/PAUSED/UNKNOWN |
| `state.py:642-681` | `_protect_transition()` | approvals 表没有特殊保护（`else: return`），即普通 DML 可改 approval 状态 |
| `contracts/codes.py:56-68` | Code 枚举 | 全部所需 code 存在：STATE_CHANGED、APPROVAL_*（6个）、VIEW_HASH_MISMATCH、RISK_ACK_REQUIRED、OTP_*（3个） |
| `idempotency.py:29-98` | `request_idempotent()` | route 级幂等，事务内 INSERT→produce→UPDATE，异常删行；重放 matching hash 返回原结果 |
| `audit/chain.py:133-155` | `append_tx()` | 独立短事务（`engine.begin()`），接收 engine/customer_id/event_type/payload/event_id/trace_id/plan_id/now_wall |
| `config.py:38-39` | Settings | `approval_hmac_key` / `otp_hmac_key`，`repr=False`，`from_env` 取值 |
| `schemas/enums.py:34-41` | `ApprovalStatus` | PENDING/CONFIRMED/REJECTED/EXPIRED/SUPERSEDED/USED/LOCKED |
| `schemas/enums.py:19-32` | `PlanStatus` | EVALUATED/REPLAN/DENIED/PENDING_APPROVAL/CONFIRMED/EXECUTING/UNKNOWN/EXECUTED/FAILED/REJECTED/EXPIRED/SUPERSEDED |

## 2. 全局状态与实例适配：最小方案

### 2.1 问题

`approval/service.py` 的全部函数（`create_for_evaluation`、内部 helper）都通过 `_require_config()` 访问全局 `_config`。当前无任何地方调用 `configure()`——所以如果你现在调 `create_for_evaluation`，会在 319 行抛 503 `DEPENDENCY_UNAVAILABLE`。

W4b2a 已将 key 放在 `app.state.approval_hmac_key` / `app.state.otp_hmac_key`，但不调 `configure()`。

### 2.2 方案

在端点（api/approvals.py）中，每次请求从 `request.app.state` 取 key、profile、wall_now，调 `approval.service.configure()`。因为：

- 两个 FastAPI app（public + internal）共享同一个 `GatewayRuntime` → 同一组 bytes key
- `configure()` 是幂等的——相同参数重复调不改变行为
- key 是 immutable bytes，无竞态

新增 `approval/adapter.py`:

```python
# ~15 行
from .service import configure

def wire_approval(app_or_request) -> None:
    """从 app.state 取密钥快照，调 service.configure() 安装全局配置。
    同一进程两 app 共享同一 GatewayRuntime → configure() 最多生效一次。"""
    state = getattr(app_or_request, 'state', app_or_request)
    configure(
        state.approval_hmac_key,
        profile=state.settings.profile,
        wall_now=state.wall_now,
    )
```

在 FastAPI lifespan startup 或每个请求开头调 `wire_approval(request)`。实际 `configure()` 内部有 `if _config is not None: return` 的优化（但当前无此优化——需要加一行）。或者更简单：在端点依赖中调一次即可。

### 2.3 建议

在 api/approvals.py 的每个 endpoint 开头调 `wire_approval(request)`，它对 `configure()` 加一个 early-return（`if _config is not None: return`）避免重复校验。不改变 approval/service.py 的公共 API。

## 3. 现有表/迁移：已足够

| 表 | 列 | 状态 |
|---|---|---|
| `approvals` | id、plan_id、evaluation_id、customer_id、sid、plan_hash、view_hash、binding_mac、expires_at_wall、status、risk_ack、confirmed_at_wall | **齐全**。`risk_ack` 当前 `server_default="false"`，G6 需 UPDATE 为 true |
| `evaluations` | view_json（JSONB） | **齐全**。G9 读此字段返回 PlanView；W4b1 `create_for_evaluation` 已持久化真实 PlanView |
| `otp_challenges` | approval_id（PK）、code_digest、code_ciphertext、view_hash、expires_at_wall、attempt_count、sent_count、last_sent_at_wall、locked_at_wall | **齐全**。W4b2c 用，本组件只查询 `otp_required` 判断 |
| `execution_jobs` | status 含 EXECUTING/FENCING/PAUSED/UNKNOWN | **齐全**。G6 不直接读写，但 W4b1 `create_for_evaluation` 已保护 live job |

**不需要新迁移/列/表**。本次不碰 migrations/。

## 4. PRD 不闭合 / 矛盾（待协调者裁定）

### 4.1 Plan 状态在 G6/G7 时是否迁移？

PRD §9.5 G6 响应 `{approval_id,plan_id,status:CONFIRMED}`——`status:CONFIRMED` 是 approval 还是 plan？

- **当前代码**：`PLAN_TRANSITIONS` 中 `PENDING_APPROVAL → CONFIRMED` 合法。`APPROVAL_TRANSITIONS` 中 `PENDING → CONFIRMED` 合法。
- **PRD §7.10.4**：执行前 "plan 处于 CONFIRMED" 可作为执行入口判断。
- **审慎推断**：G6 后 plan status 也应迁移到 CONFIRMED（而非停留在 PENDING_APPROVAL）。`state._approval()` 在执行路径检查 approval status==CONFIRMED，不直接检查 plan status——所以 plan status 转成 CONFIRMED 是状态一致性问题但不是硬依赖。

**建议**：G6 后同时迁移 both（plan PENDING_APPROVAL→CONFIRMED + approval PENDING→CONFIRMED）；G7 后同时迁移 both（plan PENDING_APPROVAL→REJECTED + approval PENDING→REJECTED）。

### 4.2 view_json 中 stale 状态

PRD §9.5 G9 返回 PlanView。当前 W4b1 `create_for_evaluation` 已将真实 PlanView（含 approval_id/status/expires_at_wall）写入 `evaluations.view_json`，其中 `status=PENDING_APPROVAL`。

G6 后审批变为 CONFIRMED，但 `view_json` 中的 `status` 仍是 `PENDING_APPROVAL`——这是 W4b1 写入时的值。

**问题**：G9 返回的 PlanView 中 `status` 应与当前审批/plan 状态一致。

**选项**：
- (a) G9 返回时动态改 `view_json` 中的 `status` 字段（读 DB 行，不写回）
- (b) G6/G7 后 UPDATE `evaluations.view_json` 的 status 字段

**建议采用 (a)**——G9 handler 读取 `view_json`，按当前 `approvals.status` 覆盖 `status` 字段，按当前 `expires_at_wall` 覆盖 `expires_at_wall`。不写回 evaluations 表（审批行已权威）。

### 4.3 G17 `risk` 来源

PRD §9.5 G17 返回字段包含 `risk`。当前：
- `approvals` 表没有 `risk` 列
- `risk` 在 `evaluations` 表（`evaluations.risk`，String(16)）
- 需要通过 `approvals.evaluation_id → evaluations.id` JOIN

G17 查询时需要 JOIN evaluations 取 risk——SQLAlchemy 简单 join。

### 4.4 G17 响应中的 `status` 语义

PRD §9.5 G17 `{items:[{approval_id,plan_id,session_id,status,risk,expires_at_wall}]}`——`status` 是 approval 的 status。无需 JOIN plans。

### 4.5 G9 的历史审批可见性

PRD §9.5 G9 "审批 ID 唯一定位计划"——可见性未明确限制。按 §7.2 approvals 表设计，USED/SUPERSEDED/EXPIRED/REJECTED 审批的 view_json 仍然存在。

**建议**：G9 对所有非 LOCKED 状态的审批返回 PlanView。LOCKED（OTP 锁定）拒绝 409 `OTP_LOCKED`。

### 4.6 G6 body 的 `otp` 字段在黄级

PRD §9.5 G6 body: `{request_id, view_hash, otp: string?, risk_ack: bool}`——otp optional。

黄级审批不需要 OTP。但 body 中 otp 为 null 时是「未提供」还是「不需要」？

**建议**：黄级审批忽略 otp 字段；红级审批不检查 otp 是否正确（W4b2c 做），只验证 `risk_ack=true` 并返回 `OTP_REQUIRED`。

### 4.7 risk_ack 持久化

PRD §7.10 审批面板包含 `risk_ack_required` 布尔值。G6 body 中有 `risk_ack: bool`。

当前 `approvals.risk_ack` 是 Boolean，`server_default="false"`。G6 时 UPDATE 为 true。

**问题**：risk_ack=false 但黄级审批 confirm 时，前端应能传 false 吗？

PRD 写「确认带 view_hash、risk_ack」，§9.5 G6 body 中 `risk_ack: bool`。未显式说 false 会导致什么。

**建议**：黄级审批 `risk_ack` 必须为 true，否则 409 `RISK_ACK_REQUIRED`。这与 PRD §7.10 risk_ack_required 字段语义一致——若面板要求确认就绪，用户必须 acknowledge。

### 4.8 审批行没有 `created_at_wall`

当前 `approvals` 表只有 `expires_at_wall`、`confirmed_at_wall`，缺少 `created_at_wall`。

G17 返回 `expires_at_wall`（PRD 明确），但条目排序没有明显字段。

**建议**：给 `approvals` 加 `created_at_wall` 列（TIMESTAMPTZ NOT NULL DEFAULT now()）。G17 按此列倒序排列。这需要一次最小迁移，由协调者指定共享 owner（或由本组件加一个 `0003_approval_timestamp` 迁移）。因为当前 `approvals` 表在生产中审批条数为 0（尚无任何 endpoint 能创建审批），补列安全。

替代方案：用 `expires_at_wall - 5min` 估算创建时间（不准确），或 JOIN `evaluations.created_at_wall`（但审批不等于评估时刻）。

**此列为唯一需要通过迁移添加的列。**

### 4.9 OTP_REQUIRED 错误码

`codes.py` 中没有 `OTP_REQUIRED`。现有 OTP 相关 code 只有 `OTP_INVALID`、`OTP_EXPIRED`、`OTP_LOCKED`。

红级 confirm 在 W4b2c 实现前应返回什么 code？

**建议**：
- 选项 (a)：新增 `OTP_REQUIRED` code（一行的 StrEnum 成员），同步 `contracts/schema_v1.json`
- 选项 (b)：返回 `STATE_CHANGED` 409 + details message "OTP required"
- 选项 (c)：返回 `OTP_INVALID` 409（语义不全对——不是 invalid，是 missing）

**推荐 (a)**——由于 codes.py 在冻结 contracts 中，需要协调者裁定是否允许在冻结区新增一行枚举成员。这是最小增量（一行）。

## 5. 需要协调者裁定的问题清单

1. **Plan 状态在 G6/G7 时是否同时迁移？** 建议 yes（both plan + approval）。
2. **G9 返回的 `status` 与 `expires_at_wall` 是否动态覆盖？** 建议 G9 handler 从 `approvals` 行覆盖这两个字段，不写回 evaluations。
3. **G17 的 `risk` 来源？** 确认通过 `approvals.evaluation_id → evaluations.risk` JOIN。
4. **G6 黄级 confirm 的 `risk_ack=false`？** 建议必须 true，否则 409 `RISK_ACK_REQUIRED`。
5. **`approvals` 表补 `created_at_wall` 列？** 建议补列（最小迁移），否则 G17 排序没有可靠字段。
6. **新增 `OTP_REQUIRED` code？** 建议在 `codes.py` 加一行 `OTP_REQUIRED = "OTP_REQUIRED"`，同步 contracts schema_v1.json。
7. **红级 confirm 拒绝 409 用 `OTP_REQUIRED`（新 code）？** 建议 yes。

## 6. 未覆盖（W4b2b 不作为）

- G10 POST /approvals/{id}/otp（OTP 发送 → W4b2c）
- 红级 confirm 的真实 OTP 校验（→ W4b2c）
- G8 执行（→ W4c）
- G5 GET /plans/{id}（plan 状态查询，W3b/W4c）
- 通知（W5）
- 演示/评测接口（W5）