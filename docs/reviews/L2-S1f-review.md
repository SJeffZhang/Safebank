# L2-S1f 独立只读评审

2026-10-24；评审范围 `git diff origin/main HEAD`（单提交 38e382c）。只读：未改任何代码/配置/文档，未 push/commit，未跑 make up/down/test-p1/test-integration，未碰 Docker 卷。定点验证：`.venv/bin/pytest tests/gateway/test_matrix.py -q` → **16 passed**（0.75s）。

实际安装 FastAPI 0.161.1 / Starlette 1.9.0，与进度文档一致。已读 `.venv/lib/python3.12/site-packages/fastapi/routing.py` 核对 `include_router`/`_IncludedRouter`/`_EffectiveRouteContext` 行为。

## 逐条核对

1. **off 真不注册 / on 注册且仍 F_SERVICE —— 通过。** `main.py:103-104` 用 `demo_mounted = settings.demo_mode or settings.eval_mode`（config 中两字段为 `bool`），`_public_auth_router(False)` 跳过 path 为 `/auth/demo-customers` 的顶层路由，off 时该路由不进 app 路由表，`test_g22_http_404_when_off` 断言 404+`NOT_FOUND`（区别于「存在但 401」），非空洞。on 时 G22 存在，`test_g22_http_requires_f_service_when_on` 验证无凭证 401、带 `X-Service-Token` 的 F 凭证 503 stub，证明凭证要求仍是 F_SERVICE（PRD §9.5 G22）。
2. **每 app 独立装配、无全局污染 —— 通过。** `_public_auth_router` 每次新建 `APIRouter`，只把 `auth.router.routes` 的路由对象按引用 append 进新 router，不改 `auth.router` 单例。读 FastAPI 0.161 源码确认共享路由对象安全：`include_router` 包成惰性 `_IncludedRouter`，请求期 `_EffectiveRouteContext.from_api_route`（routing.py:1475）对每个 app 新建 context 并经 `_populate_api_route_state`（routing.py:1014）重建独立 `dependant`/`body_field`/`response_fields`，`get_route_handler`（routing.py:1278）经 ContextVar 读的是新 context 而非原路由，故无依赖缓存/response model 跨 app 串状态。`test_g22_build_order_does_not_pollute_global_router` 覆盖 off→on→off→on 并断言单例含 G22。
3. **path 过滤 —— 有两点 P2 见下；整体由 coverage fail-closed 兜底。**
4. **coverage_errors —— 通过。** `matrix.py:154-155` off 且误挂报错；`matrix.py:163-165` off 缺 G22 放行（continue）、on 缺 G22 报错。MATRIX 字典、`lookup`、`match`、函数签名均未改（diff 只加 `G22_ROUTE` 常量与两条分支）。internal app `coverage_errors(..., internal=True)`：G22 非 `/internal/` 前缀，不要求；若真被挂进 internal 会被既有「internal app has a non-internal route」拦截（fail-closed）。
5. **文件范围 —— 通过。** 仅 5 文件：`main.py`、`auth/matrix.py`、`tests/gateway/test_matrix.py`、`services/gateway/README.md`、`docs/progress/S1f-demo-route-mount.md`。`auth.py`、config、schemas、state、contracts、迁移未动。
6. **测试真实覆盖 —— 通过。** 8 项新增测试断言均非空洞（缺/现、三开关组合、off 404 vs on 401/503、构建顺序、coverage 误挂/缺失/通过两方向）；我已亲跑 16 passed。

## 发现

### P0
无。

### P1
无。

### P2
- **P2-1 path 过滤忽略方法。** `main.py:71` 只按 `path == "/auth/demo-customers"` 过滤，不看 `route.methods`。当前 `auth.py:11` 只有 GET、MATRIX 也只有 GET，无实际影响；若 W1 未来在同 path 加非 GET 方法会被误删。建议改按 `(method, path)` 或同时比对 `route.methods`。
- **P2-2 未来嵌套 router 过滤 fail-open（已被 coverage 兜底为 fail-closed）。** `main.py:71` 对顶层路由取 `getattr(route, "path", "")`；若 W1 日后在 `auth.router` 内嵌套子 router（FastAPI 会包成 `_IncludedRouter`，无 `path` 属性，返回 `""`），off 时嵌套的 G22 不会被跳过而漏进路由表。但 `matrix.py:154-155` 会在构建期报「mounted while DEMO/EVAL off」使启动失败，净效果 fail-closed。进度文档已备注此限制。建议过滤器对非 `APIRoute` 顶层项显式断言或递归，避免依赖兜底才不越权。

## 未确认

- 跨进程 / 热重载下多次 `build_public_app` 的行为未验证（任务书明示与本组件无关）。
- G22 客户列表业务（真实 customer_id/display_name、不返 PIN/测评/余额）仍属 W1，本次未实现未验收，与组件范围一致。

## 结论

**APPROVE**。off/off 真不注册、任一 on 注册且仍 F_SERVICE、每 app 独立无污染、coverage 双向校验与 MATRIX/lookup/match 不变、文件范围合规、测试真实覆盖，均已核实。P0 0、P1 0、P2 2（均为未来 W1 演进时的健壮性提醒，当前无实际影响且有 coverage 兜底），不阻塞合并。
