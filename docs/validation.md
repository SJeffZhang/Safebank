# 验证结果与指标口径

SafeBank 将自动化测试、真实模型链路和安全效果评测分开记录。界面截图说明产品交互；计划状态、授权结果、资金变化与攻击成败，以 L3 数据库和相应审计记录判断。

## 本次回归

2026-11-02，在代码基线 `f324c52`、分支 `codex/repository-presentation` 上执行：

| 命令 | 结果 | 说明 |
|---|---|---|
| `make test` | 4,977 passed / 1,169 skipped | 35.90 秒；条件跳过项没有计为通过 |
| `cd frontend && npm test` | 494 passed，50 个文件 | 13.73 秒；包含 App 与既有页面回归 |
| `cd frontend && npm run test:bff` | 16 passed / 0 skipped | 本地测试上游，检查 BFF HTTP 边界 |
| `cd frontend && npm run build` | 通过 | TypeScript 与 Vite 构建 |

耗时仅为这台开发机本轮测试时间，不是业务响应延迟或吞吐基准。不同套件可能覆盖相同路径，不能把阶段数字累加为独立场景数。本轮未调用真实模型、未重启既有服务、未重跑完整真实 PostgreSQL CI。

本次仓库整理的检查记录见 [2026-11-02 记录](acceptance/repository-2026-11-02.md)。

## 结构性指标

| 项目 | 当前范围 | 来源 |
|---|---|---|
| 工具契约 | 18 个读工具、14 个写工具 | [`tools.py`](../packages/contracts/src/safebank_contracts/tools.py)、[冻结快照](contracts/v1.json) |
| L1 规划工具 | 13 个写工具 | [`schema.py`](../packages/contracts/src/safebank_contracts/schema.py)；风险问卷只能从 App 表单提交 |
| 稳定性语料 | 405 条、11 类、11 个 JSONL 文件 | [`l1_stability/`](../evaluation/corpus/l1_stability/) |
| 精度 | 整数分；四位 Decimal 份额 | [`base.py`](../packages/contracts/src/safebank_contracts/base.py) |
| 网络隔离 | agent-net 与内部 bank-net | [`docker-compose.yml`](../docker-compose.yml) |

405 是用例总数，不代表 405 条全量 live 已通过。语料分类覆盖转账、收款人、卡限额、渠道、冻结、解冻、挂失、越界输入和 A1 / A2 / A3 攻击，不等同于六场景全部细分需求的覆盖率。

## 真实服务证据

以下均为仓库内已存在的验收记录。本轮核对了记录内容，未重新发起这些模型请求；每份 JSON 中的 candidate / generation 标识对应各自当时的代码和数据集。

| 场景 | 可直接检查的结果 | 记录 |
|---|---|---|
| 中文定时转账来源 | 每月向赵六转 20,000 分；两次成功共 40,000 分；来源计划已提交、已执行且为黄级；无重复，账本平衡 | [JSON](acceptance/schedule-provenance-live.json) / [说明](acceptance/schedule-provenance.md) |
| 闲置订阅转储蓄 | 仅 gym 被取消，其他三条订阅保持 ACTIVE；定期向本人储蓄转 19,900 分，两期共 39,800 分；原扣费两期 BLOCKED | [JSON](acceptance/idle-subscription-savings-live.json) / [说明](acceptance/idle-subscription-savings.md) |
| 红级挂失 | 卡状态 LOST，无补卡；审批 USED / RED；OTP 已确认；一次业务操作、零转账、账本平衡 | [JSON](acceptance/lost-card-source-live.json) / [说明](acceptance/lost-card-source.md) |
| 模拟时钟与基金申购 | 推进至 12 月 6 日；2,000 元订单 SETTLED、在途冻结归零；16 个终态事件 / 16 条终态审计，14 SUCCESS / 2 BLOCKED | [JSON](acceptance/demo-clock-live.json) / [说明](acceptance/demo-clock.md) |
| 基金赎回 | 货币基金 L1 赎回 1,000 份，已入账 100,000 分；非货币基金表单赎回 100 份，经时钟结算入账 12,311 分，各一笔付款且账本平衡 | [JSON](acceptance/redemption-sources-live.json) / [说明](acceptance/redemption-sources.md) |
| 表单授权生命周期 | F/app 路径完成 20,000 分转账与绿色冻结；两计划 EXECUTED，claims CONSUMED；重复请求未重复写，零伪造原话 | [JSON](acceptance/form-authorization-live.json) / [说明](acceptance/form-authorization.md) |
| 数据代际与失败回滚 | 新旧数据库并存、初始摘要一致、旧会话失效；切换故障后恢复旧运行环境 | [说明](acceptance/demo-generation.md) |

表单记录与其他组件证据均保留当时的代码和合并门禁状态；对应实现现已随主线集成。单组件通过不等于比赛演示整体冻结。

## 分层复现

先按[部署指南](deployment.md)安装依赖。单元与构建检查：

```bash
make test
make lint
make contracts-check
cd frontend
npm ci
npm test
npm run test:bff
npm run build
```

需要 Docker 的专项测试使用真实临时 PostgreSQL，例如：

```bash
make test-gateway-db
make test-agent-db
make test-w1
make test-w2
make test-w3b
make test-w4c
make test-obligations
make test-s1
```

临时库专项与完整部署检查的运行条件不同。全套后端 CI 步骤见 [`checks.yml`](../.github/workflows/checks.yml)，前端见 [`frontend.yml`](../.github/workflows/frontend.yml)。`make test-p1` 创建独立测试库；后续 `test-p1-concurrency` 和 `test-p1-consistency` 分别检查并发与预演/持久化一致性。

`make test-integration` 检查真实网络、服务凭证、数据库角色、HTTP 执行及进程重启恢复，会改变测试客户状态并重启 L3。应在专用部署检出串行执行，避免在正在彩排的共享栈上运行。

## 安全效果如何计算

评测设计在 [PRD §10](../PRD.md) 中定义，当前 runner 的范围见[评测说明](../evaluation/README.md)。

- **任务完成率（TSR）**：按任务期望检查最终业务状态。到达审批、模型回复「成功」和 G4 放行都不等于任务已执行。
- **攻击成功率（ASR）**：按攻击对应的 L3 状态断言判断。模型输出攻击内容与真实资金流出必须分别计量。
- **安全机制对照**：完整 B2 与单步检查 B1、消融配置分别运行；基线只允许在 EVAL 模式启用。分开报告网关层与端到端结果。
- **性能**：应分别报告模型耗时、网关耗时、数据库执行耗时与样本规模；不把本机 pytest 总耗时作为线上延迟。

## 尚未发布的结果

完整六场景两轮新数据集 live、全量攻击 ASR / 正常任务 TSR、B1 / B2 对照与延迟分位数仍待最终报告。生产模型适配已接入 record / replay，专用 `make demo` 驱动检查录制、离线网络与 SQL 回执；评测 CLI 的 replay 模式仍未接通。完整断网演示尚需真实录制和数据库业务状态验收。
