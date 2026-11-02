# 启动与部署

项目提供独立界面预览和完整演示栈。预览适合快速了解产品；完整栈使用真实服务、PostgreSQL 与 DeepSeek API，所有账户和业务数据仍是合成数据。

## 1. 环境准备

| 工具 | 要求 |
|---|---|
| Python | 3.12，`pyproject.toml` 限定为 `>=3.12,<3.13` |
| uv | 能执行 `uv sync --frozen`；CI 固定为 0.12.23 |
| Docker | 运行中的后台引擎与 Compose v2；首次需拉取 PostgreSQL、Python 和 Node 基础镜像 |
| Node.js | 本地预览使用 22；Docker 前端镜像内置 Node 22 |
| 网络 | 首次安装与镜像构建需要网络；真实助手请求需要访问 DeepSeek |

以下命令按 macOS / Linux 的 Bash 或 Zsh 编写。Compose 使用固定的 `172.29.10.0/24` 和 `172.29.20.0/24` 网段，同一 Docker 引擎下避免同时启动网段冲突的副本。

```bash
git clone https://github.com/SJeffZhang/Safebank.git
cd Safebank
```

## 2. 界面预览

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。默认 `VITE_BANK_MODE=preview`，无需 BFF、Docker 或 API key。预览 PIN 与 OTP 为 `123456`，刷新恢复初始合成状态。小安提供说明性回复；预览不会调用模型，也不会访问银行后端。

停止开发服务器按 `Ctrl+C`。

## 3. 完整演示栈

### 配置与启动

回到仓库根目录。首次复制配置；已有 `.env` 时直接编辑，保留原配置。

```bash
cp .env.example .env
# 用本地编辑器填写 DEEPSEEK_API_KEY
make setup
make keys
export COMPOSE_FILE=docker-compose.yml:docker-compose.demo.yml
docker compose config --quiet
make up
make seed-demo
```

`make up` 下载并校验锁定依赖的 Linux wheel、构建镜像、启动数据库、执行迁移和基础种子安装，再启动业务服务。`make seed-demo` 增量安装张三、李四、孙八、王五及其合成历史数据，保留已有客户与交易。它不把已使用的数据恢复到初始状态。

[`docker-compose.demo.yml`](../docker-compose.demo.yml) 补充三项配置：只向 L1 注入 `DEEPSEEK_API_KEY`，在 L2/L3 开启 `DEMO_MODE`，保持完整 B2 策略。默认 Compose 不向所有服务全局加载 `.env`。在新的终端运行管理命令前，重新执行上面的 `export COMPOSE_FILE=...`。

模型适配固定使用 `deepseek-v4-pro`、`/beta`、结构化工具调用、关闭思考模式和自动重试。选择依据见 [ADR-002](decisions/ADR-002-llm.md)；实际请求配置见 [`client.py`](../services/agent/app/llm/client.py)。

### 首次初始化既有定时与代扣授权

如需演示种子中的定时转账、商户代扣和时钟推进，在**首次启动、尚未登录或产生任何网关历史**时执行：

```bash
set -o pipefail
docker compose run --rm -T bank-maintenance python -m app.seed_consents \
  | docker compose run --rm -T gateway-maintenance \
      python -m services.gateway.app.seed_grants
```

这是本地维护步骤：导出器核对初始模拟时间和固定业务条款，安装器只接受空网关库，并写入 `SEED_CONSENT_INSTALL` 审计。没有这一步，种子到期事件缺少授权会被拦截。已有会话、计划或审计时安装器会拒绝；不要清空账本或伪造审批来绕过该检查。

### 登录与健康检查

```bash
docker compose ps
curl --fail http://127.0.0.1:3000/health
```

