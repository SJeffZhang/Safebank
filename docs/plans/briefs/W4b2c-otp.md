# W4b2c OTP Send & Verify Implementation Plan

> 用户已指定本机 Claude Code CLI / DeepSeek V4 Pro 实现与独立评审、自主推进；
> 协调者只任务/文档/亲验/发布，不编业务代码，不用自身 subagent。

**Goal:** 实现 G10 `POST /approvals/{id}/otp`（发送模拟验证码）、G6 红级 OTP 校验（`ConfirmRequest` 增加 `otp` 字段）、以及 OTP 错误锁定（`PENDING → LOCKED` 审批状态迁移）。替换 W4b2b 的红级 503 哨兵。

**Architecture:** W4b2a 已完成运行时 OTP 密钥快照（`app.state.otp_hmac_key`）；W4b2b 已完成审批端点 G6/G7/G9/G17 与 D0 实例适配。本组件（W4b2c）新增 OTP 生成/加密存储/常量时间校验纯函数模块 + G10 端点 + 红级 G6 confirm 路径。不含执行（G8 是 W4c）；不含演示接口取验证码（属 W5/D 接口 D5，本任务书记为待决）。

**Tech Stack:** 现有 Python 3.12 / FastAPI / SQLAlchemy / `cryptography` / `secrets` + `hmac` 标准库，冻结依赖，不新增包。

**Spec:** PRD §7.10 第 3 项（OTP 6 位随机数、2 分钟有效期、绑定 approval_id + view_hash、最多 3 次发送间隔 30 秒、累计 5 次错误锁定）；§9.5 G6（ConfirmRequest 含 `otp:string?` 字段）、G10（`{request_id,view_hash}` → `{sent:true,expires_at_wall,retry_after_seconds}`）；§7.2（`otp_challenges` 表，PRD 行 972）；§9.8（OTP_INVALID / OTP_EXPIRED / OTP_LOCKED / RATE_LIMITED 错误码）；ADR-006/007/008；W4b1 验收与评审；W4b2a 验收与评审；W4b2b 验收与评审（尤其 D0 实例适配、D6 红级 503、调用方矩阵）。

## Global Constraints / ownership

**只写：**
- `services/gateway/app/approval/otp.py`（新文件：纯函数 OTP 生成、AEAD 加密/解密、HMAC code_digest、常量时间校验、频控/锁定判定）
- `services/gateway/app/api/approvals.py`（替换 G10 stub + 修改 G6：ConfirmRequest 加 `otp` 字段、红级路径加 OTP 校验）
- `tests/gateway/w4b2c/`（新目录：纯函数测试 + `db/` 子目录真实 PG 测试）
- `tests/gateway/w4b2c/conftest.py`
- `docs/progress/W4b2c-otp.md`

**不写**（冻结）：
- `approval/service.py`、`approval/planview.py`、`approval/adapter.py`、`approval/__init__.py`
- `state.py`、`tables.py`、`migrations/**`、`runtime.py`、`config.py`
- `auth/matrix.py`（`/approvals/{id}/otp` 已注册 `F_APPROVE`；确认矩阵条目未变）
- `schemas/**`（ConfirmRequest 增加字段是 api/approvals.py 内联 model 的局部修改，不改 shared schemas）
- `contracts/**`、`bankcore/**`、`bank-backend/**`
- `Makefile` 末尾只追加 `test-w4b2c` 目标；`.github/workflows/checks.yml` 仅 **coordinator** 追加 CI 一行，不在此 worker 范围
- `compose`、`scripts/keys.py`、`PRD`、`AGENTS`、依赖

**不读密钥文件、不读 `.env`/`.secrets`、不启动全栈/长期卷/Docker、不 push/PR/merge。** 真实 PG 仅已有独立 tmpfs fixture。

## Design decisions

### 调用方矩阵（不变）

