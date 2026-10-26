> 已作废的DS T0草稿，仅保留历史。协调者已核实其中生产respond/verify未接入、agent.env不存在、80万元黄级等判断错误；不得据此实施。当前以docs/plans/P2-remaining-2026-10-26.md及AGENTS最新日志为准。

# P2 剩余缺口与 try-agent T0 设计（2026-10-26）

> 基线 main 00aca15（PR #31 刚合入 W4c）。本文仅 T0 只读缺口分析，不实施业务。

## 1. P2 验收清单 vs 现有证据

| # | P2 验收项 | 已有证据 | 缺口 |
|---|---|---|---|
| 1 | 真 L1→L2→L3 黄级转账闭环 | W4c test-w4c 38 项 freeze_card/update_limits ALLOW+CONFIRMED→G8 真实 L3 执行；L1 test_service.py 用 FakePorts，未接真实 L2 | L1 图从未调真实 G4/G8；无「转 80 万→REQUIRE_APPROVAL→确认→执行→L3 金额变化」端到端测试 |
| 2 | 确认面板不经 LLM | G9/G6 均已实现（W4b2b、W4b2c），✓ | L1 回复中不引用 panel 敏感字段留待 C1e 验证 |
| 3 | 双网可达 + 凭证隔离 | test_service.py D9/D10 已验证 agent 源码无 GATEWAY_SERVICE_TOKEN、生产 AGENT_SERVICE_TOKEN 守卫 | try-agent 全栈时实际网络验证未做 |
| 4 | PG saver 重启 resume | test_service.py `test_interrupt_survives_graph_rebuild` + `test_resume_works_after_rebuild` ✓ | 均用 FakePorts，真 G4 中断恢复未测 |
| 5 | 失败路由 STATE_CHANGED/UNKNOWN | W4c `test_g8_fence.py` 屏障 + `test_g8_unknown.py`；C1c2 `routing.py` 路由表 | L1 图对真实 STATE_CHANGED 的端到端处理未验 |
| 6 | 金额输出/掩码/卡片 | C1e verify.py 掩码单测 ✓；cards.py 卡片生成 ✓ | 生产 `verify_output` 端口用 `return Done` 假实现（main.py:217–225）；C1e ReplayLLM 未接 |
| 7 | ADR-004 录制/回放验收 | replay.py RecordTransport/ReplayLoader 代码存在 ✓ | **生产 `_build_production_ports` 直接调 `make_llm()` 无任何 LLM_MODE 判断；RecordTransport 从未被注入**；ADR-004 第 3 条「回放模式下缺录制直接报错」无法验证 |
| 8 | main 组合回归 + DS 独审 | ✓ 每次 PR 前做 | P2 文档 `docs/acceptance/P2.md` 尚未写 |

**最高优先级缺口**：C1e record/replay 未接入生产路径（gap #7），导致 try-agent 和 E0 评测无法在 record/replay 模式下运行。

## 2. 关键代码事实

### 2.1 生产 LLM 工厂（未接 replay）

`services/agent/app/main.py` 第 141–195 行 `_build_production_ports()`：
- 第 162 行 `llm = make_llm()` — **直接调用，无 `LLM_MODE` 环境变量检查**
- `replay.py` 的 `RecordTransport`（第 236 行）和 `ReplayLoader`（第 317 行）未被任何生产代码引用
- `replay.py` 第 89 行 `LLMMode.from_env()` 可读 `LLM_MODE`，但调用方缺失

### 2.2 Agent 服务端点（生产路径可用）

`services/agent/app/main.py` 三端点：
- `POST /threads` — 创建线程，校验 JWT sid=thread_id
- `GET /threads/{id}/state` — 读 PG checkpoint，含 interrupts 列表
- `POST /threads/{id}/runs/stream` — SSE 流，body：`{"input": {"utterances": [...]}}` 或 `{"command": {"resume": "apv_..."}}`

生产模式下 `_resolve_ports`→`_build_production_ports` 组装真实 DeepSeek+C1b+C1c1+C1c2 gateway 图。方法：`graph.ainvoke`（非 astream，因 langgraph 1.2.16 checkpoint 保存问题）。

