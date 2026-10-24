# W4b2a Runtime Approval Keys 进度

日期：2026-10-24；基线：main `8ad47d0`（最新，含已合 W4b1 PR #22）；分支：`codex/w4b2a-runtime-keys`。

## 范围与验收契约

- 输入：`Settings.approval_hmac_key` / `Settings.otp_hmac_key`（from_env 只从传入映射取，默认空串仅为构造兼容，`repr=False`）。
- 输出：`GatewayRuntime.approval_hmac_key: bytes` / `GatewayRuntime.otp_hmac_key: bytes`（UTF-8 编码，`repr=False`），`install()` 放到 `app.state.approval_hmac_key` / `app.state.otp_hmac_key`。
- 验证（在 db_factory / locks.configure / l3_factory 之前）：非空非空白、各 ≥32 bytes、互异、不等任何已配服务令牌（F/A/S/D/GATEWAY_SERVICE_TOKEN）；失败 `GatewayError(DEPENDENCY_UNAVAILABLE,503)`，错误链/repr 不含原值。
- 依赖：W4b1 PlanView/HMAC 审批库已合 main；W1 已合 main。
- 失败条件：Settings 取环境、缺/短/重复/全空白 key 可分配资源；repr/错误泄 key；两个 runtime 串用密钥；环境变更影响已启动 runtime。

## 实现摘要

| 文件 | 变更 |
|---|---|
| `services/gateway/app/config.py` | Settings 新增 `approval_hmac_key: str`/`otp_hmac_key: str`（默认空串、repr=False）；from_env 只取传入映射 |
| `services/gateway/app/runtime.py` | 新增 `_validate_approval_keys(settings)` 在生产资源分配前校验；GatewayRuntime 新增 `approval_hmac_key: bytes`/`otp_hmac_key: bytes`（repr=False）；`install()` 安装到 app.state |
| `tests/gateway/shared_runtime/test_approval_keys.py` | 新文件：16 项测试覆盖显式 Settings 快照、缺 key 不取环境、无效 key 在资源分配前拒、repr/错误链无 key、双 runtime 隔离、环境变更不影响已有 runtime、Settings repr 无 key、from_env 只取映射 |
| `tests/gateway/shared_runtime/conftest.py` | 导出 test_runtime 的 fixtures/类使对全 shared_runtime 可见 |
| `tests/gateway/shared_runtime/test_runtime.py` | secret_settings fixture 新增合成 APPROVAL_HMAC_KEY/OTP_HMAC_KEY |
| `tests/gateway/w1/db/support.py` | ENV 与 harness 注入合成 keys，原断言语义不变 |
| `services/gateway/README.md` | 环境变量表补充 W4b2a 资源说明 |

红绿记录：初次红 16 failed（AttributeError / TypeError，Settings 无这两字段）；两 fixture/格式修复后绿 16 passed。

## 实际退出命令（全量）

| 命令 | 结果 |
|---|---|
| `make setup` | 78 锁定包 ✅ |
| `make lint` | ruff check/format 通过，291 files ✅ |
| `make test` | 2419 passed / 561 skipped，10.84s ✅ |
| `make test-gateway` | 1978 passed / 501 skipped，7.88s ✅ |
| `make test-gateway-db` | 17 passed（真实临时 PG），2.00s ✅ |
| `make test-shared-runtime` | 75 passed（含 16 新 + 59 既有），8.35s ✅ |
| `make test-w1` | 1688 passed（51.52s，23 真实 PG），零 skip/fail ✅ |
| `make test-w4b1` | 57 passed（34 纯函数 + 23 真实临时 PG），3.01s ✅ |
| `make contracts-check` | PASS ✅ |

shared-runtime 的 `test_servers.py:68` 取消场景继续打印预期的 `CancelledError`，命令 exit 0，未隐藏。

无 Docker 数据卷创建/删除；无 up/down/test-p1/test-integration。长期栈未运行。未 push/PR/merge。

## 未完成 / 边界

- 本组件只提供审批与 OTP 的两项独立密钥运行时资源快照，不调用 `approval.service.configure`、不声明 G6/OTP/G4 已开通。
- 生产 `approval.service.configure` 的实例适配（如何让一个 service 拿到当前 runtime 的 key）留给后续组件，本组件不把全局重配当实例隔离。`approval/` 下 HMAC 验证仍走全局 `_config`，本组件不更改。
- W1 真实 PIN 文件 + runtime + PG 两路径登录已通过（1688 passed），这次仅 fixture 适配，语义词干不变，不重新跑 PIN 真实验收。
- `test_invalid_keys_fail_before_resource_allocation` 中「key 等同服务令牌」的参数化因现有测试 seeds 的 `GATEWAY_SERVICE_TOKEN="l3-token"`（短于 32 bytes），实际命中「太短」而非「复用令牌」检查；两个检查的代码路径均通过单独推理校验，无绕过。

## 设计决策

- 验证在 `gateway_runtime()` 内（每次启动），不在 `Settings.validate()`（那是静态护栏）。原因：Settings 作为纯数据对象保有默认空串以兼容构造；资源级校验必须在资源分配前，属于启动生命周期代码。
- `approval_hmac_key` / `otp_hmac_key` 在 GatewayRuntime 中是 `bytes`（一次编码），在 Settings 中是 `str`（原样来自 env）。不 strip、不 base64 解码——直接 UTF-8 编码。
- `from_env` 只从传入映射取这两项：`env.get("APPROVAL_HMAC_KEY") or ""`，不读 `os.environ` 补缺。这是 S1c PIN 接线先例的延续。

## 待决

- 后续 `approval.service.configure()` 显式实例适配设计（当前库用全局 `_config`），需在 W4b2 确认/OTP 组件中决定：是一次性在 gateway_runtime 调用 configure，还是给每个 HTTP 请求传入 runtime key。
- OTP 密钥的消费者（W5 OTP 挑战/验证端点）尚未实现，届时直接从 `app.state.otp_hmac_key` 取。


## 修复轮 1（独立评审 P2-1）

独立评审 `docs/reviews/W4b2a-runtime-keys-review.md` 结论 **APPROVE**，P2-1 要求补服务令牌复用守卫的测试覆盖。

**P2-1 修复：** 新增 `test_key_reuse_of_long_service_token_rejected_before_resources`（4 参数化用例）：
- 构造 ≥32 字节的 `gateway_service_token` 与 F 服务令牌
- 审批密钥 / OTP 密钥分别等于这些长令牌
- 断言在资源分配前被 `DEPENDENCY_UNAVAILABLE` 503 拒绝
- 错误消息含 "service token" 但不含原值
- `factory_called` 列表为空（db/l3 工厂未调用）

**红→绿验证：** 临时注释 `runtime.py` 的 113-123 行（服务令牌复用守卫）→ 4 项全部 FAIL（`AttributeError: 'NoneType' object has no attribute 'dispose'`，证明 `forbidden_resource` 被调用即验证通过）→ 还原守卫 → 4 项全部 PASS。

| 命令 | 结果 |
|---|---|
| `make lint` | ✅ 291 files |
| `make test-shared-runtime` | ✅ 79 passed（20 新 + 59 既有），8.22s |
| `make test-gateway` | ✅ 1982 passed / 501 skipped |
| `make test` | ✅ 2423 passed / 561 skipped，10.75s |

无 Docker 数据卷操作；未 push/PR/merge。本地提交冻结。

Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>