| 接口 | 矩阵条目 | 说明 |
|---|---|---|
| G10 `POST /approvals/{id}/otp` | `F_APPROVE` | 只有 F+app approve scope 可请求发送 OTP |
| G6 `POST /approvals/{id}/confirm` | `F_APPROVE` | 已有；新增 otp 字段用于红级校验 |

**L1 A+assistant 绝不能取码/确认**——矩阵已保证，无 A_ASSISTANT 条目。
**D 演示控制台**通过独立接口 D5 `GET /demo/sms?approval_id=` 读验证码（属 W5，本任务书记为待决，不做）。

### OTP 密钥派生（域分离）

`otp_hmac_key`（32+ bytes）已由 W4b2a 安装在 `request.app.state.otp_hmac_key`。OTP 模块需要两个用途不同的密钥：

- **code_digest key**：`HKDF-SHA256(ikm=otp_hmac_key, salt=None, info=b"safebank-otp-code-digest")` → 32 bytes
- **AEAD encryption key**：`HKDF-SHA256(ikm=otp_hmac_key, salt=None, info=b"safebank-otp-ciphertext")` → 32 bytes

两端点从 `request.app.state.otp_hmac_key` 取原密钥后在调用 OTP 模块时派生，不把派生密钥缓存为全局状态。D0 原则：两条 app 不同密钥 → 各自派生不同 → 不可互认。

### OTP 生成

```python
import secrets

def generate_otp_code() -> str:
    """6-digit zero-padded code using secrets.randbelow (cryptographically secure)."""
    return f"{secrets.randbelow(1_000_000):06d}"
```

### AEAD 加密存储（code_ciphertext）

按 D5 模拟短信要求，验证码以 AEAD 密文持久化，供 W5/D5 演示控制台解密展示。明文不进 A/F 响应、日志、审计。

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_OTP_CIPHERTEXT_TTL = 300  # 5 minutes, long enough for demo console to read

def encrypt_otp_code(
    code: str, approval_id: str, view_hash: str, wall_now_ts: int, key: bytes
) -> str:
    """AES-256-GCM. 96-bit random nonce prepended. AAD binds approval/view/time."""
    nonce = secrets.token_bytes(12)
    aad = _otp_aad(approval_id, view_hash, wall_now_ts).encode("utf-8")
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, code.encode("utf-8"), aad)
    return (nonce + ciphertext).hex()

def decrypt_otp_code(
    ciphertext_hex: str, approval_id: str, view_hash: str, wall_now_ts: int, key: bytes
) -> str | None:
    """Return plaintext code or None on wrong nonce/AAD/tag."""
    raw = bytes.fromhex(ciphertext_hex)
    nonce, ct = raw[:12], raw[12:]
    aad = _otp_aad(approval_id, view_hash, wall_now_ts).encode("utf-8")
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ct, aad).decode("utf-8")
    except Exception:
        return None

