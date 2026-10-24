# W4b2c OTP 设计笔记：代码事实与缺口

> 只读代码核对，不改代码。2026-10-24。

## 1. 现有 OTP 基础设施

### 数据库表 `otp_challenges`（tables.py:215–228）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `approval_id` | String(256) | PK, FK→approvals.id | OTP 与审批一一对应 |
| `code_digest` | String(128) | NOT NULL | 已存在，用于 HMAC 校验 |
| `code_ciphertext` | Text | nullable | 0002_wave1 添加（migrations/versions/0002_wave1.py:19），D5 模拟短信用 |
| `view_hash` | String(64) | NOT NULL | 绑定 OTP 与面板内容 |
| `expires_at_wall` | timestamptz | NOT NULL | OTP 2 分钟有效期 |
| `attempt_count` | Integer | NOT NULL, default 0 | 错误尝试次数 |
| `sent_count` | Integer | NOT NULL, default 0 | 发送次数（最多 3 次） |
| `last_sent_at_wall` | timestamptz | nullable | 用于 30 秒间隔频控 |
| `locked_at_wall` | timestamptz | nullable | LOCKED 发生时刻 |

**状态**：表齐全，无需迁移。没有索引覆盖 `approval_id` 上的读（但 PK 本身已提供）。

### 错误码（contracts/codes.py:57–69）

| 码 | 值 | 用途 |
|---|---|---|
| `OTP_INVALID` | line 65 | 校验失败 |
| `OTP_EXPIRED` | line 66 | 挑战过期 |
| `OTP_LOCKED` | line 67 | 尝试次数超限 |
| `RATE_LIMITED` | line 68 | G10 发送频控 |

**状态**：齐全。无需新增。

### 审批状态迁移（state.py:87–105）

- `PENDING → LOCKED`：**已在 `APPROVAL_TRANSITIONS` 中**（line 95）
- `LOCKED` 已作为 `ApprovalStatus.LOCKED`（schemas/enums.py:42）
- `LOCKED → {EXPIRED, SUPERSEDED}`：**已定义**（state.py:104）
- 表中 CHECK 约束覆盖全部 `ApprovalStatus` 值

**状态**：迁移边齐备。

### 运行时密钥（runtime.py:128–148）

- `GatewayRuntime.otp_hmac_key: bytes`（`repr=False`，line 138）
- `install(app)` 安装到 `app.state.otp_hmac_key`（line 148）
- `_validate_approval_keys`（line 85–125）校验长度 ≥32、与 approval key/服务令牌不重复

**状态**：密钥资源就绪。

### G10 stub（api/approvals.py:521–524）

```python
@router.post("/approvals/{approval_id}/otp")
async def otp_send(approval_id: str, request: Request):
    """G10 OTP send — lands in W4b2c."""
    raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "G10 otp lands in W4b2c")
```

**状态**：路由已注册在 auth/matrix.py 的 `F_APPROVE` 条目中（已核实矩阵包含 `/approvals/{id}/otp`），只需替换 handler。

### G6 ConfirmRequest（api/approvals.py:51–52）

```python
class ConfirmRequest(ConfirmRejectRequest):
    risk_ack: bool = False
```

**状态**：缺 `otp: str | None = None` 字段。新增后向后兼容（默认 None，黄级路径不受影响）。

### G6 红级拒 503（api/approvals.py:289–293）

```python
if risk == "RED":
    raise GatewayError(
        Code.DEPENDENCY_UNAVAILABLE, 503, "red approval requires OTP (not yet implemented)"
    )
```

**状态**：这是要替换的哨兵。

### adapter.py 缺少 OTP 密钥助手

`approval/adapter.py:16–28` 导出 `approval_hmac_key`、`profile`、`wall_now`，但**没有** `otp_hmac_key` helper。端点可直接通过 `request.app.state.otp_hmac_key` 访问——这是 D0 原则（不依赖全局状态），只需在端点中直接引用。

## 2. 代码缺口

### 2.1 `approval/adapter.py` — 缺 `otp_hmac_key` 导出

**文件**：`services/gateway/app/approval/adapter.py:16–28`
**缺口**：有 `approval_hmac_key(request)` 但无对应的 `otp_hmac_key(request)`。

**建议**：不强制加。端点可以直接读 `request.app.state.otp_hmac_key`，这与 D0 原则一致（适配器只是语法糖，不是唯一入口）。但如果任务书决定加，那就加一行：

```python
def otp_hmac_key(request: Request) -> bytes:
    return request.app.state.otp_hmac_key
```

### 2.2 `otp_challenges` 表 — 无 `last_sent_at_wall` 索引

**文件**：`tables.py:215–228`
**缺口**：`last_sent_at_wall` 没有索引。G10 读已有 challenge 行只通过 PK（approval_id），不涉及范围扫描，**不需要索引**。

### 2.3 OTP 模块不存在

