# SafeBank Gateway

2026 深圳国际金融科技大赛银行 AI 智能体。现行规格为 [PRD.md](PRD.md)，接手先读 [AGENTS.md](AGENTS.md)。

当前阶段：P1 已完成本地验收。L3 已实现真实 PostgreSQL 账本、转账、收款人、卡片、快照与原子执行/恢复协议；L1/L2/前端仍为 P0 骨架。按组件逐项验收，记录见 AGENTS.md。

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

唯一宿主入口为 http://127.0.0.1:3000/health，前端探针仍标注 P0-skeleton。银行接口仅在 bank-net，通过服务凭证访问；启动自动迁移并加载独立 P1 核心测试数据，不代表 PRD 正式演示种子。
`make up` 从 uv.lock 下载并校验当前架构的 Linux wheel 后离线构建，避免构建内 DNS 依赖；下载仍需要宿主网络。
服务间访问由 Compose DNS 与两个隔离网络提供；gateway 两个应用分别绑定对应网络地址。
`make down` 停容器保留卷；`make logs` 查看服务日志。

## 独立验收

- `make test`：契约、账本、转账/卡规则和原 P0 单元回归；没有真实 PG 配置的集成项显示 skipped。
- `make test-p1`：从空独立测试库迁移、重复升级、全部真实 PostgreSQL 验收。
- `make test-p1-concurrency` / `make test-p1-consistency`：独立并发与预演一致性门禁；先运行 test-p1。
- `make contracts-check`：冻结契约 v1 漂移检测。
- `make test-integration`：真实容器网络/服务凭证/数据库 CONNECT 角色隔离，以及 HTTP 转账、L3 进程重启与幂等恢复。
- `make probe`：330次中的原始300次E1–E6探针；**调用真实模型并收费**。E2回退独立重跑：`.venv/bin/python -m scripts.deepseek_probe --only E2-fallback`。
- 证据与限制：[P0验收](docs/acceptance/P0.md)、[LLM决策](docs/decisions/ADR-002-llm.md)、[官方要求](docs/acceptance/official-v1.md)。

当前采用 DeepSeek V4 Pro、显式 beta、strict、关闭思考；开启思考+指定tool_choice被实测拒绝。
`make demo/eval/reset` 在 P1 仍明确失败，等待后续阶段。后续按 PRD 的组件依赖顺序实现并独立验收。

密钥只在 .env/.secrets，不进Git或镜像。`make keys`不覆盖已有凭证；发现半生成目录会拒绝继续，需先人工核对。不要删除已有数据库卷来掩盖初始化问题。

L3 路由、环境变量、启动恢复与范围见 [后端说明](services/bank-backend/README.md)。CI 已接入单元、真实 PG、并发、一致性、网络和契约门禁；远端执行状态以 GitHub 实际运行结果为准。
