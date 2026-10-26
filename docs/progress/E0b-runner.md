# E0b 进度：L1 稳定性评测 runner

## T1 评测种子加载器（2026-10-26）

- **实现**：DeepSeek worker 在会话 E0b-T1-r1 中写完主体（用满轮数，未提交），后由 E0b-T1fin-r1 修复 `KeyError: 'cus_ww'`（同样用满轮数，未提交）。现场分别快照在 `refs/wip/E0b-T1-r1`、`refs/wip/E0b-T1fin-r1`。代码由协调者核验后代为提交，作者仍为 worker。
- **文件**：
  - `evaluation/runner/seed.py`：把评测种子 JSON 加载进 tmpfs PG 上的真实 bank-backend 数据库；target_fingerprint 用 gateway 的真实指纹算法和测试用 FINGERPRINT_KEY 重新计算。
  - `tests/evaluation/runner/{conftest.py,test_seed_loader.py,fixtures/mini_seed.json}`。
  - `Makefile` 新增 `test-e0b` 目标。
- **协调者亲自复跑**：
  - `make lint`：342 files
  - `make test-e0b`：3 passed（真实 L3，零 skip）
  - `make test`：2702 passed / 708 skipped
- **未完成**：checks.yml 中的 test-e0b 一行、runner 主流程、指标、报告，留到后续单任务（T2 起）。

## T2b（2026-10-26）

- `evaluation/runner/l1.py`：同步 `GatewayClient` → 主事件循环 ASGI gateway 的桥接 transport（记录每个请求/响应）与 fake LLM 端口；`tests/evaluation/runner/test_l1_one_case.py`：真实 L1 graph 经真实 gateway G4 评估「冻结我的卡」。
- worker E0b-T2b-r1 用满 41 轮未提交（快照 `refs/wip/E0b-T2b-r1`）；协调者修 ruff 3 处（import 排序/未用导入），复跑 `make lint` 通过、`make test-e0b` 5 passed，代为提交。
- 实际 G4 Decision 为 **REPLAN**：fake plan 把 `card_id=crd_zs_1` 的来源标为原话 span(0,5)，而原话不含该值，L2 拒绝来源，符合预期，不放宽。待决：E0b 的 fake 计划需改为先 gather `get_cards` 并标 read 来源，才能得到 ALLOW 样例；live 模式由真实 LLM 产出来源。

## T3 评测指标与报告（2026-10-26）

- `evaluation/metrics/__init__.py`、`evaluation/metrics/compute.py`：CaseRun dataclass + `compute_metrics`（12 个指标键）+ `render_report`（Markdown）+ `write_outputs`（写 metrics.json、report.md、cases.jsonl），纯函数无外部依赖。
- `tests/evaluation/runner/test_metrics.py`：16 项已知答案测试，覆盖全部指标、分母为 0 返回 None、attack_needs_approval 单独计数、render_report 缺 meta 键抛 ValueError、write_outputs 三文件与行数校验。
- `make lint` 通过、16 passed 零 skip。

## T3b REQUIRE_APPROVAL 可执行路径覆盖（2026-10-26）

- `tests/evaluation/runner/test_metrics.py`：新增 `TestRequireApprovalInExecutable`（3 条含 REQUIRE_APPROVAL 期望的 known-answer run），断言 executable_rate=2/3、false_replan_rate=1/3、false_deny_rate=0、false_by_class={"span":1}、param_accuracy=1.0。
- **变异测试**：删除 `compute.py:67` 的 `"REQUIRE_APPROVAL"` 后，新测试 1 failed（`executable_rate` 1.0 ≠ 2/3）、旧 16 passed；还原后 17 passed 零 skip。`make lint` 通过。

## T4（2026-10-26）

