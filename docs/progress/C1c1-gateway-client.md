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

## 评审修复轮 1（`docs/reviews/C1c1-gateway-client-review.md`，REQUEST_CHANGES）

逐条先红→修→绿，P1/P2/Observations 全部由本 worker 处理：

- **P1-1 注入 client 绕过 timeout/重定向**：`_call` 改为 `build_request` 后 `send(request,
  follow_redirects=False)`，并在 `request.extensions["timeout"]` 显式写入 `httpx.Timeout(self._timeout)`
  强制每次调用有限超时 + 禁重定向，不信任注入 client 默认；构造器用 `math.isfinite` 拒 NaN/Inf/0/负数。
  红测复现：注入 `httpx.Client(follow_redirects=True, timeout=None)` + 跨域 302 会向 evil 发第二次请求
  （带 X-Service-Token）；绿测 `test_injected_client_cross_host_redirect_is_not_followed` 断言仅 1 次
  原网关请求 + 协议错误，`test_injected_client_timeout_is_enforced_via_request_extensions` 断言
  `request.extensions["timeout"]` 为有限 `httpx.Timeout`（connect/read/write/pool=5.0）。生产标准 client
  与 MockTransport 均保留可用，未另造成功客户端掩盖注入。
- **P2-1 严格 wire**：`shadow_b1` 递归 `GatewayDecision | None`（+`model_rebuild`）；本地
  `GatewayNormalizedScope`/`GatewayScopeTransfer` mirror（transfer `amount_cents` 用严格 `Cents`，
  `fields` 保持 free map）；`parse_status`/clarification `kind`/finding `code` 闭集（本地
  `GatewayParseStatus`/`GatewayClarificationKind` mirror + 冻结 `Code`）；`Utterance.kind` 保持开放 `str`。
  未改共享 schema/graph/LLM，合法字段不丢。
- **P2-2 不链接 raw Pydantic error**：`read_tool` 非法 args 与 `evaluate` 非法 draft 改 `from None`
  并固定消息；unknown tool 固定消息不回显任意原输入。测试只断言输出边界（`str`/`repr`/
  `format_exception_only` 不含输入、`__cause__ is None`），**不**断言 `__context__`/会话调试框架 locals
  的假保证。
- **P2-3 18 工具参数化**：`tests/agent/gateway/read_cases.py` 覆盖全部 18 个 `READ_ARGS`/`READ_RESULTS`
  的真实合法生产字段形状；happy 返回 + 畸形返回拒 + 必填不补猜（`is_required()` 内省参数化）各一组。
- **Observations 安全边界**：`_error_from` 只保留路由所需且经 wire 验证的 `details.decision`
  （`decision.model_dump`，非原始 dict 副本），其余任意 details 键丢弃；`trace_id` 只接受
  `^[A-Za-z0-9_-]{1,256}$` 否则省略。补合成 service/JWT 在 error `message`/未知 `details`/`trace_id`
  的回显测试，`str`/`repr`/`details`/`trace_id` 均不泄密；Code/status 精确保留。

修复后 `tests/agent/gateway -q`：124 passed。

## 退出命令（全部实际运行）

- `make lint`：ruff check 全绿；273 files already formatted。
- `.venv/bin/pytest tests/agent -q`：207 passed（C1b 83 + 本组件 124），零 skip/xfail。
- `make test`：2326 passed / 531 skipped（9.10s）。
- `make contracts-check`：PASS（契约 v1 未漂移）。

## 未覆盖 / 待决

- 真实 L2 G4/G8 尚为 stub，本组件不宣称真实 evaluate/execute/三层闭环（C1c2 接入原图后由协调者真实联调）。
- 错误路由（§6.1.1 逐行）、`get_utterances` 结果的 typed/hint/action 语义、REPLAN 定向补读与
  `read_tool` 桥接现有 `GatherAdapter.ReadExecutor` 均为 C1c2。
- PG saver / HTTP/SSE / 回复与卡片（C1d/e）不在本组件。
- 凭证永不落盘：`.env`/`.secrets` 未读取；测试内 token 为合成占位文本。
