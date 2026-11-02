# L1 稳定性评测

本目录包含 405 条输入语料、可连接真实网关与 L3 的评测驱动，以及指标计算与脱敏报告输出。

## 语料

[`corpus/l1_stability/`](corpus/l1_stability/) 的 11 个 JSONL 文件覆盖转账、收款人、卡限额、渠道、冻结、解冻、挂失、越界与 A1–A3 攻击输入。用例包含期望决策、风险、关键参数或澄清结果。405 条为语料规模，完整 live 通过率须由实际报告提供。

## 运行

在仓库根目录执行 `make setup`，并确保 Docker 后台可用。默认不调用真实模型：

```bash
make eval-l1 LIMIT=20
# 等价于：
.venv/bin/python -m evaluation.runner.cli --llm-mode fake --limit 20
```

fake 模式只替换 LLM 端口，使用真实网关、L3 和临时 PostgreSQL。它验证评测与决策链路，不代表真实模型的语义能力。每个用例默认重复 3 次。

live 模式使用 DeepSeek V4 Pro，会产生实际费用；先在进程环境中设置 `DEEPSEEK_API_KEY`：

```bash
.venv/bin/python -m evaluation.runner.cli \
  --llm-mode live --limit 5 --repeat 1 --max-requests 60
```

可用 `--sample-ids` 选择特定用例，或 `--filter` 按用例 ID / 类别筛选。参数以 `--help` 为准。live 驱动限制请求数，额度或权限错误会停止，不进行无限重试。

报告默认写入 `evaluation/reports/<UTC时间>/`，可用 `--out` 指定目录。输出实现见 [`metrics/compute.py`](metrics/compute.py)，包含逐次结果、聚合指标、运行元数据和可读报告；分享前核查脱敏结果。

## 范围与限制

当前稳定性驱动主要验证理解、计划、来源和 G4 决策，不把「到达审批」计作真实资金执行。端到端业务结果仍需单独检查 L3 状态，已有案例见[验证说明](../docs/validation.md)。

CLI 接受 `replay` 参数，但该模式尚未接通；Agent 的回放组件与专项测试不能替代整套评测回放。`make eval` 和 `make reset` 仍是明确失败的占位目标。独立的 `make demo` 已接入 `scripts.demo_runner`，默认使用 replay，要求录制文件、隔离网络与 SQL 回执；详见[驱动验收](../docs/acceptance/demo-runner.md)。

测试：`make test-e0b` 启用真实临时 PostgreSQL；纯指标与语料测试可通过 `make test` 运行。历史组件验收见 [E0b 记录](../docs/acceptance/E0b-runner.md)。