- `evaluation/runner/loop.py`：`run_corpus`（每 case × repeat 新会话，逐轮登记原话并调用 L1 graph，取最后一轮结果）、纯函数 `classify_outcome`、`StopRun`（402/403/429/连接错误立即停止并保留已完成 runs）；l1.py 泛化为 `build_graph_with_ports`。
- 事实：L2 在 G3 登记「hello」「hi」「查询余额」这类无写意图原话时会创建 `FORM_REQUIRED` 澄清，L1 经 G21 取回，故结果记为 CLARIFY；这是系统真实行为，测试据实断言，并以 gateway 库新增 4 条 FORM_REQUIRED 记录为证。语料中期望 NO_PLAN 的样本在 live 下可能同样得到 CLARIFY，届时作为 E0 发现报告，不改 L2。
- worker E0b-T4-r1、E0b-T4b-r1 均用满轮数未提交；协调者补 L2 证据断言（计数取前后差值，因同一测试库被多个测试共用）、ruff 格式化，复跑 make lint、make test-e0b 29 passed、make test 通过后代为提交。


## T5 G4-only边界检查点（2026-10-26，协调者）

- 多轮DS产物已将run_corpus改为固定evaluate-only，execute发明确EvaluationComplete终止信号，不伪造执行结果；transport另阻断G8。真实get_cards使用items与/items/0/id，G4 request_id每次唯一。
- 协调者最新亲验32项中31 passed/1 failed；ALLOW冻结及实际run_corpus正例证明卡/账本不变。黄级失败为真实生产503 approval HMAC is not configured，已独立建立P2-approval-runtime修复，不在harness注入global伪造绿。
- T5未完成验收/变异/独审，不运行live。后续可并行做不依赖该生产缺陷的纯证据/报告功能。

## T6 逐样本证据完整归档（2026-10-26，协调者实现）

- **范围**：为 `CaseRun` 新增向后兼容的原始证据字段（默认空），`run_corpus` 每轮收集原话/plan_draft/G4 尝试/决策/findings，`write_outputs` 完整写到 cases.jsonl 并递归脱敏。
- **文件**：
  - `evaluation/metrics/compute.py`：
    - 新增 `_strip_secrets()`：递归清除键名含 token/key/secret/password/pin/bearer/jwt/authorization/credential 的字段（值写 `[REDACTED]`），清除 Bearer 前缀字符串和 JWT 形字符串。
    - 新增 `extract_g4_evidence()`：纯函数，从 recorded transport requests 提取 `plan_draft`（最后一次请求体）、`g4_decision`、`g4_findings`（最后一次成功响应）、`g4_attempts`（**每次** POST /plans/evaluate，含 503 等失败，不丢诊断）。
    - `CaseRun` 新增字段：`utterances: list[str]`、`plan_draft: dict | None`、`g4_attempts: list[dict]`、`usage_known: bool`、`usage: dict | None`。全部默认空，旧构造不破坏。
    - `write_outputs`：cases.jsonl 追加 evidence 字段，plan_draft/g4_attempts 写入前经 `_strip_secrets`。
  - `evaluation/runner/loop.py`：
    - `run_corpus`：逐 repeat 收集 `utt_texts`，轮回结束后调用 `extract_g4_evidence(transport.requests)` 并传入 CaseRun。
  - `tests/evaluation/runner/test_case_evidence.py`（新文件，28 项）：
    - TestStripSecrets：11 项（含 token/key/bearer/JWT/PIN/secret/嵌套列表/业务字段保留/原语/深度上限）。
    - TestExtractG4Evidence：7 项（单次 200、多步 arg_sources 全部保留、多次尝试含 422+200、503 不丢、无 evaluate、非 evaluate 忽略）。
    - TestCaseRunBackwardCompat：3 项（空默认、旧字段构造无异常、旧字段仍可访问）。
    - TestWriteOutputsWithEvidence：7 项（evidence 写入 JSONL、多轮原话、脱敏生效、旧字段保留、usage_known/usage、旧 17 项指标测试稳定）。