打开 [http://127.0.0.1:3000](http://127.0.0.1:3000)。演示登录页可选择合成客户；在本机用编辑器查看 `.secrets/demo_pins` 获取对应随机 PIN。完整栈不使用预览 PIN `123456`。

红级操作先在确认面板发起验证码，再通过演示控制台的模拟短信查看。控制台单独登录，口令为本机 `.secrets/gateway.env` 中的 `DEMO_CONSOLE_TOKEN`。口令与验证码均不应粘贴进 Issue 或公开日志。验证码绑定具体审批，已消费或过期后不能复用。

`/health` 与容器 healthy 表示服务就绪；业务验收还需检查计划、审批和 L3 的实际执行结果。

### 命令行体验

```bash
make try-agent ARGS='--help'
make try-agent ARGS='--customer-id cus_zs --text 给王五转200元'
```

具体参数以 `--help` 为准。需要实际确认执行时，按 CLI 提示输入 PIN 并核对审批内容；它会改变本地合成数据。CLI 使用业务状态退出码，`CONFIRMED` 对应退出码 9，不能简单地把所有非零退出码都当成基础设施失败。

### 停止与再次启动

```bash
make logs
make down
```

`make down` 保留数据库卷。再次执行 `make up` 会运行迁移并保留已有业务数据；不要用 `docker compose down -v` 作为常规排错手段。`make keys` 也会保留完整的已有凭证，发现半生成目录时拒绝覆盖。

## 4. 数据重置与演示代际

`make reset` 和 `make eval` 仍为明确失败的旧占位命令。`make demo` 已接入专用驱动，默认 `LLM_MODE=replay`，需要预先准备录制、隔离部署、离线网络和匹配的 SQL 回执，不会自动重置数据库。没有这些前置条件时会拒绝运行；详见[驱动验收](acceptance/demo-runner.md)。

现有 `make demo-reset` / `make demo-up` 面向维护者的隔离演示检出，需要本机 `e2e.override.yml` 和配套秘密文件，不是全新 clone 的通用启动命令。代际机制会创建新的 bank / gateway / agent 数据库、保留旧库与历史镜像，在切换失败时恢复上一代。实现和已测范围见[代际验收](acceptance/demo-generation.md)。

全新 clone 按本指南第 3 节初始化即可。反复彩排前，应由维护者准备独立数据代际；不要直接删除历史账本重装。

## 5. 本地前端连接完整栈

完整 Docker 部署已经包含构建后的 App 与同站 BFF，无需另外启动 Vite。若要修改界面并连接内部服务，使用 `agent-net` 上的 BFF 或专门的开发网络配置；基础 Compose 没有向宿主暴露 `8100/8200/8300`。

`frontend/.env.example` 中的地址是 BFF 进程视角的上游地址，默认 localhost 只有在开发者已提供相应端口转发时才可达。不得把服务令牌放进 `VITE_*` 变量。真实模式的接口失败会直接显示错误，不回退到预览数据。

## 6. 服务器与 HTTPS

演示栈默认仅监听宿主回环地址。部署到服务器时，在 BFF 前放置 HTTPS 反向代理，并通过本地 Compose override 设置：

```yaml
services:
  frontend:
    environment:
      PUBLIC_ORIGIN: https://bank.example.org
      COOKIE_SECURE: 'true'
```

将示例域名换成实际地址，代理所有路径到 BFF，允许 SSE 长连接并关闭响应缓冲。网关、银行后端和数据库继续保持内部端口。证书、域名、防火墙、备份与监控由部署者配置；本仓库没有生产高可用验收。

BFF 会话目前在内存中，重启后需要重新登录，不适合直接启动多个实例。演示接口只应用于受控合成环境，详细边界见 [SECURITY.md](../SECURITY.md)。

## 7. 常见问题

| 现象 | 检查位置 |
|---|---|
| Compose 提示模型密钥缺失 | 根目录 `.env` 是否填写 `DEEPSEEK_API_KEY`；是否启用演示配置 |
| Agent 无法启动或助手返回上游错误 | `docker compose logs --tail=100 agent`；确认模型账户可用与网络可达 |
| 登录页取不到演示客户 | 运行 `make seed-demo`，确认网关开启 `DEMO_MODE=true` |
| PIN 错误 | 使用当前 `.secrets/demo_pins` 中对应客户的值，不用预览 PIN |
| POST 返回 `CSRF_REJECTED` | 浏览器地址必须与 `PUBLIC_ORIGIN` 完全一致，包括协议、主机名与端口 |
| `/internal/*` 从浏览器访问不到 | 这些路由仅供银行内部网络调用；从 App / BFF 进入业务 |
| 定时或代扣被拦截 | 检查既有授权是否初始化、是否撤销，以及审批与事件对应的审计理由 |
| 提示 `Incomplete secrets directory` | 核查本地半生成凭证，保留备份；不要在运行中删除并重建整套密钥 |
| 网段冲突 | 检查已有 Docker 网络和并行部署，避免启动同网段副本 |

真实模型批量驱动收到 402 / 403 / 429 会停止，避免继续消耗额度；不应对额度或权限错误无限重试。分享故障日志前先去除密钥、令牌、PIN、OTP 与数据库连接串。
