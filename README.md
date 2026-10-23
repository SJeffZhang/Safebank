# SafeBank Gateway

2026 深圳国际金融科技大赛银行 AI 智能体。现行规格为 [PRD.md](PRD.md)，接手先读 [AGENTS.md](AGENTS.md)。

当前阶段：P0。提供经过独立验收的环境与进程骨架、两网隔离、DB角色、密钥生成及 DeepSeek POC；尚未实现银行业务。

## 本地启动

需要 Python 3.12、uv、Docker CLI 和运行中的后台 Docker 引擎。无需打开 Desktop 窗口。

```sh
cp .env.example .env   # 本地填 DEEPSEEK_API_KEY，只用于 POC
make setup
make keys
make test
make lint
make up
make test-integration
```

唯一宿主入口为 http://127.0.0.1:3000/health，响应明确标注 P0-skeleton。
`make up` 从 uv.lock 下载并校验当前架构的 Linux wheel 后离线构建，避免构建内 DNS 依赖；下载仍需要宿主网络。
服务间访问由 Compose DNS 与两个隔离网络提供；gateway 两个应用分别绑定对应网络地址。
`make down` 停容器保留卷；`make logs` 查看服务日志。

## 独立验收

- `make test`：服务路由/凭证、密钥、拓扑、探针Schema、LangGraph中断恢复与runtime context不持久化。
- `make test-integration`：真实容器网络/服务凭证/数据库CONNECT角色隔离。
- `make probe`：330次中的原始300次E1–E6探针；**调用真实模型并收费**。E2回退独立重跑：`.venv/bin/python -m scripts.deepseek_probe --only E2-fallback`。
- 证据与限制：[P0验收](docs/acceptance/P0.md)、[LLM决策](docs/decisions/ADR-002-llm.md)、[官方要求](docs/acceptance/official-v1.md)。

当前采用 DeepSeek V4 Pro、显式 beta、strict、关闭思考；开启思考+指定tool_choice被实测拒绝。
`make demo/eval/reset` 在P0明确失败，不返回模拟成功。后续按 PRD 的组件依赖顺序实现并独立验收。

密钥只在 .env/.secrets，不进Git或镜像。`make keys`不覆盖已有凭证；发现半生成目录会拒绝继续，需先人工核对。不要删除已有数据库卷来掩盖初始化问题。