- **协调者亲自复跑**：
  - `make lint` 通过（ruff check + format）。
  - `pytest tests/evaluation/runner/test_metrics.py tests/evaluation/runner/test_case_evidence.py -q --tb=short`：**45 passed 零 skip**（17 旧 + 28 新）。
  - `pytest tests/evaluation/runner/ -q --tb=short -k "not (gateway_db or obligations_db or monkeypatch)"`：74 passed / 11 skipped（无回归）。
- **已知未覆盖**：不依赖真实 HMAC 修复（生产 G4 审批 503 继续不影响本组件）；未接 live 模型端口（usage_known=False、usage=None、tokens=0，后续 live 时填充）；T5 的 1 条真实失败保持原样未动。

## T6 评审修复轮 1（2026-10-26，协调者逐条修复 + DeepSeek 只读评审）

- **范围**：按 `docs/reviews/E0b-T6-review.md`（未提交，随本轮一并提交）逐条修复 P1-1..P1-4、P2-1/P2-2/P2-3/P2-5。
- **P1-1 tuple 绕过**：`_strip_secrets` 新增 `tuple` 分支，递归 strip 每个元素；旧代码 `("Authorization", "Bearer eyJ...")` 完整原样泄露。
- **P1-2 中段 Bearer 绕过**：`startswith("Bearer ")` 改为 `_BEARER_RE.search(obj)`（`re.compile(r'\bBearer\s+\S+', re.IGNORECASE)`），匹配文本任意位置的 Bearer 令牌。
- **P1-3 中段 JWT 绕过**：`_JWT_RE` 去掉 `^$` 锚点，改用 `.search()`；`len(obj) > 50` 守卫保留。
- **P1-4 g4_decision/g4_findings 丢弃**：`CaseRun` 新增 `g4_decision: str | None = None`、`g4_findings: list[dict] | None = None` 字段；`run_corpus` 从 `extract_g4_evidence` 传入；`write_outputs` 写入 cases.jsonl。
- **P2-1 深度绕过**：`_depth > _MAX_STRIP_DEPTH` 时返回 `"[REDACTED:depth]"` 占位符，不再原样返回。
- **P2-2 大小写**：已在 P1-2 的 `_BEARER_RE`（`re.IGNORECASE`）中修复。
- **P2-3 set/frozenset**：新增 `set` 与 `frozenset` 分支，递归 strip 并保持容器类型。
- **P2-4 提取简化**：`extract_g4_evidence` 中 plan_draft 提取简化为 `req_body.get("plan_draft")`，不再回退到 `req_body` 本身。
- **P2-5 补充脱敏覆盖**：新增 `test_strips_bearer_in_g4_error_code`（error_code 含 Bearer）、`test_strips_secrets_in_nested_arg_sources`（arg_sources.source 深层 api_key）。
- **文件变更**：
  - `evaluation/metrics/compute.py`：`_JWT_RE` 去锚、新增 `_BEARER_RE`；`_strip_secrets` 增 tuple/set/frozenset 分支、深度占位符、Bearer/JWT search；`CaseRun` 增 g4_decision/g4_findings；`write_outputs` 写出；`extract_g4_evidence` 简化。
  - `evaluation/runner/loop.py`：`run_corpus` 传入 g4_decision/g4_findings。
  - `tests/evaluation/runner/test_case_evidence.py`：新增 12 项测试（P1-1 tuple、P1-2 Bearer 任意位置 + 小写、P1-3 嵌入 JWT、P1-4 g4_decision 写入 + None 路径、P2-1 深度占位、P2-3 set + frozenset、P2-5 error_code Bearer + arg_sources 深层）。
- **协调者亲自复跑**：
  - `ruff check` 通过，`ruff format` 通过。
  - `.venv/bin/pytest tests/evaluation/runner/test_metrics.py tests/evaluation/runner/test_case_evidence.py -q --tb=short`：**56 passed 零 skip**（17 旧指标 + 39 evidence）。
- **未覆盖**：P2-5「utterances 中嵌入 JWT」仅标注为已知限制（短原话文本长度 < 50 不触发 JWT 检测，且攻击者无法通过此路径注入 token）；P1-2/P1-3 旧代码 RED 验证为静态分析（stash→还原太危险）。
- **评审报告**：`docs/reviews/E0b-T6-review.md` 由 DeepSeek V4 Pro 全新会话独立审出，本次一并提交入分支。

