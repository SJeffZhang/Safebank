# E0b Runner 验收：L1 稳定性评测

**状态**：组件开发中（T14 收尾）  
**日期**：2026-10-27  
**分支**：`codex/e0b-l1-runner`

## 组件说明

E0b 是 L1 稳定性评测 runner，对 E0a 语料库（405 条）逐条运行并输出指标、报告和逐条结果。

**运行模式**：
- `fake`：fake LLM ports，经 ASGI 同步→异步桥接调用真实 gateway G4（G4-only，不执行）
- `live`：真实 DeepSeek V4 Pro LLM，经真实 gateway G4；402/403/429 立即 StopRun

**基础设施**：
- 真实 tmpfs PostgreSQL（隔离 bank + agent 双库，`GATEWAY_DB_TESTS=1 OBLIGATIONS_DB_TESTS=1`）
- 真实 bank-backend（uvicorn in-process）
- 真实 gateway（ASGI in-process）  
- 真实 L1 graph（`services/agent/`）

**密钥安全**：
- `_strip_secrets()`：递归清除键名含 token/key/secret/password/pin/bearer/jwt/authorization/credential 的字段（值写 `[REDACTED]`），清除 Bearer 前缀字符串和 JWT 形字符串，tuple/set/frozenset 递归，深度上限占位符
- 哨兵泄漏测试：`DEEPSEEK_API_KEY` 哨兵不得出现在 stderr、stdout 或任何输出文件中
- `cases.jsonl` 中 `plan_draft`/`g4_attempts` 写入前经 `_strip_secrets`
- `usage_known=False` 时 `tokens` 字段为 0

**证据字段清单**（每 CaseRun）：
`utterances`、`plan_draft`、`g4_attempts`、`g4_decision`、`g4_findings`、`usage_known`、`usage`（向后兼容，旧字段保持）

## Make 目标

```bash
make test-e0b   # E0b 专项测试（真实 PG + gateway + L3）
```

## 命令

```bash
# Fake 模式（G4-only）
python -m evaluation.runner.cli --llm-mode fake --limit 10 --repeat 3 --out /tmp/e0b-out

# Live 模式（真实 DeepSeek，少量样本先冒烟）
python -m evaluation.runner.cli --llm-mode live --limit 5 --repeat 1 --out /tmp/e0b-out-live
```

## 实际数字（2026-10-27，HEAD 60f642b）

| 命令 | 结果 | 备注 |
|---|---|---|
| `make test-e0b` | 119 passed, 0 skipped, 0 failed | 真实 PG/G4/L3，15.45s |
| `make lint` | 388 files already formatted, All checks passed | — |
| `make test` | — | 未单独跑（E0b 不覆盖全量） |

## 已知限制

1. **live 结果依赖 L2 G3 原话解析覆盖**：当前 L2 的 W1 意图解析仅识别「给X转N」等固定句式，其余原话（中文金额、「把N转给X」、卡渠道开关、新增收款人）全部 `INTENT_UNCLEAR → FORM_REQUIRED`，导致大量 live 样本结果为 CLARIFY 而非预期 outcome。这是 L2 能力缺口，E0b 如实记录，不修改 L2。
2. **replay 模式未接入**：当前仅 fake/live 两种 LLM 模式。
3. **tokens 用量未知**：`usage_known=False` 时 tokens=0；后续接真实 LLM 请求收集 usage 后才可填充。
4. **G4-only**：runner 不调用 G8（执行），仅评估；因此黄级审批用例无法完成闭环，只到达 G4 REQURE_APPROVAL。
5. **FORM_REQUIRED 澄清**：L2 对「hello」「hi」等无写意图原话自动创建 FORM_REQUIRED 澄清，L1 经 G21 取回，结果记为 CLARIFY。这是系统正常行为，与语料中期望 NO_PLAN 的样本不同，作为 E0 发现报告。

## 输出格式

- `metrics.json`：`{meta, totals, by_category, by_risk, ...}`
- `report.md`：Markdown 可读表格
- `cases.jsonl`：每行为一个 CaseRun（含 evidence 字段，plan_draft/g4_attempts 已脱敏）