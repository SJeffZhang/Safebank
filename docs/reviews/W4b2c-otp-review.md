# W4b2c OTP — 独立评审报告

**日期**: 2026-10-25
**范围**: `git diff 72a4161 96ba734`（不含 AGENTS.md revert）
**方法**: 只读静态分析 + 纯函数 test_otp_module.py 实测（30 passed）

---

## 1. 参数核对（R0）

| 要求 | 实现 | 位置 |
|---|---|---|
| 6 位随机 | `f"{secrets.randbelow(1_000_000):06d}"` ✓ | otp.py:64 |
| 2 分有效 | `OTP_TTL = timedelta(minutes=2)` ✓ | otp.py:28 |
| 绑定 approval_id+view_hash | G10 逐字节比较 `view_hash`；code_digest 含 `approval_id` ✓ | approvals.py:693, otp.py:75 |
| 最多 3 次发送 | `OTP_MAX_SENDS = 3`；≥3 时 `otp_retry_after` 抛出 ValueError → 429 ✓ | otp.py:30,194–195, approvals.py:728–735 |
| 30 秒间隔 | `OTP_SEND_INTERVAL = timedelta(seconds=30)`；不足返回 429 带 `details.retry_after_seconds` ✓ | otp.py:31, approvals.py:736–741 |
| **累计 5 次锁定（R1）** | `prev_attempt = existing["attempt_count"]...` 不清零；`OTP_MAX_ATTEMPTS = 5` ✓ | approvals.py:761, otp.py:32 |

### R1 红测核验

协调者描述「改为 `prev_attempt=0` 后 test-w4b2c 1 failed」——代码中 `prev_attempt` 恰在第 761 行取自已有 challenge 的 `attempt_count`（未重置）。静态分析确认：若强行改为 `prev_attempt = 0`，`test_g10_resend_overwrites_code_but_keeps_attempt_count` 中断言 `challenge["attempt_count"] == 2`（test_g10_send.py:431）将失败。逻辑自洽，R1 满足。

---

## 2. 错误码核对（R2）

| 场景 | 代码 | 现实 | 位置 |
|---|---|---|---|
| 无挑战 / 过期 | `Code.OTP_EXPIRED` | ✓ | approvals.py:324 |
| otp 为空 | `Code.OTP_EXPIRED`（R2 裁定） | ✓ | approvals.py:332 |
| 已有 challenge 未过期 + otp=null | `Code.OTP_EXPIRED` | ✓（语义折中但裁定如此） | approvals.py:332 |
| 码错 → 错误计数 | `Code.OTP_INVALID` + attempt_count++ | ✓ | approvals.py:370–386 |
| 第 5 次错 → 锁定 | `Code.OTP_LOCKED` + transition PENDING→LOCKED | ✓ | approvals.py:350–368 |
| 审批非 PENDING | `Code.STATE_CHANGED` | ✓ | approvals.py:685 |
| 已过期审批 | `Code.APPROVAL_EXPIRED` | ✓ | approvals.py:689 |
| view_hash 不匹配 | `Code.VIEW_HASH_MISMATCH` | ✓ | approvals.py:693 |
| 发送耗尽 | `Code.RATE_LIMITED`（429） | ✓ | approvals.py:731–735 |
| 发送过频 | `Code.RATE_LIMITED`（429，含 retry_after） | ✓ | approvals.py:736–741 |
| LOCKED → G6 | `Code.STATE_CHANGED`（`_validate_confirmable` 先检查 status=PENDING） | ✓ | approvals.py:134 |
| LOCKED → G9 | `Code.OTP_LOCKED` | ✓ | approvals.py:238 |
| LOCKED → G10 | `Code.STATE_CHANGED`（status != PENDING） | ✓ | approvals.py:685 |

错误码全部为 `safebank_contracts.codes.Code` 现有成员（含 OTP_LOCKED line 68），**无新增**。DB 中仅 `attempt_count` 或审批状态变化——approval/plan 不额外被修改。✓

---

## 3. 密钥与加密（R4/R5）

### R4 实例密钥

- `adapter.otp_hmac_key(request)` → `request.app.state.otp_hmac_key`（adapter.py:26–28）✓
- G10 handler 使用 `_adapter_otp_key(request)`（approvals.py:747）✓
- G6 RED 分支使用 `_adapter_otp_key(request)`（approvals.py:335）✓

协调者描述「改为固定全局值后 4 failed」——每条 OTP 操作都经过 adapter，改 adapter 返回固定值即可让 D0 跨 app 测试失败。逻辑自洽。

### R5 HKDF 域分离

- digest key: `HKDF(…info=b"safebank-otp-code-digest")`（otp.py:44–49）✓
- AEAD key: `HKDF(…info=b"safebank-otp-ciphertext")`（otp.py:50–55）✓
- 两 key 不同（test_derive_otp_keys_outputs_differ 验证）✓