### 2.3 Gateway 图已接 G20/G21 ingest

`build_gateway_graph`（graph.py:255）：
- `ingest` 节点调 `ingest_via_gateway(client, ...)` → G20 `GET /sessions/{sid}/utterances` + G21 `GET /sessions/{sid}/clarifications/current`
- 输入 `utterances` 只带 ID（如 `[{"id":"utt_x","kind":"typed","text":""}]`），L2 返回权威 text/scope/parse_status
- `gather` 节点的 read executor 经 `make_gateway_read_executor` → G2 `POST /tools/read/{tool}`

### 2.4 W4c 夹具模式（可复用做 try-agent）

`tests/gateway/w4c/db/support.py`：
- `allow_plan_ctx`（第 142 行）：async context manager，真实 obligations L3 + 真实 G4，种 session/utterance/read 记录，调 G4 evaluate，yield `AllowPlanContext`
- `approval_plan_ctx`（第 276 行）：同 + G9 面板 + G6 确认，yield `ApprovalPlanContext`
- G9 用 F JWT（`sign_jwt(scope=["app","approve"], aud=APP_AUDIENCE)`），G6 带 view_hash + risk_ack
- 时钟：`_use_real_clock`（第 54 行）替代 W3b 固定时钟

### 2.5 令牌分配（已满足 PRD 安全要求）

| 调用方 | 令牌 | 可做的事 |
|---|---|---|
| L1 agent → L2 | `AGENT_SERVICE_TOKEN` + assistant JWT（Bearer） | G2 reads, G20/G21, G4 evaluate, G8 execute |
| BFF 测试 → L2 | `SERVICE_TOKEN` + F JWT（含 approve scope） | G3/G15 登记原话, G6 确认, G7 拒绝, G9 取面板, G10 OTP |
| 协调者 | `DEEPSEEK_API_KEY`（仅 `.env`） | 注入 agent 容器环境 |

L1 **不持** F/GATEWAY/D 写凭证 — D9/D10 测试已验证。

### 2.6 长期栈种子

当前 `p1_core_v1`（cus_a/cus_b，活期 6,000 元）。PRD §2.2 完整种子（cus_zs etc.）未实现。W4c/E0a 的 obligations fixtures 只有临时 tmpfs PG。

## 3. try-agent 的现有生产 API 具体字段

### 3.1 BFF 侧（测试扮演）

原话登记（G3）：
```
POST /sessions/{sid}/utterances
X-Service-Token: <SERVICE_TOKEN>
Authorization: Bearer <F_JWT>   # aud=safebank-app, scope=app, sub=cus_zs
Body: {
  "kind": "typed",
  "text": "给张三转800元",
  "request_id": "req_xxx"
}
→ 200 {"utterance_id": "utt_xxx", "parse_status": "PARSED", "scope": {...}, ...}
```

取面板（G9）：
```
GET /approvals/{approval_id}
Authorization: Bearer <F_JWT>   # scope=app,approve
→ 200 {"approval_id": "apv_xxx", "plan_id": "pln_xxx", "view_hash": "abc...",
        "risk_ack_required": true, "view": {...}}
```

确认（G6）：
```
POST /approvals/{approval_id}/confirm
Authorization: Bearer <F_JWT>
Body: {"request_id": "req_xxx", "view_hash": "abc...", "risk_ack": true}
→ 200 {"plan_id": "pln_xxx", "status": "CONFIRMED"}
```

### 3.2 Agent 侧

Agent 收到 assistant JWT（sub=cus_zs, sid=ses_x, aud=safebank-assistant, scope=assistant, utts=[...]），调 agent HTTP 服务：
```
POST /threads/{sid}/runs/stream
Authorization: Bearer <assistant_JWT>
Body: {"input": {"utterances": [{"id": "utt_xxx", "kind": "typed", "text": "给张三转800元"}]}}
→ SSE events: interrupt (REQUIRE_APPROVAL) 或 values+cards
→ resume: {"command": {"resume": "apv_xxx"}} → G8→respond
```

## 4. 最小可行 T1

### T1：C1e record/replay 接入生产 LLM 路径

