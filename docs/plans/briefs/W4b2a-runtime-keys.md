# W4b2a Runtime Approval Keys Implementation Plan

> 准备稿，尚未派发：DeepSeek API 402余额不足，恢复证据出现后才由本机Claude Code CLI / DeepSeek V4 Pro逐任务实施/独立评审。用户已授权自主推进，协调者只计划/文档/亲验/发布，不编业务代码，不用自身subagent。

**Goal:** 从显式Settings一次性取得审批/OTP独立密钥快照，由单进程GatewayRuntime持有并安装到两app，缺失/短值在资源分配前fail-closed。

**Architecture:** W4b1纯库已合PR #22；后续W4b2拆成此共享运行时资源组件，再做确认/OTP端点组件，最后才W3b真实G4。此组件不调用全局approval.service.configure、不改其API；持久审批状态与OTP算法仍后续owner接入，避免配置两个runtime时改写全局key。本组件只提供真实资源快照，不声称G6/OTP/G4已开通。

**Tech Stack:** 现有Python3.12/dataclasses/FastAPI/SQLAlchemy，冻结依赖。

**Spec:** PRD7.10/11.1（APPROVAL_HMAC_KEY、OTP_HMAC_KEY仅gateway）、AGENTS、W4b1验收、S1c-pin-settings接线原则。实际scripts/keys.py已经在忽略的gateway.env生成两项token_urlsafe(32)字符串，compose已只注入gateway；不要重新生成密钥或新增文件格式。

## Global Constraints / ownership

共享代码串行，仅services/gateway/app/config.py、runtime.py、services/gateway/README.md、tests/gateway/shared_runtime/test_runtime.py、新tests/gateway/shared_runtime/test_approval_keys.py、docs/progress/W4b2a-runtime-keys.md；既有W1真实runtime测试需要注入合成keys时仅允许tests/gateway/w1/db/{test_s1_runtime.py,support.py,test_pin_settings_integration.py}必要fixture适配，不改变原断言语义。shared contracts/schema/approval-service/planview/OTP/API/main/auth/locks/state/tables/migrations/compose/scripts/keys.py/依赖/Makefile/CI/AGENTS/PRD冻结。

不读真实.env/.secrets；只合成字符串/临时测试文件。无随机/服务token默认key，无请求期环境读取/密钥轮换，不把key写响应/错误/日志/repr/audit/DB。无HTTP写银行/授权决定。禁止全栈up/down/test-p1、长期卷/数据/reset/TRUNCATE/push/PR/merge/派agent；真实PG仅已有独立tmpfs fixture。

## Interfaces / fixed choices

- Settings新增approval_hmac_key: str与otp_hmac_key: str，默认空串仅为构造兼容，field(repr=False)。from_env仅取传入映射中的APPROVAL_HMAC_KEY/OTP_HMAC_KEY，不读取其它进程环境补缺，不将设置缓存成全局。保留所有既有field/validate行为。
- gateway_runtime启动从该Settings快照取两项，UTF-8编码成bytes（不base64解码、不strip改变key），各至少32byte，拒空/全空白/非法类型；两key互异，且不能等于已配置的F/A/S/D或GATEWAY_SERVICE_TOKEN。所有校验失败为固定GatewayError(DEPENDENCY_UNAVAILABLE,503)，错误链/repr不含原值。
- GatewayRuntime新增approval_hmac_key: bytes与otp_hmac_key: bytes，必须明确提供且repr=False；install(app)将相同bytes对象分别放app.state.approval_hmac_key/otp_hmac_key，两app复用同runtime资源。profile与wall仍复用现有settings.profile/wall_now，不另造请求配置。
- 必须在db_factory/locks.configure/l3_factory分配前验证key。正常生命周期资源创建/关闭顺序、池容量/两网/Ed25519/PIN现有行为不变。仅新keys验证不放入build_public_app健康探针构造；生产通过gateway_runtime必校验。
- 本组件不调用approval.service.configure：现有全局_config无法证明两个不同runtime的隔离。本组件验收只承诺资源按实例隔离；后续确认/创建调用方须设计显式实例适配，不把全局重配当实例隔离。不要现在顺手改库/HTTP端点。

## Review Focus

1. 显式Settings A与进程环境B相反时只取A；缺A不能借B成功。
2. 两个不同runtime同时存活、退出其中一个、重新启动均无key串用；两app确实共享同一runtime，Settings/Runtime repr无合成key。
3. 缺/短/重复/全空白key在分配任何DB/L3资源前拒，错误字符串/链不泄原值。
4. 输入字符串按UTF8字节数验证，不误解既有token_urlsafe文本、不读取或旋转真实gateway.env。
5. 旧W1登录/会话、PIN路径快照/清理与锁资源回归不因新必需项而放宽；fixture仅加合成key。

### Task 1: Settings and fail-closed snapshot

Files: config.py/runtime.py、新test_approval_keys.py。

- [ ] 写红测test_keys_use_explicit_settings_snapshot、test_missing_key_does_not_fallback_to_environment、test_invalid_keys_fail_before_resource_allocation（各项参数化）、test_keys_do_not_appear_in_repr_or_error_chain；断言实际资源工厂调用为0，不能只查异常类。
- [ ] .venv/bin/pytest tests/gateway/shared_runtime/test_approval_keys.py -q，确认因接口未实施红测。
- [ ] 最小新增上述fields与启动验证helper；不打印值，不改授权逻辑或读取真实key。
- [ ] 定点绿，小提交并署名DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>。

### Task 2: Runtime isolation and real regression

Files: runtime.py、test_approval_keys.py、允许的既有runtime fixture。

- [ ] 红测两个不同Settings/runtime嵌套与两app安装后值/对象身份一致；把进程环境改为第三个值，老runtime不变，新runtime仍按其Settings，退出/部分启动失败资源关闭各一次，重启读取新的显式Settings。只模拟DB/L3工厂测试生命周期，不能称真实PG。
- [ ] 既有真实临时PG runtime/W1 fixture仅补合成keys后，实际池连接/登录/两个PIN文件相反环境的测试照旧通过，不能删断言或新增skip。
- [ ] 跑定点红→最小实现→绿，小提交。校验global approval配置未被调用/改写。

### Task 3: Acceptance and freeze

- [ ] make setup、make lint/test/test-gateway/test-gateway-db/test-shared-runtime/test-w1/test-w4b1/contracts-check全跑；数据库使用原有专用环境开关/临时fixture，分清pure/fake/真PG与skip，不冒充G6或OTP。
- [ ] README/进度列资源名称、实际红绿/测试、仅实例key快照未服务接线、完整确认/OTP/调用方适配后续；无API新行为/无阶段闭合宣称。
- [ ] 本地commit后冻结回SHA，停止待协调者亲验/独审，不自行开始下一端点组件或发布。

自审：此小组件只补已规定的密钥运行时资源，未改模型/协议/授权/金额/网络架构。生产审批configure的实例适配问题明确留下一组件，不能以资源存在代替审批或OTP可用。