### R5 AEAD AAD

`_otp_aad` 含 `approval_id`、`plan_id`、`view_hash`、`wall_now`（otp.py:82–99）✓。错误 AAD（换 approval/plan/view/time）均返回 None（test_decrypt_wrong_* 覆盖）✓。

### R5 compare_digest

`verify_otp` 使用 `hmac.compare_digest`（otp.py:174）✓。重新计算 digest 后再比较，不信任存储值 ✓。

### R5 明文不入响应/日志/审计/异常

- **G10 响应**: `OtpSendResponse(sent=True, expires_at_wall, retry_after_seconds=None)` — 无 code ✓
- **G6 响应**: `{approval_id, plan_id, status: "CONFIRMED"}` — 无 code ✓
- **G10 审计**: `{approval_id, view_hash, expires_at_wall}` — 无 code/ciphertext ✓
- **G6 审计**: `{..., otp_verified: true}` — 无 code ✓
- **错误消息**: `"OTP challenge not found or expired"`、`"OTP attempts exhausted"`、`"OTP code required"`（其中的 "code" 是类别名非具体码值）✓
- **异常链**: 所有 GatewayError 只传 code 枚举和固定消息 ✓
- `decrypt_otp_code` 仅导出供 D5 使用（W5），gateway 正常流程不调用 ✓

测试 `test_g10_audit_event`（test_g10_send.py:474–475）检查 `"code" not in payload_str.lower()`，但**未检查 `"ciphertext"`**。审计 payload 含 `approval_id`、`view_hash`、`expires_at_wall`，确实不含 ciphertext，但此为巧合（payload 不由 ciphertext 字段组成），无专门断言。P3 级。

---

## 4. 并发与锁

### 锁序分析

**G10 锁序**: approvals → sessions → otp_challenges（均 FOR UPDATE）
**G6 RED 锁序**: approvals → sessions → plans → evaluations → otp_challenges（**读无锁**）→ approval/plan state.transition

G10 的 approvals→sessions→otp_challenges 与 W4b2b 的 approvals→sessions→plans→evaluations 起始一致（approvals→sessions），**未见新锁序反转**。

### P2-1：G6 读 otp_challenges 无 FOR UPDATE

`approvals.py:312-320` — `select(t.otp_challenges)...` **无 `.with_for_update()`**。注释称「approval lock 提供 serialisation」。分析确认：同一 approval 的并发 G6 被 approval row lock 串行化，G10 也先持 approval lock。**实际功能安全**，但与 R5 原文「都要对 otp_challenges/approvals 行加锁更新」不一致。P2。

### 并发双确认：已串行化

同一 approval 的两个 G6 请求经 `_lock_approval` 的 `with_for_update()` 串行化，第二个会看到 status 非 PENDING → STATE_CHANGED。✓

### 并发 G10+G6：已串行化

两方都先锁 approvals，经同锁串行化。✓

### 与 `_supersede_session` 的 ABBA

已知 W4b2b P2-4（`docs/reviews/W4b2b-approval-endpoints-review.md:119-124`）：G6/G7 锁序 approvals→sessions→plans 与 `_supersede_session` 的 sessions→plans→approval(state.transition) 形成 ABBA。**本组件未恶化此问题**，G10 不调用 `state.transition` 且不锁 plans→evaluations。PostgreSQL 死锁检测兜底。

---

## 5. 调用方矩阵

矩阵条目（`auth/matrix.py`）未改动。G10 `/approvals/{id}/otp` 仍只为 `F_APPROVE`。A+assistant 令牌无此条目 → 403。✓

---

## 6. R3: W4b2b 红级 503 重写

| 旧测试 | 新测试 | 状态 |
|---|---|---|
| `test_confirm_red_returns_503`（断言 503） | `test_confirm_red_no_challenge_returns_otp_expired`（断言 409，无 challenge → OTP_EXPIRED） | ✓ |
| （无） | `test_confirm_red_with_otp_succeeds`（预存 challenge + 正确 OTP → 200 CONFIRMED） | ✓ |

拒绝覆盖未丢失：无 challenge 场景从 503 变为 409 OTP_EXPIRED，DB 不变断言保留。✓

---

## 7. 未新增迁移 / contracts 未改

- `migrations/` 无变动 ✓
- `packages/contracts/` 无变动 ✓
- `make test-w4b2c` 已入 CI（checks.yml:37） ✓
- ConfirmRequest.otp 为局部 Pydantic model 字段，非 shared schema ✓

---

## 8. 测试质量

