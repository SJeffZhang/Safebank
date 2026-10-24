### 修复轮 1 进度日志

**日期**: 2026-10-25
**分支**: `codex/w4b2c-otp`
**提交**: 0924660

#### 修复内容

| 编号 | 级别 | 简述 | 修复 |
|---|---|---|---|
| P1-1 | P1 | G10 审计 `plan_id=approval_id` 以 approval ID 冒充 plan_id | `produce()` 内 `_result["plan_id"] = approval["plan_id"]`，审计块用 `_result["plan_id"]`；测试断言 `evt["plan_id"] == "pln_1"` |
| P2-1 | P2 | G6 RED 读 otp_challenges 无 FOR UPDATE | 添加 `.with_for_update()`，注释更新说明防范性加锁 |
| P2-2 | P2 | 回放错误路径 `Code("UNKNOWN")` 可能 ValueError | 改为 `getattr(Code, response.get("code", ""), Code.CONTRACT_INVALID)` |
| P2-3 | P2 | 429 测试未断言 retry_after_seconds 值 | 断言 `isinstance(rr, int) and 0 < rr <= 30` |
| P2-4 | P2 | 审计测试未排除 ciphertext/digest | 新增 `assert "ciphertext" not in ...` 和 `assert "digest" not in ...` |

#### 退出命令

- `make lint`: 通过（316 files formatted）
- `make test`: 2533 passed, 640 skipped
- `make test-gateway`: 2017 passed, 580 skipped
- `make test-gateway-db`: 17 passed
- `make test-shared-runtime`: 79 passed
- `make test-w1`: 1688 passed
- `make test-w4b1`: 57 passed
- `make test-w4b2b`: 58 passed
- `make test-w4b2c`: 56 passed
- `make contracts-check`: PASS
- actionlint: 跳过（本轮未改 CI）