**输入**：`services/agent/app/main.py` 的 `_build_production_ports()`；`services/agent/app/llm/replay.py` 的 `RecordTransport`、`ReplayLoader`、`LLMMode`

**输出**：`_build_production_ports()` 在 `LLM_MODE=record` 时用 `httpx.Client(transport=RecordTransport(...))` 构造 `make_llm(http_client=...)`；`LLM_MODE=replay` 时用 `ReplayLLM`（需新建，封装 `ReplayLoader` 做 httpx handler 注入）；`LLM_MODE=live` 走原路径

**白名单文件**（至多 6 个）：
1. `services/agent/app/main.py`（_build_production_ports + make_llm 注入点）
2. `services/agent/app/llm/replay.py`（RecordTransport, ReplayLoader, LLMMode, replay_key）
3. `services/agent/app/llm/client.py`（make_llm 签名，接受 http_client 参数）✓ 已支持
4. `tests/agent/test_service.py`（现有 fixture 模式参考）
5. `services/agent/app/llm/schema.py`（system prompt + tools schema 来源）
6. `Makefile`（LLM_MODE env 默认值，如需）

**真实红测判据**（3 条）：
- `LLM_MODE=record` 调用流式无 crash，`evaluation/replay/b1/default/replay.jsonl` 写入 ≥1 条
- `LLM_MODE=replay` 重跑同一输入，不调 DeepSeek API（用 fake transport 验证无 HTTP 出站）
- `LLM_MODE=live` 行为不变（现有 regression 全绿）

**退出命令**：`make test-agent-db; make test -k agent`（fake ports 下 replay 路径）；之后协调者手工注入 key 跑 1 次 live 冒烟

**依赖顺序**：T1 必须先于 try-agent T2（没有 record/replay，try-agent 每次调 DeepSeek 不可复现）

### T2（T1 后）：try-agent 真实闭环脚本第一版

**输入**：W4c `tests/gateway/w4c/db/support.py` 的 `approval_plan_ctx` 模式（真实 L3+G4+G9+G6+G8）

**输出**：`scripts/try_agent.py`（<150 行）：运行为 async script，启动真实 L3（obligations seed 含 cus_zs）+ gateway + agent 服务（TestClient），扮演 BFF 登记原话、取面板、确认、调 agent resume，断言 L3 账本变化 = 计划金额

**依赖**：T1（record/replay 接好否则不可复现）、W4c G8（已合 ✓）、G6 确认 + G10 OTP（W4b2b/W4b2c 已合 ✓）

### T3（T2 后）：错误/重启/record-replay 加固 + L1 残缺口

L1 已知缺陷（按优先）：
1. `verify_output` 端口假实现（main.py:220 `return "Done"`）— 需接 C1e `OutputVerifier`
2. `respond` 端口无 LLM（main.py:208–212）— 需接 C1e `RespondAdapter`
3. `_dummy_read_executor`（main.py:200）— gateway 图已替代，basic 图仍是 dummy

## 5. 推荐 try-agent 驱动模式

**全默认生产**（不自动确认）：try-agent 脚本接收 utterance text，运行完整三层流到 `REQUIRE_APPROVAL` 中断，打印 `approval_id`、`view_hash`，**等待人工按回车**才继续确认 + resume + 执行。

**测试模式**：`TRY_AGENT_AUTO_CONFIRM=1` 时自动确认（仅合成固定场景），检查 L3 数据库前后差值。

**模式推荐**：对 P2 验收用 `LLM_MODE=record` 先录制一次 → 之后用 `LLM_MODE=replay` 反复跑（不烧 DeepSeek token），L3 数据库每次 tmpfs 重建。协调者偶尔用 live 验证。

## 6. 未覆盖风险（P2 不阻塞）

- 跨场景联动（订阅/理财/账单）的真实 L3 工具未实现（仅 transfer/freeze_card/update_limits 可用）
- G10 OTP 红级确认（W4b2c 已有，但黄级转账不需要 OTP，红级场景留 P3）
- 真实 compose agent 容器运行（当前仅 TestClient/FastAPI 测试，不做 docker 部署验收）
- agent.env 的 DEEPSEEK_API_KEY 注入（compose 尚未有 agent.env 文件，需 scripts/keys.py 新增）