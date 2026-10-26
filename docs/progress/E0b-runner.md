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