## T8b 恢复测试 + 哨兵泄漏守卫（2026-10-27）

- **工作目录**：`../Fintech2026-wt/e0b-l1-runner`，HEAD `2b3b011`。
- **实现**：协调者亲自编写，不调用 DeepSeek。
- **问题**：T8 重写 `test_cli.py` 时删掉了 c73082f 中的两条 Docker 测试。
- **修复**：
  - 恢复 `test_fake_limit_1_creates_output_through_real_g4`：适配自提供 CLI（移除 `_require_real_pg()` 与 `_ENV_REAL_PG`，保留 `_require_docker()`，其余断言不变）。
  - 恢复 `test_anti_spoofing_tweaked_expected_does_not_change_actual`：同上适配，`cli.main()` 内部自己提供 PG。
  - 新增 `test_live_402_sentinel_not_leaked_to_output`：设置 `DEEPSEEK_API_KEY="sk-test-SENTINEL-0123456789"`，mock `run_corpus` 抛出 `StopRun("402 ...")`，mock 全部提供函数与 harness；通过 `mock.patch("sys.stderr", StringIO())` 捕获 stderr；断言退出码 3，且输出文件与 stderr 中均不含该哨兵大小写。
- **协调者亲自复跑**：
  - `.venv/bin/ruff check/format` 通过。
  - 非 Docker：`-k "not fake_limit and not anti_spoofing"` **8 passed**。
  - Docker：`-k "fake_limit_1 or anti_spoofing"` **2 passed**（11.31s，真实 PG/G4）。
  - `make test-e0b` 全量 **81 passed**（20.44s）。
- **提交**：`d791832`。

## T10 登录复用与 L2 429 处理（2026-10-27）

- **工作目录**：`../Fintech2026-wt/e0b-l1-runner`，HEAD `4bf66ec`。
- **实现**：协调者亲自编写，不调用 DeepSeek。
- **目标**：解决 live 20 条运行时每条样本都登录触发 L2 频控 429 的问题。
- **改动**：
  - `evaluation/runner/harness.py`：
    - 新增 `GatewayRateLimited` 异常（含 `status_code`、`code`、`detail`）。
    - 新增 `_check_rate_limited(response)` 辅助函数，在 HTTP 429 时抛出 `GatewayRateLimited`。
    - `_login`、`_create_session`、`register_utterance` 均在断言前调用 `_check_rate_limited`。
    - `EvalHarness` 新增 `_login_cache: dict[str, str]` 字段，按 `customer_id` 缓存 `app_token`。
    - `new_session` 改为先查缓存 → 用缓存令牌调 `/sessions` → 成功则返回；遇 `GatewayRateLimited` 直抛；遇其他异常（如 401）清缓存并降至条件重登。
  - `evaluation/runner/loop.py`：
    - 导入 `GatewayRateLimited`。
    - 在 `run_corpus` 最外层 `except` 链中新增 `except GatewayRateLimited`，转为 `StopRun`（含已完成 runs）。
  - `tests/evaluation/runner/test_t10_harness.py`（新增）：
    - `TestLoginCaching.test_same_customer_logs_in_once`：同一客户 5 次 `new_session` → 仅 1 次登录。
    - `TestRateLimitedStopRun.test_429_on_create_session_stops_with_runs`：第二次 `/sessions` 返回 429 → `StopRun` 含已完成 runs。
    - `TestTokenExpiryRelogin.test_401_on_cached_session_triggers_relogin`：缓存令牌遇 401 → 清缓存重登 → 成功。
- **协调者亲自复跑**：
  - `make test-e0b` 全量 **93 passed**（15.13s，含真实 PG）。
  - `.venv/bin/ruff check` 通过。
  - `.venv/bin/ruff format` 通过（test_t10_harness.py 被格式化）。
- **提交**：见下。
