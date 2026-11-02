# BankSense Gateway

2026 深圳国际金融科技大赛银行 AI 智能体。现行规格为 [PRD.md](PRD.md)，接手先读 [AGENTS.md](AGENTS.md)。

BankSense 采用 L1 LangGraph 银行助手、L2 确定性安全网关、L3 FastAPI + PostgreSQL 银行沙箱三层架构，覆盖转账、账单分析、卡片、订阅、理财与跨场景联动。竖版 App 与同站 BFF 位于 `frontend/`。当前组件实现与验收记录见 [AGENTS.md](AGENTS.md)，完整现场验收和冻结状态以其中记录为准。

## 本地启动

需要 Python 3.12、uv、Docker CLI 和运行中的后台 Docker 引擎。无需打开 Desktop 窗口。

```sh
cp .env.example .env   # 本地填 DEEPSEEK_API_KEY，只用于 POC
make setup
make keys
make test
make lint
make up
make test-p1
make test-integration
make contracts-check
```

浏览器入口为 http://127.0.0.1:3000/ ，健康检查为 `/health`。客户登录使用本机演示 PIN；短信验证码由登录后的演示控制台读取，不能使用预览模式的固定码。银行接口仅在 bank-net，通过服务凭证访问。`make up` 自动迁移并加载 P1 核心数据，不等于恢复正式演示初始状态。
`make up` 从 uv.lock 下载并校验当前架构的 Linux wheel 后离线构建，避免构建内 DNS 依赖；下载仍需要宿主网络。
服务间访问由 Compose DNS 与两个隔离网络提供；gateway 两个应用分别绑定对应网络地址。
`make down` 停容器保留卷；`make logs` 查看服务日志。

## 独立验收

- `make test`：契约、账本、转账/卡规则和原 P0 单元回归；没有真实 PG 配置的集成项显示 skipped。
- `make test-postgres-bootstrap`：用可清理的独立Linux卷复现旧权限错误并验证双库首次初始化、私有权限和重启保留数据。
- `make test-p1`：从空独立测试库迁移、重复升级、全部真实 PostgreSQL 验收。
- `make test-p1-concurrency` / `make test-p1-consistency`：独立并发与预演一致性门禁；先运行 test-p1。
- `make contracts-check`：冻结契约 v1 漂移检测。
- `make test-integration`：真实容器网络/服务凭证/数据库 CONNECT 角色隔离，以及 HTTP 转账、L3 进程重启与幂等恢复。
- `make probe`：330次中的原始300次E1–E6探针；**调用真实模型并收费**。E2回退独立重跑：`.venv/bin/python -m scripts.deepseek_probe --only E2-fallback`。
- 证据与限制：[P0验收](docs/acceptance/P0.md)、[LLM决策](docs/decisions/ADR-002-llm.md)、[官方要求](docs/acceptance/official-v1.md)。

当前采用 DeepSeek V4 Pro、显式 beta、strict、关闭思考；开启思考+指定tool_choice被实测拒绝。
`make demo` 已接入演示 runner，默认使用回放；录制和 live 模式需显式选择。新数据代际初始化使用 `make demo-reset`，启动当前演示代际使用 `make demo-up`；这些管理命令要求已配置的隔离部署目录，详见 [演示运行指南](docs/acceptance/demo-runner.md)。`make eval` 和通用 `make reset` 仍为未实现占位，不作为可用入口。

密钥只在 .env/.secrets，不进Git或镜像。`make keys`不覆盖已有凭证；发现半生成目录会拒绝继续，需先人工核对。不要删除已有数据库卷来掩盖初始化问题。

L3 路由、环境变量、启动恢复与范围见 [后端说明](services/bank-backend/README.md)。CI 已接入单元、真实 PG、并发、一致性、网络和契约门禁；远端执行状态以 GitHub 实际运行结果为准。

PostgreSQL 初始化 SQL 保持宿主0600，通过启动适配脚本复制到容器私有tmpfs，设为postgres所有后交给官方entrypoint。不能把SQL放宽为全员可读；运行时副本不进入镜像，已有数据卷不会自动清空。

## 项目名称

项目对外名称统一为 **BankSense**，用于 App、浏览器标题、导出文件、服务说明和当前文档。现有 `safebank_*` Python 包、Compose 项目/卷、数据库角色、配置变量、HTTP 协议头和本地存储键属于兼容标识，沿用现有值以保留数据和服务互通。GitHub 链接使用真实仓库地址；原始实测、评审证据及历史规格保留记录时的名称和哈希。

## 演示初始化数据

`make demo-reset ARGS="--deploy /path/to/deploy"` 在既有 PostgreSQL 内创建新的独立代际库，并安装 `banksense_showcase_v1`：六款理财产品、张三四项订阅（月265元/年3180元），以及尾号1234/5678/9012的三张卡。新增备用活期为零余额，原正式种子余额不变；基准模拟时间2026-10-15 09:00下，未来7日两笔扣费共45元。三张卡沿用石墨、银色、香槟金样式。新库内通过原有种子授权导出/空网关库安装流程绑定订阅授权，运行服务不获数据库管理权限。旧库、卷和历史实测记录保留。`demo-up` 只启动既有代际，不重新补种。