**路径**：`services/gateway/app/approval/otp.py` — **不存在**。
**所需接口**：见任务书。

### 2.4 依赖检查 — `cryptography` 包

`cryptography` 已是项目依赖（用于 Ed25519 JWT 签名，runtime.py:18–21）。AES-GCM 从 `cryptography.hazmat.primitives.ciphers.aead` 导入，无需新包。`secrets`、`hmac`、`hashlib` 是标准库。

## 3. PRD 不闭合点

### 3.1 null otp 时的错误码选择（需裁定）

**PRD §9.5 G6** 定义 `body: {request_id, view_hash, otp: string?, risk_ack: bool}`。`otp` 为可选 → null 是合法请求。红级确认时 otp=null：

- 用 `OTP_INVALID`：不符合"无效"语义（根本没提交）
- 用 `OTP_EXPIRED`：不太符合（挑战可能存在且未过期）
- 没有 `OTP_REQUIRED` 码

**建议**：G6 红级路径先读 `otp_challenges` 行。如果 `body.otp` 为空/none：
- 存在未过期 challenge → 客户应提交 OTP → 返回 409 `OTP_REQUIRED`（需要新增 code，违反 D6）
- 不存在 challenge 或已过期 → 返回 409 `OTP_EXPIRED`（不需要新 code）

替代方案（不新增 code）：otp=null 时一律返回 `OTP_INVALID`（因为解析不到有效 OTP 码）。PRD §9.5 G6 schema 默认`otp` 可选，而调用方在红级 panel（`otp_required:true`）展示时应当知道需要填 OTP；不填是客户端错误。

**协调者裁定默认**：otp=null 时返回 `OTP_INVALID`，不新增 code。

### 3.2 G10 `request_idempotent` 的 route 名称

**PRD §9.1** 规定 `customer_id + route + request_id` 构成幂等键。G10 的 route 字符串应为 `"/approvals/{id}/otp"`。`request_idempotent`（idempotency.py:29–98）要求调用方硬编码 route——G10 handler 中应使用**文字量**，不是 f-string（否则每个 approval_id 是不同的 route）。

**实际**：参考 G6 做法（api/approvals.py:275），handler 内 `route = f"/approvals/{approval_id}/confirm"`。但对 G10，按现有惯例，`route = f"/approvals/{approval_id}/otp"` 也是合理的——每个 approval 的 OTP 幂等是独立的。

等等——再看 idempotency.py：`request_idempotent` 用 `customer_id + route + request_id` 为主键。如果 route 包含具体 approval_id，那不同 approval 就自动在不同的幂等命名空间里了，这是期望的行为。与 G6 和 G7 一致。

### 3.3 G10 重发作废前码 vs attempt_count 重置

**PRD §7.10 第 3 项**："重发作废前码，累计 5 次错误锁定该审批"。

解读："前码"作废——新 code 覆盖旧 code_digest 与 code_ciphertext。`attempt_count` 应**重置为 0**（新码不应继承旧码的错误计数）。任务书已采纳此解读。

### 3.4 LOCKED 后 plan 是否变化

**PRD §7.10 第 3 项**："累计 5 次错误锁定该审批"。

仅审批 LOCKED，plan 保持 PENDING_APPROVAL。符合现有 `APPROVAL_TRANSITIONS`（PENDING → LOCKED 与 plan 状态独立）。

### 3.5 D5 演示控制台取验证码

**PRD §9.7 D5**：`GET /demo/sms?approval_id=` → `{approval_id, code, expires_at_wall}`。

本组件只持久化 `code_ciphertext`，不解密。D5 端点属 W5 范围。任务书已将 `decrypt_otp_code` 导出为纯函数，届时直接调用。

## 4. 建议协调者裁定

1. **null otp: OTP_INVALID vs OTP_EXPIRED vs OTP_REQUIRED** — 默认选 `OTP_INVALID`（不新增 code）。若存在有效的未过期 challenge，`OTP_EXPIRED` 更合适字面但语义偏差（使其与"challenge 不存在/过期"合并）。建议：存在有效 challenge + otp=null → OTP_INVALID（客户端应填但未填）；不存在 challenge → OTP_EXPIRED。

2. **G10 重发时 attempt_count 重置** — 已裁定重置为 0。PRD 原文只说"重发作废前码"，未说重置计数；但新码不应受旧码错误影响——这是合理的设计选择。

3. **W4b2b 红级 503 测试迁移** — `test_confirm_red_returns_503` 需改：测试将改为验证红级 confirm 在没有 OTP challenge 时返回 `OTP_EXPIRED`（409），或在上游先调 G10 发送后验证正确 OTP 可 confirm。不能保留 503 断言。这是**必要修改**，不是回归。

4. **adapter.py 是否加 `otp_hmac_key` helper** — 取决于审美偏好。不加也能正常工作。建议不加，保持 adapter 最小化，端点直接读 `request.app.state`。