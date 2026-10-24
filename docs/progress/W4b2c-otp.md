# W4b2c OTP — 验收记录

**日期**: 2026-10-24 (committed 2026-10-25 00:40 CST)
**分支**: `codex/w4b2c-otp`
**提交**: 64d8f09

## 组件目标

实现 G10 `POST /approvals/{id}/otp`（发送模拟 OTP）、G6 红级 OTP 校验（ConfirmRequest 增加 `otp` 字段）、OTP 错误锁定（PENDING → LOCKED）。

## 关键设计决策

### G6 RED OTP 状态持久化与事务回滚问题

**问题**: `produce()` 在 `request_idempotent(conn, produce=produce)` 内部执行。当 OTP 校验失败时，`produce()` 中的 `attempt_count` 更新和 `PENDING → LOCKED` 状态迁移在同一个 `async with db.begin()` 事务中。如果 `produce()` 抛出异常，`request_idempotent` 的 `except BaseException` 会删除幂等行并重抛，导致外层 `db.begin()` 回滚——所有状态变更丢失。

**修复**: `produce()` 对 OTP 错误**返回**错误响应（而非抛出），让事务正常提交。错误通过 `_result["otp_error"]` dict 传递。`request_idempotent` 完成后，handler 检查 `_result["otp_error"]` 并转换为 `GatewayError` 作为 HTTP 响应。

```python
# produce() returns error for wrong OTP instead of raising
if not _verify_otp(...):
    if new_attempt >= 5:
        await state.transition(conn, ..., to=ApprovalStatus.LOCKED.value)
        ...
        _result["otp_error"] = {"code": "OTP_LOCKED", ...}
        return 409, {"code": "OTP_LOCKED", ...}
    else:
        _result["otp_error"] = {"code": "OTP_INVALID", ...}
        return 409, {"code": "OTP_INVALID", ...}

# After request_idempotent returns:
if not replayed and "otp_error" in _result:
    err = _result["otp_error"]
    raise GatewayError(getattr(Code, err["code"], Code.CONTRACT_INVALID), ...)
```

### D0 跨应用 OTP 密钥隔离

两个 app 实例使用不同的 OTP HMAC 密钥：
- App A (KEY_X): 生成 code→digest，seed challenge，confirm→200
- App B (KEY_Y): 使用相同的 code + challenge→digest 不匹配→409 OTP_INVALID

证明每个 app 实例的 OTP 密钥完全隔离，不可互认。

### Code.UNKNOWN fallback 修复

`Code` 枚举没有 `UNKNOWN` 成员。`getattr(Code, err["code"], Code.UNKNOWN)` 运行时抛 `AttributeError`。修复为 `Code.CONTRACT_INVALID`（通用错误码，且在此路径中实际不会触发——otp_error code 始终为已知值 OTP_INVALID/OTP_LOCKED）。

## 测试覆盖

| 类别 | 数量 | 说明 |
|------|------|------|
| test_otp_module.py | 30 | 纯函数: generate/derive/compute/encrypt/decrypt/verify/retry_after/attempts_exceeded |
| test_g10_send.py | 15 | G10 端点真实PG: 成功/幂等/auth/ownership/status/view_hash/过期/会话撤销/频控/重发/审计 |
| test_g6_red_otp.py | 9 | G6 红级OTP校验: 正确OTP/错误OTP/5次锁定/过期/缺失/空白/空challenge/审计无明文 |
| test_d0_cross_app_otp.py | 2 | D0 跨应用密钥隔离: app A验证成功/app B验证失败 |
| **总计** | **56** | 30 纯函数 + 26 真实PG |

## 退出命令

```bash
make lint          # 通过（316 files formatted）
make test-w4b2c    # 56 passed, 0 failed
make test          # 2533 passed, 638 skipped
make test-gateway  # 2017 passed, 578 skipped
make test-gateway-db # 17 passed
make test-shared-runtime # 79 passed
make test-w1       # 1688 passed
make test-w4b1     # 57 passed
make test-w4b2b    # 58 passed
make test-w2       # 246 passed
make test-w3a      # 45 passed
make test-obligations # 30 passed (1 SAWarning)
make contracts-check  # PASS
tests/agent        # 287 passed
```

## 变更文件

| 文件 | 变更 |
|------|------|
| `services/gateway/app/approval/otp.py` | OTP 纯函数模块 (Task 1, 已在前一个提交) |
| `services/gateway/app/api/approvals.py` | G10 handler + G6 RED OTP 校验 + Code fallback 修复 |
| `services/gateway/app/approval/adapter.py` | `otp_hmac_key(request)` adapter |
| `tests/gateway/w4b2c/test_otp_module.py` | 30 纯函数红→绿测试 |
| `tests/gateway/w4b2c/conftest.py` | 测试 fixtures |
| `tests/gateway/w4b2c/db/conftest.py` | DB 测试 fixtures (seed helpers for RED approval + OTP challenge) |
| `tests/gateway/w4b2c/db/test_g10_send.py` | 15 G10 端点真实PG测试 |
| `tests/gateway/w4b2c/db/test_g6_red_otp.py` | 9 G6 红级OTP真实PG测试 |
| `tests/gateway/w4b2c/db/test_d0_cross_app_otp.py` | 2 D0 跨应用密钥隔离测试 |
| `tests/gateway/w4b2b/db/test_confirm.py` | 已有confirm测试的OTP字段支持 |
| `Makefile` | 新增 `test-w4b2c` 目标 |
| `.github/workflows/checks.yml` | CI 新增 `make test-w4b2c` |

## 未覆盖/待决

- 演示/D 接口取验证码 (D5, 属 W5)
- G8 执行 (属 W4c)
- 真实 L3 闭环 (OTP 只到 confirm 状态迁移，不执行转账)
- actionlint 未在本机运行 (需权限审批)