# W4b2a Runtime Approval Keys — 独立只读评审

**评审日期**: 2026-10-24
**范围**: `git diff 8ad47d0 4decdfd`（9 files, +538/-4）
**基准**: 任务书 `docs/plans/briefs/W4b2a-runtime-keys.md`、进度 `docs/progress/W4b2a-runtime-keys.md`、PRD §7.10/§11.1、W4b1 验收、S1c 先例。
**测试定点验证**: `test_approval_keys.py` 16 passed、`test_runtime.py` 23 passed/2 skipped。

## 逐项核对

### 1. 密钥来源：显式 Settings，不读进程环境补缺

- `config.py:101-102`: `from_env` 使用 `env.get("APPROVAL_HMAC_KEY") or ""` / `env.get("OTP_HMAC_KEY") or ""`。`env` 在显式传入时是 `dict(env)`，不触碰 `os.environ`。 ✅
- `test_settings_from_env_only_reads_explicit_mapping` (`test_approval_keys.py:303-338`): 空映射→空串、映射无键但进程环境有键→空串、映射显式提供→取映射值。三个断言均验证通过。 ✅
- `test_missing_key_does_not_fallback_to_environment` (`test_approval_keys.py:70-98`): `monkeypatch.setenv` 设环境变量后，构造无 keys 的 Settings → runtime 拒绝启动（DEPENDENCY_UNAVAILABLE 503），`forbidden_pool` 未被调用。 ✅
- 生产路径 `serve(from_env())`（`main.py:283`）传入 `None`，正确读取 `os.environ`（compose 注入的 env file）。测试与生产路径语义分离。 ✅

### 2. 校验时序：资源分配前 fail-closed

- `runtime.py:171`: `_validate_approval_keys(settings)` 在 `AsyncExitStack` 进入（172 行）之前调用，因此在 `db_factory`（176）、`locks.configure`（184）、`l3_factory`（185）之前。
- 校验覆盖：非 str/空/空白 → "empty or blank"；UTF-8 编码 `<32 bytes` → "at least 32 bytes"；两 key 相同 → "different"；等于任一 `service_tokens.values()` 或 `gateway_service_token` → "service token"。六类故障路径均参数化覆盖（`test_approval_keys.py:104-140`）。
- 错误码: `_unavailable` 固定 `GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)`，符合任务书/PRD。 ✅
- 错误消息不含原值：所有 error message 均为固定英文短语，不含 key 原文。`test_keys_do_not_appear_in_repr_or_error_chain` 断言 `str(exc.value)` 不含 `"short"` 或合成 key。 ✅
- `field(repr=False)` 在 `Settings`（`config.py:38-39`）和 `GatewayRuntime`（`runtime.py:137-138`）两处，`test_settings_repr_excludes_keys` 验证。 ✅

### 3. 双 runtime 隔离，两 app 共享同一对象

- `test_two_runtimes_keep_independent_key_snapshots` (`test_approval_keys.py:192-240`): 并发两个 runtime，各自持有独立 key snapshot；`app.state.approval_hmac_key is first.approval_hmac_key` 验证两 app 共享同一 bytes 对象；第一个 runtime 关闭后 `first_db.closed == 1` 而 `second_db.closed == 0`。 ✅
- `test_environment_change_does_not_affect_existing_runtime` (`test_approval_keys.py:243-280`): 环境变更后已启动 runtime/app 不变化；新 runtime 读取新 Settings。 ✅
- 未调用 `approval.service.configure`：`services/gateway/app/main.py` 全文件 grep 零命中。Settings 上无全局缓存，GatewayRuntime 是 frozen dataclass。 ✅

### 4. 文件范围与冻结

- 实现文件: `config.py`（+4）、`runtime.py`（+52）、`README.md` ✅
- 新测试: `test_approval_keys.py`（338 行） ✅
- 必要 fixture 适配: `test_runtime.py` 的 `secret_settings` 新增两环境变量（+2 行）、`support.py` 的 ENV/harness 注入合成 keys（+4 行） ✅ 均在任务书白名单内。
- **`conftest.py` (+7 行)**: 任务书未显式列出，变更内容为从 `test_runtime.py` 重新导出 `Client, Engine, fake_engine_lock_hook, fake_lock_configuration, secret_settings` 供 `test_approval_keys.py` 使用。pytest 不自动发现相邻测试文件的 fixtures，必须通过 conftest 或显式导入。这是必要 fixture 适配，无业务逻辑。**P2**: 不在白名单但属必要样板代码，可接受。
- 冻结文件零修改: `approval/service.py`、`planview/`、`main.py`（除已有 serve/from_env 路径外）、`auth/`、`state/`、`migrations/`、`compose`、`scripts/keys.py`、`Makefile`、`CI`。 ✅

### 5. 生产部署兼容

- `scripts/keys.py:39-49`: `token_urlsafe(32)` 生成 `APPROVAL_HMAC_KEY` / `OTP_HMAC_KEY`，写入 `.secrets/gateway.env`。 ✅
- `docker-compose.yml:74,198`: 两个 gateway 服务端口均挂载 `.secrets/gateway.env`。 ✅
- `config.py:101-102`: `from_env` 读取键名 `APPROVAL_HMAC_KEY` / `OTP_HMAC_KEY`，与 `scripts/keys.py` 生成的变量名一致。 ✅
- 默认值 `field(default="", repr=False)`: 空串仅在构造期兼容，运行时必被 `_validate_approval_keys` 拒绝（"empty or blank" → 503）。缺配置不会静默通过。 ✅

### 6. 测试真实度

- 红测证据：进度报告记载初始 16 failed（`AttributeError` / `TypeError`，Settings 无字段），符合预期。
- `test_invalid_keys_fail_before_resource_allocation`：`factory_called` 列表断言 db_factory/l3_factory 零调用，非仅查异常类。 ✅
- `test_keys_do_not_appear_in_repr_or_error_chain`：同时验证 repr 无 key bytes 和 error message 无 key 子串。 ✅
- `test_two_runtimes_keep_independent_key_snapshots`：验证 `is` 身份（共享同一对象）和 `==` 值、两个 runtime 独立（`is not`）、资源关闭顺序。 ✅
- `test_environment_change_does_not_affect_existing_runtime`：环境变更后旧 runtime 不变 + 新 runtime 读取新 Settings。 ✅
- 16 项全部独立 pass，无 skip/xfail。 ✅

## 未确认 / 已知限制

1. **服务令牌复用检查无测试覆盖** (`runtime.py:113-123`): 测试种子 `GATEWAY_SERVICE_TOKEN="l3-token"` 仅 9 字符（<32 bytes），"复用令牌"参数化 (`"l3-token"*3`=24 chars）实际命中长度检查。代码路径本身（`if token and approval_bytes == token.encode("utf-8")`）是简单字符串比较，逻辑正确，且已在进度报告中记录。**P2**：建议后续在独立测试 fixture 中构造 ≥32 bytes 的服务令牌以覆盖此路径。
2. **`test_settings_from_env_only_reads_explicit_mapping` 直接修改 `os.environ`**（`test_approval_keys.py:322-323`）而非使用 `monkeypatch.setenv`。`finally` 清理充分，但若进程在 `del` 前被 SIGKILL 会残留环境变量。仅影响测试隔离性，不涉及生产。

## 结论

**APPROVE** — 无 P0/P1。

P2（2 条，均不阻塞合并）：
1. `runtime.py:113-123` 服务令牌复用守卫无测试覆盖（种子长度不足），代码路径简单、逻辑正确。
2. `conftest.py` 不在任务书显式白名单，变更为 7 行 fixture 重新导出，属必要适配。