def _otp_aad(approval_id: str, view_hash: str, wall_now_ts: int) -> str:
    """Canonical AAD string binding the challenge to its approval and time."""
    # Stable key-sorted JSON; no nonces or secrets in AAD
    return json.dumps(
        {"approval_id": approval_id, "view_hash": view_hash, "wall_now_ts": wall_now_ts},
        sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
```

`encrypt_otp_code` 由 G10 handler 在写 `otp_challenges` 行时调用；`decrypt_otp_code` 仅供 D5 演示控制台（W5）使用，本组件只写不读密文。

### code_digest（带密钥 HMAC）

```python
import hmac, hashlib

def compute_code_digest(code: str, approval_id: str, key: bytes) -> str:
    """HMAC-SHA256 over code || approval_id."""
    payload = f"{code}||{approval_id}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()
```

### 常量时间 OTP 校验

```python
def verify_otp(challenge: dict, submitted_code: str, approval_id: str, key: bytes) -> bool:
    """Constant-time: recompute HMAC digest and compare. Never trust stored digest."""
    expected = compute_code_digest(submitted_code, approval_id, key)
    return hmac.compare_digest(challenge["code_digest"], expected)
```

不使用数据库中的 code_ciphertext 列做校验（那是给 D5 读的）；只用 code_digest。

### 频控与锁定

- **发送频控**：同审批最多发送 3 次；`sent_count >= 3` 时返回 429 `RATE_LIMITED`，`retry_after_seconds` 不适用（已耗尽）。
- **发送间隔**：距上次发送 >= 30 秒；否则返回 429 `RATE_LIMITED`，`retry_after_seconds = 30 - (now - last_sent_at_wall).seconds`。
- **错误锁定**：累计 5 次错误（`attempt_count >= 5`）→ PENDING 审批迁移为 LOCKED。此后该审批不可确认、不可 G9 读取（已有 OTP_LOCKED 拒绝）、不可再试 OTP。
- **OTP 过期**：2 分钟（`expires_at_wall`），过期后校验返回 `OTP_EXPIRED`。用户可重新请求 G10 发送（次数允许时作废旧码）。

### 状态迁移

| 触发 | 审批迁移 | plan 迁移 |
|---|---|---|
| `attempt_count >= 5`（G6 红级校验失败） | PENDING → LOCKED（经 `state.transition`） | 不变（plan 保持 PENDING_APPROVAL） |
| G10 重发 OTP | 不变（旧的 code_digest / ciphertext / expires_at_wall 被同事务覆盖） | 不变 |

`PENDING → LOCKED` 已在 `APPROVAL_TRANSITIONS` 中（`PENDING: {..., LOCKED}`，line 88-95 of state.py）。
`LOCKED` 审批不可确认（G6 检查 status != PENDING → 409 STATE_CHANGED）、不可 G9 读取（已有 `OTP_LOCKED` 拒绝）、不可 G10 重发（检查 status != PENDING → 409）。

### 错误码

全部来自 `safebank_contracts.codes.Code`：

| 场景 | code | HTTP |
|---|---|---|
| OTP 码错误 | `OTP_INVALID` | 409 |
| OTP 已过期 | `OTP_EXPIRED` | 409 |
| OTP 错误次数到达上限，审批已锁定 | `OTP_LOCKED` | 409 |
| 发送频控（间隔不足或次数耗尽） | `RATE_LIMITED` | 429 |
| 审批不是 PENDING | `STATE_CHANGED` | 409 |
| approval 不存在或属于其他客户 | `OWNERSHIP_MISMATCH` / `NOT_FOUND` | 404 |
| 审批已过期 | `APPROVAL_EXPIRED` | 409 |
| view_hash 不匹配 | `VIEW_HASH_MISMATCH` | 409 |

**不新增 code**——现有的全部够用。

### G10 端点（替换 stub）

```
POST /approvals/{approval_id}/otp
请求: F+app approve scope
body: {request_id, view_hash}
返回: {sent: true, expires_at_wall, retry_after_seconds: null}
```

处理流程（在 `request_idempotent` 包裹的短事务内）：
1. `_lock_approval(conn, approval_id, customer_id)` — 校验归属
2. 校验 status = PENDING
3. 校验未过期（expires_at_wall > now）
4. 校验 view_hash 匹配（逐字节）
5. 读取已有 `otp_challenges` 行（FOR UPDATE）
6. 频控检查：`sent_count >= 3` → 429 RATE_LIMITED；`last_sent_at_wall + 30s > now` → 429 RATE_LIMITED（带 retry_after）
7. 生成 6 位 code → 计算 `code_digest` → AEAD 加密得 `code_ciphertext`
8. 写入/更新 `otp_challenges` 行（`on_conflict_do_update`）：code_digest、code_ciphertext、view_hash、expires_at_wall=now+2min、sent_count+1、last_sent_at_wall=now；attempt_count 清零（新码重置错误计数）
9. 幂等重发（同 request_id）返回相同 `{sent:true, expires_at_wall, retry_after_seconds:null}`（不重新生成 code、不计费）

**不返验证码**——明文只在 D5 演示控制台可见。

### G6 红级路径修改

`ConfirmRequest` 增加 `otp: str | None = None` 字段。

在 `_validate_confirmable` 之后、`risk_ack` 检查之前，新增红级分支：

```
if risk == "RED":
    - 读取 otp_challenges 行（FOR UPDATE，与 approval 锁在同一事务内）
    - 若行不存在或 expires_at_wall <= now → 409 OTP_EXPIRED
    - 若 attempt_count >= 5 → 迁移 PENDING→LOCKED + 409 OTP_LOCKED
    - 若 body.otp 为空 → 409 OTP_REQUIRED（保留此语义：要求提供 OTP）
    - 调用 verify_otp(...) → 常量时间校验
    - 若失败：increment attempt_count
      - 若新 attempt_count >= 5 → 同时迁移 PENDING→LOCKED + 写 locked_at_wall=now
      - 返回 409 OTP_INVALID（details 中含 remaining_attempts）
    - 若成功：清除 otp_challenges 行（DELETE，已消费不可重用）
    - 继续执行后续的 risk_ack / transition / audit 路径（与黄级相同）
```

注意：OTP 校验成功后与黄级走**完全相同的** confirm 路径（`state.transition` PENDING→CONFIRMED + plan transition）。红级的 `otp_required` 在 view_json 中的展示只是 UI 提示，不影响后端确认逻辑。

### 新列/迁移

**不需要新迁移**。`otp_challenges` 表（tables.py:215–228）已包含所有必需列：
- `attempt_count`（INT NOT NULL, default 0）
- `sent_count`（INT NOT NULL, default 0）
- `last_sent_at_wall`（TIMESTAMPTZ nullable）
- `locked_at_wall`（TIMESTAMPTZ nullable）
- `code_ciphertext`（TEXT nullable，已由 0002_wave1 添加）
- `code_digest`、`view_hash`、`expires_at_wall`（均已存在）

所有列在 0001 建表、0002 补 ciphertext，无需 0003。

### 真实 PG vs 纯函数

- **纯函数**（内存 test）：OTP 生成格式、HKDF 派生确定性、HMAC code_digest 确定性、常量时间校验正确/错误分支、AAD 绑定域、密文加解密往返、频控边界计算（间隔、次数）、锁定判定（attempt_count 阈值）
- **真实 PG（tmpfs fixture）**：G10 全链路（审批归属/状态/过期/view_hash 校验 + 频控 + 发送/重发/覆盖旧码 + 幂等重放）、G6 红级全链路（OTP 校验成功 → CONFIRMED、错误递增 → 第 5 次 LOCKED、过期拒绝、缺失拒绝、校验成功后原 OTP DELETE、幂等重放）、LOCKED 后 G6/G9/G10 均拒绝、G10 与 G6 的 request_idempotent 与审计

不 mock `state.transition` 或 `audit.append_tx`。

### 审计

G10 成功后，通过 `audit.append_tx` 写入 `APPROVAL_OTP_SENT` 事件（payload 含 `approval_id`、`view_hash`，不含 code 或 code_ciphertext）。**密文不进审计。**

G6 红级确认成功后的审计事件仍为 `APPROVAL_CONFIRMED`（与黄级同类型），payload 增加 `otp_verified: true`。

## Interfaces / fixed choices

### ConfirmRequest（修改）

```python
class ConfirmRequest(ConfirmRejectRequest):
    risk_ack: bool = False
    otp: str | None = None  # W4b2c: required for red-level confirm
```

### G10 Response

```python
class OtpSendResponse(BaseModel):
    sent: bool  # always true
    expires_at_wall: str  # RFC3339, now + 2 min
    retry_after_seconds: int | None  # null when sent successfully
```

### otp.py 模块签名

```python
# -- Key derivation --
def derive_otp_keys(master_key: bytes) -> tuple[bytes, bytes]:
    """Return (code_digest_key, aead_key). Deterministic HKDF-SHA256."""

# -- Code generation --
def generate_otp_code() -> str:
    """6-digit zero-padded string."""

# -- Code digest --
def compute_code_digest(code: str, approval_id: str, key: bytes) -> str:
    """HMAC-SHA256(code || approval_id)."""

# -- AEAD --
def encrypt_otp_code(code: str, approval_id: str, view_hash: str, wall_now_ts: int, key: bytes) -> str:
    """Return hex-encoded nonce+ciphertext (AES-256-GCM)."""

def decrypt_otp_code(ciphertext_hex: str, approval_id: str, view_hash: str, wall_now_ts: int, key: bytes) -> str | None:
    """Return plaintext or None. For D5 demo console only."""

# -- Verification --
def verify_otp(challenge: Mapping, submitted_code: str, approval_id: str, key: bytes) -> bool:
    """Constant-time comparison via hmac.compare_digest."""

# -- Rate limit helpers --
def otp_retry_after(last_sent_at_wall: datetime | None, sent_count: int, now: datetime) -> int | None:
    """None = can send. int = seconds to wait. Raises if sent_count >= 3 (exhausted)."""

def otp_attempts_exceeded(attempt_count: int) -> bool:
    """True when attempt_count >= 5."""
```

## Task checklist（先红后绿）

### Task 1: 纯函数模块 otp.py

Files: `approval/otp.py`、`tests/gateway/w4b2c/test_otp_module.py`

- [ ] 红测：`generate_otp_code` 返回 6 位数字字符串、1000 次调用均匀分布、零填充正确
- [ ] 红测：`derive_otp_keys` 两输出互不相同、确定性（同输入同输出）、不同 master key 输出不同
- [ ] 红测：`compute_code_digest` 确定性、不同 code/approval_id/key 产生不同摘要
- [ ] 红测：`encrypt/decrypt` 往返一致；错误 AAD/错误 key/错误的 nonce bit-flip → None；不同 approval_id → 解密失败
- [ ] 红测：`verify_otp` 正确 code → True；错误 code → False；常量时间（基本断言：same-length wrong code 与 right code 走同一代码路径）
- [ ] 红测：`otp_retry_after` — sent_count=0 无限制；last_sent=20s ago 限制 10s；last_sent=40s ago 无限制；sent_count=3 → ValueError（调用方转为 RATE_LIMITED）
- [ ] 红测：`otp_attempts_exceeded` — 4 → False、5 → True
- [ ] `.venv/bin/pytest tests/gateway/w4b2c/test_otp_module.py -q` 确认全红
- [ ] 实现 → 绿 → 本地 commit

### Task 2: G10 端点（真实 PG）

Files: `api/approvals.py`（G10）、`tests/gateway/w4b2c/db/conftest.py`、`tests/gateway/w4b2c/db/test_g10_send.py`

- [ ] 红测：G10 成功发送 → 200 `{sent:true, expires_at_wall, retry_after_seconds:null}`；otp_challenges 行存在且 code_digest 非空、sent_count=1
- [ ] 红测：G10 同 request_id 重放 → 幂等返回相同结果、sent_count 不增加、code_digest 不变
- [ ] 红测：G10 无凭证/仅 app scope → 401/403
- [ ] 红测：G10 approval 不存在/属其他客户 → 404
- [ ] 红测：G10 approval status 非 PENDING（已 CONFIRMED/REJECTED/EXPIRED/LOCKED）→ 409
- [ ] 红测：G10 view_hash 不匹配 → 409 VIEW_HASH_MISMATCH
- [ ] 红测：G10 审批过期 → 409 APPROVAL_EXPIRED
- [ ] 红测：G10 会话撤销 → 409 AUTHORIZATION_CANCELLED
- [ ] 红测：G10 3 次发送 → 第 3 次成功、第 4 次 429 RATE_LIMITED
- [ ] 红测：G10 30 秒内重发 → 429 RATE_LIMITED + response body `retry_after_seconds` 正整数
- [ ] 红测：G10 重发作废旧码 → 同 approval 两次 G10，不同 code_digest / ciphertext / expires_at_wall，attempt_count 清零
- [ ] 红测：G10 成功后审计行 `APPROVAL_OTP_SENT` 存在
- [ ] 实现 G10 handler（替换 stub）→ 绿 → 本地 commit

### Task 3: G6 红级 OTP 校验（真实 PG）

Files: `api/approvals.py`（ConfirmRequest + G6 红级分支）、`tests/gateway/w4b2c/db/test_g6_red_otp.py`

- [ ] 红测：红级 + 正确 OTP → 200 CONFIRMED；otp_challenges 行被 DELETE；审计 `APPROVAL_CONFIRMED`（含 `otp_verified:true`）
- [ ] 红测：红级 + 错误 OTP → 409 OTP_INVALID；attempt_count 递增 1；approval 仍 PENDING
- [ ] 红测：红级 + 第 5 次错误 OTP → 409 OTP_LOCKED；approval PENDING→LOCKED + locked_at_wall 非空；plan 仍 PENDING_APPROVAL 不变
- [ ] 红测：红级 + OTP 已过期（expires_at_wall ≤ now）→ 409 OTP_EXPIRED
- [ ] 红测：红级 + 无 otp_challenges 行（从未发送）→ 409 OTP_EXPIRED
- [ ] 红测：红级 + body.otp=null → 409 (行为同：先查 challenge → 存在而有 code → 不满足 → 但 OTP 为空应明确拒绝；待协调者裁定是 `OTP_REQUIRED` 还是 `OTP_INVALID`)
- [ ] 红测：红级 + 正确 OTP 但 risk_ack_required 且 risk_ack=false → 409 RISK_ACK_REQUIRED（红级的 risk_ack 逻辑与黄级相同）
- [ ] 红测：LOCKED 审批 → G6 409 STATE_CHANGED（非 OTP_LOCKED，因 _validate_confirmable 先检查 status=PENDING）
- [ ] 红测：红级 OTP 确认幂等重放 → 同 request_id 返回原 CONFIRMED 结果
- [ ] 红测：红级 OTP 确认后不同 request_id 再次 confirm → 409（已 CONFIRMED）
- [ ] 实现 G6 红级分支 + ConfirmRequest.otp → 绿 → 本地 commit

### Task 4: backed-by-existing 回归

- [ ] W4b2b 全部黄级测试（test_confirm.py）不受 OTP 字段默认值 `None` 影响：`risk_ack=false` 当 `risk_ack_required=false` 仍成功；新字段对黄级路径无副作用
- [ ] W4b2b 红级 503 测试（test_confirm_red_returns_503）须修改：正向改为 OTP 校验失败（因没有预存 challenge 得 OTP_EXPIRED），或保留作为「无 otp_challenges 行 → OTP_EXPIRED」测试
- [ ] `make test-w4b2b` 全部 57 项仍通过（含修改后的红级测试）

### Task 5: Acceptance

- [ ] `make setup` / `make lint` / `make test` / `make test-gateway` / `make test-gateway-db` / `make test-w4b2c` / `make test-w4b2b` / `make test-w4b1` / `make test-w1` / `make test-w2` / `make test-w3a` / `make test-obligations` / `make test-shared-runtime` / `make contracts-check` 全跑
- [ ] **新 `make test-w4b2c` 目标**：`GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w4b2c -q`，零 skip/xfail
- [ ] CI 需求说明（由协调者在发布时向 `checks.yml` 的 p1-postgres 追加）：
  ```
  - run: make test-w4b2c
  ```
  只在已有 job 末尾追加一行，不改 job 名。
- [ ] 进度写实际红绿/PG 与纯函数边界、未实现 D5 演示取码/W5/执行/通知、后续 W3b→W4c
- [ ] 本地 commit 署名 DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>，冻结停止，待协调者亲验/独审

## 待协调者裁定

1. **红级 + otp=null**：409 用 `OTP_REQUIRED`（当前 codes 无此枚举）还是 `OTP_INVALID`？PRD §9.5 G6 的 body schema 已含 `otp:string?`，故 null 是合法输入；建议用 `OTP_EXPIRED`（因为"无有效挑战"最接近）。D6 裁定禁止新增错误码——但 `OTP_EXPIRED`、`OTP_INVALID` 都已存在，不需要新增。
2. **D5 /demo/sms 取验证码**：属 W5/D 接口（PRD §9.7 D5），本组件只持久化 `code_ciphertext` 供演示控制台解密，不做 D5 端点。`decrypt_otp_code` 纯函数已导出可直接用。

自审：本组件不创建新表/列/错误码/全局状态；所有写路径经 `state.transition`；OTP 明文不进 F/A 响应、日志或审计；将 W4b2b 的红级 503 哨兵替换为真实 OTP 校验。
## 协调者裁定（2026-10-24，与上文冲突时以本节为准）

- **R0 参数以 PRD §7.10 为准**：6 位验证码，用 `secrets` 生成；有效期 2 分钟；绑定 approval_id 与 view_hash；同一审批最多发送 3 次；两次发送至少间隔 30 秒，间隔不足返回 429 `RATE_LIMITED` 并带 `retry_after_seconds`；重发后前一个码立即作废；**错误次数按审批累计，累计 5 次就把该审批锁为 LOCKED**，此后 G10 与 G6 都返回 409 `OTP_LOCKED`。阈值放进配置或 YAML，不得写死在散落的多处。
- **R1 否决设计稿「重发时 attempt_count 清零」**：清零后可以靠重发无限次猜码，违反 PRD「累计 5 次错误锁定」。错误次数跨重发累计；超过第 3 次发送返回 `RATE_LIMITED`（如 PRD 或代码表另有更准确的现有码，以现有码为准，并写进进度），且 DB 不变。必须有红测证明：重发之后累计错误仍会触发锁定。
- **R2 缺 OTP / 无挑战**：红级审批 G6 不带 otp，或挑战已作废、已过期时返回 409 `OTP_EXPIRED`；有有效挑战但码不对时返回 409 `OTP_INVALID`，并累计错误次数。两种情况 DB 中只允许错误计数变化，approval/plan 状态不变。不新增错误码。
- **R3 W4b2b 的 `test_confirm_red_returns_503`**：改为两条测试：未发码时确认红级返回 `OTP_EXPIRED` 且 DB 不变；发码后带正确码确认成功，状态变为 CONFIRMED，确认后不执行。不得删除红级拒绝路径的覆盖。
- **R4 实例密钥**：在 `approval/adapter.py` 新增 `otp_hmac_key(request)`，与审批密钥同一模式，端点只通过它取 OTP 密钥。D0 红测要扩展到 OTP：两个 app 用不同 OTP 密钥，各自发出的码只能在本 app 校验通过；adapter 改为返回固定全局值时，测试必须失败。
- **R5 加密与比较**：HKDF 按用途派生出两把独立子密钥（digest 与 ciphertext 域分离）；AEAD 的 AAD 包含 approval_id、plan_id、view_hash、challenge id；比较使用 `hmac.compare_digest`。明文只在生成那一刻存在于内存，不进入任何响应、日志、审计、异常信息或 repr。并发确认和错误计数都要对 otp_challenges/approvals 行加锁更新，同时遵守 W4b2b 的 sessions→plan→evaluation 锁序，不得新增锁序反转。
- **R6 不含**：演示/D 接口读取验证码、G8 执行；遇到需要的地方写进待决。不新增迁移；如确实缺列，停下写待决，不要自行设计迁移。
