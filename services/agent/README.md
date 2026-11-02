# L1 · Banking Agent

银行助手「小安」使用 LangGraph 完成意图理解、只读信息收集、结构化规划、审批等待与执行恢复。L1 通过 L2 调用所有业务工具，不持有银行后端写入凭证。

## 处理流程

```text
ingest → understand → gather → plan → evaluate
                                   ├─ ALLOW → execute
                                   ├─ REQUIRE_APPROVAL → interrupt → resume → execute
                                   └─ DENY / REPLAN → respond
                                                   ↓
                                         verify_output → END
```

网关返回的业务拒绝、状态变化、未知结果与依赖故障有独立路由。模型输出通过检查后再回复；不能根据模型措辞判断资金是否已执行。

`AgentState` 保存业务过程，助手令牌只进入运行时 `AgentContext`，不写检查点。`AsyncPostgresSaver` 将会话持久化；线程锁避免同一会话并发运行。新输入与恢复分别经过受控入口，防止复用旧原话或跨客户状态。

## 模型与工具

当前适配固定使用 DeepSeek V4 Pro、显式 `/beta`、关闭思考、`temperature=0` 和 `max_retries=0`。理解与计划使用结构化工具；信息收集只绑定 18 个读工具。模型提供参数来源，身份、会话和请求字段由代码填写。

生产模型工厂支持 `LLM_MODE=live|record|replay`；回放只替换模型响应，不替代 L2 授权。缺失、损坏或不匹配的录制会拒绝继续，不回退到真实模型。接入与检查范围见[回放验收](../../docs/acceptance/runtime-llm-replay.md)。

工具自由文本用不可信标记包裹，输出端检查金额与敏感内容。模型是规划者，授权结果完全由网关返回。

## 启动与配置

推荐从根目录按[部署指南](../../docs/deployment.md)启动。演示 Compose 将 API key 仅注入本服务；服务在内部网络监听 8100，不向宿主发布。

| 变量 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | 模型 API key，live / record 必需；replay 不加载密钥 |
| `LLM_MODE` | `live`（默认）、`record` 或 `replay` |
| `LLM_PROFILE` / `LLM_CASE_ID` / `LLM_REPLAY_ROOT` | 录制与回放的策略、用例和持久化目录 |
| `AGENT_SERVICE_TOKEN` | 以 A 身份调用网关 |
| `GATEWAY_URL` | 网关公共接口，Compose 内为 `http://gateway:8200` |
| `AGENT_DATABASE_URL` | 助手检查点数据库 |
| `JWT_PUBLIC_KEY_PATH` | 验证助手身份上下文的 Ed25519 公钥 |

HTTP 入口为 `POST /threads`、`GET /threads/{id}/state`、`POST /threads/{id}/runs/stream`。BFF 负责浏览器会话和 SSE 适配，浏览器不直接连接 L1。

## 代码与测试

| 目录 | 内容 |
|---|---|
| `app/graph/` | 状态、节点、路由、输入与恢复 |
| `app/llm/` | DeepSeek 适配、规划与收集、输出检查、回放组件 |
| `app/gateway/` | L2 HTTP 协议、错误与图端口适配 |
| `app/main.py` | FastAPI、PostgreSQL 检查点和 SSE 服务 |

```bash
# 仓库根目录
.venv/bin/pytest tests/agent -q
make test-agent-db
make test-replay
make test-verify
make test-cards
```

数据库目标使用独立临时 PostgreSQL，需要 Docker。真实模型测试与评测会产生 API 费用；稳定性语料与驱动见 [`evaluation/`](../../evaluation/README.md)，真实业务证据见[验证说明](../../docs/validation.md)。
