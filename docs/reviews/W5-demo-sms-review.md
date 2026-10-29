# W5-demo-sms 评审（D0 控制台登录 + D5 模拟短信取 OTP）

范围：`git diff origin/main...91be54b`（`services/gateway/app/api/demo.py`、`tests/gateway/w5_sms/{conftest,test_demo}.py`、`Makefile`、`.github/workflows/checks.yml`）

## 结论

**APPROVE**。D0/D5 实现与测试符合 PRD §9.7 D0/D5 与安全要求，未发现 P0/P1。要点全部落实：只在 DEMO/EVAL 挂载（生产 404）、D0 常量时间比较且 401 不泄露原因、console JWT 走真实调用方鉴权（service=D + aud=demo-console + scope=console + 签名/过期校验）、D5 复用 W4b2c 的 `derive_otp_keys`/`decrypt_otp_code`（AES-256-GCM + 同 `_otp_aad`，无新加密方案）、码不进审计/日志、已消费挑战经 G6 删除行后返回 404。仅三条 P2 覆盖性/审计小项，不阻塞合并。

## P0

（无）

## P1

（无）

## P2

1. **缺 assistant-aud JWT 被拒的测试** — `tests/gateway/w5_sms/test_demo.py:85`（`test_d5_wrong_aud_401`）只用 `aud=APP_AUDIENCE` 的 app JWT 验证「普通 JWT 不能调用 D5」；检查点明确要求「普通 app/assistant JWT 不能调用 D5」，assistant（`safebank-assistant`）走同一 aud 不匹配路径、风险低，但未直接覆盖。建议补一条 `aud=ASSISTANT_AUD` 用例。
2. **缺「缺 D 服务令牌」的 401 测试** — `tests/gateway/w5_sms/test_demo.py:76-96` 覆盖了无 JWT、错 aud、过期 JWT 三种 401，但未覆盖「带合法 console JWT、无 `X-Service-Token`」这一支（`D_CONSOLE` 的 `service=ServiceKind.D` 半边）。机制属既有 caller_dependency（W0 探针已验），本套件可补一条。
3. **D0 登录未写审计** — `services/gateway/app/api/demo.py:47-82` 的 `console_login` 无 `append_tx`，而 PRD §9.7 D0 行注明「全部操作审计标 DEMO」，D5 有 `DEMO_OTP_READ`、D0 缺失。控制台属可信方、影响极小，但为满足「全部操作审计」可补一条 `DEMO_CONSOLE_LOGIN`。

## 已核对项

- **demo.py（D0）**：`secrets.compare_digest(body.secret, settings.demo_console_token)` 常量时间比较（demo.py:55）；`expected` 为空 → 503 不泄露；错 secret → `GatewayError(Code.UNAUTHENTICATED, 401)` 无 message（demo.py:58）。`sign_jwt` 用 `sub="demo-console", aud=CONSOLE_AUD, scope=["console"], ttl=3600`（demo.py:66-73）。响应返回 `{token, expires_at_wall}`，比 PRD 记法多出 token（必需，非问题）。
- **demo.py（D5）**：查询 `otp_challenges JOIN approvals`（demo.py:110-130），`approval_id` 是 `otp_challenges` 主键（tables.py:219），`.one_or_none()` 安全。404 条件统一覆盖 行不存在 / `locked_at_wall` 非空 / `expires_at_wall <= now` / ciphertext 空 / `last_sent` 空 / 解密失败，均返回同一句 `"no active OTP challenge"`（demo.py:131,140,147,160），不泄露「已消费/锁定/过期/AEAD 不符」的区别。密文与 `last_sent` 的 tz 缺失时 503（demo.py:138-140）。
- **密钥/AAD 复用（W4b2c）**：`derive_otp_keys(otp_master)` + `decrypt_otp_code(ciphertext, approval_id, plan_id, view_hash, wall_now=last_sent, key=_aead_key)`（demo.py:148-155），与 G10 加密侧共用 `_otp_aad`（otp.py:82-99，AAD 含 approval_id/plan_id/view_hash/wall_now，键排序 JSON）。无新加密方案。AAD 的 `wall_now` 取 DB 读回的 `last_sent_at_wall`，生产默认 `wall_now` 为 `datetime.now(UTC)`（runtime.py:159），timestamptz 往返无损，`isoformat()` 字节一致——由端到端测试证实。
- **审计无明文**：`append_tx(event_type="DEMO_OTP_READ", payload={"approval_id": approval_id}, ...)`（demo.py:162-169），payload 仅 approval_id；`test_g10_d5_g6_full_flow` 断言 code 不出现在任何 audit payload 与 caplog 中（test_demo.py:187-203）。
- **挂载/矩阵**：`main.py:118 demo_mounted = settings.demo_mode or settings.eval_mode`，`main.py:129-130 if demo_mounted: app.include_router(demo.router)`；矩阵 `("POST","/demo/auth/login"): (D_SECRET,)`、`("GET","/demo/sms"): (D_CONSOLE,)`（matrix.py:85,91），`D_CONSOLE = CallerSpec(service=D, jwt_aud="demo-console", scopes={"console"})`（matrix.py:47）。
- **conftest.py**：`build_demo_app` 用真实 `_key_pair` + `build_public_app(..., public_key=public_pem, runtime=rt)`（conftest.py:92-110），JWT 走真实签名/aud/scope/过期校验，非桩；`wall_now=_real_now`（UTC）；`demo_mode=False` 时 `demo_console_token=None` 且 D 服务令牌置空（conftest.py:64,75）。
- **测试覆盖**（test_demo.py）：模式关闭 404（:36）、D0 成功（:51）与错 secret 401（:65）、D5 无 JWT 401（:76）、错 aud(app) 401（:85）、过期 console JWT 401（:99）、过期挑战 404（:119）、G10→D5→G6 全链路 + 审计/日志无明文 + 消费后 404（:147-216）。**「已消费的挑战返回 404」不缺失**：G6 验证成功后 `sqla_delete(otp_challenges)`（approvals.py:430），test_demo.py:213-216 断言其后 D5 为 404。
- **CI/Makefile**：`checks.yml:40` 在 p1-postgres job 加入 `make test-w5-sms`；`Makefile` 定义 `test-w5-sms: GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w5_sms -q`（真实 tmpfs PG）。