| 套件 | 数量 | 类型 | 断言 |
|---|---|---|---|
| test_otp_module.py | 30 | 纯函数 | 具体（长度、确定性、往返、bit-flip、分布） ✓ |
| test_g10_send.py | 15 | 真实 PG | 状态码、DB 状态、审计 payload、sent_count/attempt_count 精确值 ✓ |
| test_g6_red_otp.py | 9 | 真实 PG | 审批/plan 状态、challenge 行存在/删除、attempt_count 递增、locked_at_wall 非空 ✓ |
| test_d0_cross_app_otp.py | 2 | 真实 PG | app A 确认 200、app B 确认 409 + attempt_count 递增 ✓ |
| test_confirm.py（W4b2b） | 2 新增 | 真实 PG | RED 无 challenge 409 + RED 正确 OTP 200 ✓ |

纯函数 30 项实测通过。真实 PG 26 项待协调者已亲验（本次未跑 DB）。断言具体、DB 状态逐列验证、跨度全面。✓

### P2-3：test_g10_within_30s_interval_returns_429 未断言 retry_after_seconds

`test_g10_send.py:372` 只检查 `assert r2.status_code == 429`，不检查 `retry_after_seconds` 是否在响应中且为正整数。对比 `test_g10_sent_count_exhausted_returns_429` 也不检查 retry_after。P2。

---

## 9. 发现

### P1-1：G10 审计 event 以 approval_id 冒充 plan_id

- **Files**: `services/gateway/app/api/approvals.py:820`
- **Evidence**: `plan_id=approval_id` 将 `apv_1` 传入 `append_tx` 的 `plan_id` 参数，实际应传 `approval["plan_id"]`（已在同函数 line 753 正确使用）。line 206 的 G6 confirm audit 传 `plan_id=approval["plan_id"]`，line 753 的加密调用也传 `plan_id=approval["plan_id"]`。
- **Failure scenario**: 查询某 plan 的审计事件时，APPROVAL_OTP_SENT 无法关联到正确 plan。审计元数据损坏。
- **Fix**: 将 `plan_id=approval_id` 改为 `plan_id=approval["plan_id"]`。

### P2-1：G6 RED 路径读 otp_challenges 未 FOR UPDATE

- **Files**: `services/gateway/app/api/approvals.py:312-320`
- **Evidence**: `select(t.otp_challenges)` 未加 `.with_for_update()`，仅注释解释 approval lock 提供串行化。R5 要求「都要对 otp_challenges/approvals 行加锁更新」。
- **Failure scenario**: 第三方代码路径（非当前 G6/G10）若直接锁 otp_challenges 而不持 approval lock，可能形成锁序反转。
- **Severity**: P2（当前流量路径下安全，但不满足 R5 防范性要求）。

### P2-2：replayed error 路径 `Code("UNKNOWN")` 可能 ValueError

- **Files**: `services/gateway/app/api/approvals.py:468`
- **Evidence**: `Code(response.get("code", "UNKNOWN"))`——若缓存响应缺失 `code` 字段，`Code("UNKNOWN")` 将抛 `ValueError`（`Code` 无 UNKNOWN 成员）。理论上 reachable（若缓存数据损坏或未来添加的 error path 漏写 code）。
- **Failure scenario**: 幂等重放损坏的 error 响应 → ValueError 而非 GatewayError → 500。
- **Fix**: 改用 `Code(response.get("code", Code.CONTRACT_INVALID.value))`，或与 line 459 风格一致的 `getattr(Code, response.get("code", ""), Code.CONTRACT_INVALID)`。

### P2-3：test_g10_within_30s_interval_returns_429 未断言 retry_after_seconds

- **Files**: `tests/gateway/w4b2c/db/test_g10_send.py:372`
- **Evidence**: 只断言 `assert r2.status_code == 429`，未解析 JSON 验证 `retry_after_seconds > 0`。
- **Fix**: 增加 `data = r2.json(); assert data.get("retry_after_seconds", 0) > 0`。

### P2-4：test_g10_audit_event 仅检查 "code" 不含 payload，未显式排除 "ciphertext"

- **Files**: `tests/gateway/w4b2c/db/test_g10_send.py:474-475`
- **Evidence**: `assert "code" not in payload_str.lower()` 只对 "code" 子串生效。审计 payload 当前确不含 "ciphertext"，但此为巧合——payload 是 `{approval_id, view_hash, expires_at_wall}`，ciphertext 不在其中。未来若扩展 payload 可能无意泄密。
- **Fix**: 增加 `assert "ciphertext" not in payload_str.lower()`。

---

## 10. 结论

**REQUEST_CHANGES** — P1-1 必须修（审计元数据损坏）。P2 四项建议修。

