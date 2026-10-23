# C1c1：L1 gateway 协议客户端（wire + 只读 + 评估/执行 + 错误边界）

2026-10-24，DeepSeek V4 Pro worker（本机 Claude Code CLI）。候选业务提交见组件 commit；协调者亲验前不发布、不 push / PR / merge。

## 范围与边界

- 只实现 L1 侧 HTTP/wire 边界：`services/agent/app/gateway/{__init__,wire,errors,client}.py` 与
  `tests/agent/gateway/{__init__,transport,test_wire,test_client_reads,test_client_write_paths,test_errors}.py`。
- 只调用配置的 L2 base_url，携带 `X-Service-Token`=A 服务令牌（构造参数）+ `Authorization: Bearer
  context.assistant_token`（每次调用从 `AgentContext` 取，不缓存、不进 state/checkpoint/日志/异常）。
- 未改 graph/**、llm/**、gateway 服务、contracts、bankcore、L3、依赖/Makefile/CI/compose/AGENTS/PRD。
- **fake 边界**：全部自动测试走 `httpx.MockTransport`（`tests/agent/gateway/transport.py` 的
  `ScriptedGateway`）；L2 G4/G8 当前仍是 stub（503），因此 evaluate/execute 的「成功/202/409」wire 均
  由 fake 端回放，**不构成真实 L1→L2→L3 三层联调**。
- 客户端不生成授权判定、不把失败当成功、不把 202 当执行完成；HTTP 不重试、不轮询 UNKNOWN、
  `follow_redirects=False`、有限 timeout。

## 实现接口

- `GatewayClient(base_url, service_token, *, http_client=None, timeout=10.0)`。
- `get_utterances(ids, context) -> tuple[GatewayUtterance, ...]`（G20，ids 精确匹配、无重复/漏项/跨请求）。
- `get_clarification(context) -> GatewayClarification | None`（G21，null → None）。
- `read_tool(tool, args, context) -> GatewayReadEnvelope`（G2，18 个 `READ_ARGS` 白名单，必填
  `history_days` 不补猜，结果经 `READ_RESULTS[tool]` 校验，原始 `data` 原样保留、来源不重写）。
- `evaluate(draft, context) -> GatewayDecision`（G4，仅 PlanDraft 原样 body，无 extra origin/auth，
  `draft.session_id != context.session_id` 零 HTTP 拒）。
- `execute(plan_id, request_id, context) -> ExecutionResult | UnknownExecution`（G8，body 仅
  `{request_id}`，200 复用冻结 `ExecutionResult`、202 用 `UnknownExecution`，两者 `plan_id` 必须等于
  目标；409 `STATE_CHANGED`/`POLICY_CHANGED` 的 `details.decision` 完整保留且绑定原 plan，错误 plan → 协议异常）。
- 错误三类（`GatewayErrorCategory`）：`RESPONSE`（良构 `{error:{code,message,details,trace_id}}`，
  code 必须为已知冻结 `Code`，保留 wire 校验后的 `details.decision`）/ `PROTOCOL`（非 JSON、302、
  未知 code、畸形成功/错误形状）/ `TRANSPORT`（超时/断连，execute 语义上为结果不确定）。
  `__str__` 只含固定类别/状态/code，不回显凭证、原始 httpx 异常或服务端任意 message。

## 红→绿

- 先写 4 个测试文件 + transport 助手，未实现源码时先跑：`tests/agent/gateway` 4 个模块
  `ModuleNotFoundError: No module named 'services.agent.app.gateway'`（红，0.10s）。
- 实现 `wire/errors/client/__init__` 后 `tests/agent/gateway -q`：首轮 52 passed / 1 failed
  （`test_execute_409_state_changed_wrong_plan_rejects`：409 内 decision 的 plan 绑定校验必须发生在
  `execute` 而非通用 `_error_from`，因撤销类 STATE_CHANGED 合法地不含 decision）。在 `execute` 捕获
  `GatewayResponseError` 并核对 `decision.plan_id` 后，53 passed。

## 退出命令（全部实际运行）

- `make lint`：ruff check 全绿；272 files already formatted。
- `.venv/bin/pytest tests/agent -q`：136 passed（C1b 83 + 本组件 53），零 skip/xfail。
- `make test`：2255 passed / 531 skipped（9.07s）。
- `make contracts-check`：PASS（契约 v1 未漂移）。

## 未覆盖 / 待决

- 真实 L2 G4/G8 尚为 stub，本组件不宣称真实 evaluate/execute/三层闭环（C1c2 接入原图后由协调者真实联调）。
- 错误路由（§6.1.1 逐行）、`get_utterances` 结果的 typed/hint/action 语义、REPLAN 定向补读与
  `read_tool` 桥接现有 `GatherAdapter.ReadExecutor` 均为 C1c2。
- PG saver / HTTP/SSE / 回复与卡片（C1d/e）不在本组件。
- 凭证永不落盘：`.env`/`.secrets` 未读取；测试内 token 为合成占位文本。
