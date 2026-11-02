# BankSense 技术文档

第一次浏览项目，建议从[仓库首页](../README.md)了解业务与设计，再按用途选择文档。

## 运行与验证

| 文档 | 内容 |
|---|---|
| [部署指南](deployment.md) | 界面预览、完整演示栈、初始化授权、登录与 OTP、HTTPS 和常见故障 |
| [验证说明](validation.md) | 本次实测指标、历史数据库证据、测试命令与统计口径 |
| [前端说明](../frontend/README.md) | App、BFF、本地联调和构建 |
| [评测说明](../evaluation/README.md) | 405 条语料、fake / live 驱动、结果输出与限制 |
| [安全边界](../SECURITY.md) | 信任模型、凭证隔离和演示环境的使用范围 |

## 设计与代码

| 文档 | 内容 |
|---|---|
| [架构说明](architecture.md) | 三层职责、评估流水线、审批、执行恢复和到期业务 |
| [PRD 与接口规格](../PRD.md) | 六场景需求、工具与服务接口、规则代码、评测设计；接口集中在 §9 |
| [共享契约](../packages/contracts/README.md) | 工具参数与结果、计划、状态和执行协议 |
| [金融领域内核](../packages/bankcore/README.md) | 预演与执行共用的纯函数与账本规则 |
| [L1 助手](../services/agent/README.md) | LangGraph、模型适配、检查点、HTTP / SSE |
| [L2 安全网关](../services/gateway/README.md) | 确定性规则、来源检查、审批和审计 |
| [L3 银行后端](../services/bank-backend/README.md) | 状态加载、原子执行、账本与调度 |
| [架构决策](decisions/) | 模型选择、执行身份、状态变化屏障、到期事件等 ADR |
| [第三方依赖](third-party.md) | 已引入依赖的版本与许可证核对记录 |

## 证据与历史

`acceptance/` 保存组件验收和真实服务证据，记录各自的提交、数据集及未覆盖项；同一套件在不同阶段反复执行的数字不能累加为总测试数。

`reviews/`、`progress/`、`plans/` 和 `handoffs/` 保存开发评审、阶段计划与交接历史。其内容反映记录时的状态，判断当前可用范围时应同时核对代码与较新的验收记录。`archive/` 为已被替代的规格草稿。

开发维护约定与追加日志见 [AGENTS.md](../AGENTS.md)。它是维护入口，项目介绍与复现步骤以首页及本导航所列文档为入口。