| 级别 | 编号 | 简述 |
|---|---|---|
| **P1** | P1-1 | G10 审计 `plan_id=approval_id`，应为 `plan_id=approval["plan_id"]`（approvals.py:820） |
| P2 | P2-1 | G6 RED 读 otp_challenges 无 FOR UPDATE，与 R5 不一致（approvals.py:312） |
| P2 | P2-2 | replayed error `Code("UNKNOWN")` may ValueError（approvals.py:468） |
| P2 | P2-3 | 间隔测试未断言 retry_after_seconds 值（test_g10_send.py:372） |
| P2 | P2-4 | 审计测试未显式排除 "ciphertext"（test_g10_send.py:474） |

其余 R0–R6 全部满足：参数、错误码、密钥隔离、HKDF 域分离、AEAD AAD 绑定、compare_digest、明文不入响应/日志、并发串行化、调用方矩阵、R3 503 替换、无迁移/contracts 不改、CI 已接入。纯函数 30 项实测通过。

---

## 修复轮 1 定点复审（2026-10-25，96ba734..0c4ed23）

**范围**: `git diff 96ba734 0c4ed23`
**方法**: 只读静态分析，逐条核对 diff 中每个修复

### 逐条复核

| 编号 | 简述 | 位置 | 修复方式 | 状态 |
|---|---|---|---|---|
| **P1-1** | G10 审计 plan_id 误用 | approvals.py:801,822 | `_result["plan_id"] = approval["plan_id"]` 在 produce() 内赋值，audit 调用改用 `plan_id=_result["plan_id"]` | **FIXED** |
| **P2-1** | G6 读 otp_challenges 无 FOR UPDATE | approvals.py:316 | 新增 `.with_for_update()` | **FIXED** |
| **P2-2** | replayed error `Code("UNKNOWN")` | approvals.py:466 | 改为 `getattr(Code, response.get("code", ""), Code.CONTRACT_INVALID)` | **FIXED** |
| **P2-3** | 间隔测试未断言 retry_after | test_g10_send.py:374-375 | `rr = data.get("error", {}).get("details", {}).get("retry_after_seconds"); assert isinstance(rr, int) and 0 < rr <= 30` | **FIXED** |
| **P2-4** | 审计测试未排除 ciphertext | test_g10_send.py:480-481 | `"ciphertext" not in payload_str.lower()` 和 `"digest" not in payload_str.lower()` | **FIXED** |

### P1-1 详细验证

修复在 `approvals.py` 中：
- `line 801`: `_result["plan_id"] = approval["plan_id"]` — 在 `produce()` 闭包内从 approval 行取出正确 plan_id
- `line 822`: `plan_id=_result["plan_id"]` — 审计调用使用 produce 闭包捕获的正确值
- 测试 `test_g10_send.py:473`: `assert evt["plan_id"] == "pln_1"` — 断言 plan_id 是 `"pln_1"` 而非 `"apv_1"`

协调者红绿核验：把 G10 审计 plan_id 改回 `approval_id` 后 test-w4b2c 1 failed，还原后 56 passed。确认修复有效。✓

### P2-1 锁序验证

新增的 `.with_for_update()` 在 `approvals.py:316`，G6 RED 路径完整锁序：

```
approvals (same-tx _lock_approval)
  → sessions (line 154)
  → plans (line 169)
  → evaluations (line 187)
  → otp_challenges (line 316, NEW with_for_update)
  → approval/plan state.transition
```

与 G10 锁序对比：
```
approvals → sessions → otp_challenges
```

两路径都以 approvals → sessions 开头，**无 ABBA**。与已知 W4b2b P2-4（`_supersede_session` 的 sessions→plans→approval vs G6/G7 的 approvals→sessions→plans）也不恶化——新锁在已有 plans→evaluations 之后，不引入额外反转点。✓

### P2-2 错误码安全性验证

`getattr(Code, response.get("code", ""), Code.CONTRACT_INVALID)`:
- 正常路径：缓存响应中 `code` 字段为有效 Code 成员 → `getattr` 返回对应枚举
- 损坏路径：`code` 缺失或为空 → `getattr(Code, "", Code.CONTRACT_INVALID)` → 返回 `Code.CONTRACT_INVALID`
- 无新增枚举值，纯现有 Code 成员，fail-closed ✓

### 新问题检查

- **无新增文件**: diff 仅涉及 `api/approvals.py`（7 行）、`test_g10_send.py`（6 行）、进度日志（1 文件）、本报告（1 文件）
- **锁序无变化**: P2-1 在现有路径末尾加 `.with_for_update()`，不改变锁获取顺序
- **无回归**: 生产代码改动最小（3 处），测试改动仅增强断言，不弱化
- **无新 P0/P1/P2**

### 结论

**APPROVE** — P1-1、P2-1/2/3/4 全部关闭，无回归、无新问题。锁序保持安全，错误码 fallback fail-closed。