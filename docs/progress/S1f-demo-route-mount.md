# S1f：G22 按 DEMO/EVAL 真实条件挂载

2026-10-24；分支 `feat/l2-demo-route-guard`；输入基线 `aa42a0c`（最新 main，已含 S1d/PR #11 文档合并）。本组件只本地提交，不 push / 不开 PR / 不 merge。

## 输入、输出与范围

输入 `Settings.demo_mode` / `Settings.eval_mode`；输出每个 public app 的真实路由集合：

- 两者都 False：无 `GET /auth/demo-customers`（G22），HTTP 404。
- 任一 True：G22 存在，凭证要求不变（`F_SERVICE`，无 app JWT）。
- health / 其他 auth / public / internal / demo 原规则保留，MATRIX 权限字典、`lookup`、`match`、所有函数签名均不变，不碰 W1 正在改的 `auth.py`，不改 config 结构 / 冻结 schema / state / contracts / 迁移。

## 方案（先读实际依赖实现再选）

实测安装为 FastAPI 0.161.1 / Starlette 1.9.0。读 `.venv/.../fastapi/routing.py` 确认 `include_router` 现在把被包含路由包进惰性 `_IncludedRouter`（`original_router`），在**请求期**才经 `effective_candidates()` 读 `original_router.routes`，并按 `_get_routes_version()` 缓存。因此：

- 不能 `auth.router.routes.remove(...)`（会污染 W1 单例，先建 off 后建 on 会缺路由）；
- 不能缓存一个共享的「过滤后 router」再反复 include（同样会跨 app 串状态）。

采用**每 app 独立装配**：`main._public_auth_router(demo_mounted)` 每次新建一个 `APIRouter()`，把 `auth.router.routes` 里的路由对象**按引用复制**进去（demo off 时跳过 path 为 `/auth/demo-customers` 的那条），再 `app.include_router(该新 router)`。每个 app 各包各的 router 实例，互不共享缓存；`_build_effective_context` 对 `APIRoute` 只读，共享路由对象安全。全局 `caller_dependency` 仍经 include 上下文向下传播，鉴权行为不变。

`matrix.coverage_errors` 增加 G22 条件校验：public app 在 demo off 时 G22 必须缺失（误挂报 `demo endpoint mounted while DEMO/EVAL off`），demo on 时 G22 必须存在（缺失报 `matrix entry has no route`）。internal app 不要求 G22（原逻辑不变）。

## 红 → 绿证据

1. 初次红（含我自己的测试 helper 签名 bug）：`.venv/bin/pytest tests/gateway/test_matrix.py -q` → **9 passed / 7 failed**。其中 6 条失败是 `_settings(**overrides)` 被位置传参调用导致的 `TypeError`（测试代码缺陷，非真实红），第 7 条 `test_g22_coverage_rejects_mount_when_off` 是真实红。
2. 修正 helper 为 `_settings(overrides=None)` 后重跑 → **12 passed / 4 failed**，exit 1，0.74s，真实红：
   - `test_g22_absent_when_demo_and_eval_off`：off 时 G22 仍在路由集合；
   - `test_g22_http_404_when_off`：off 时 GET G22 返回 401（路由存在被全局依赖拒绝）而非 404；
   - `test_g22_build_order_does_not_pollute_global_router`：off app 仍含 G22；
   - `test_g22_coverage_rejects_mount_when_off`：无「误挂」方向校验。
3. 实现 `main._public_auth_router` + `matrix.coverage_errors` 条件分支后同命令 → **16 passed**，exit 0，0.67s，全绿。其中 `test_g22_present_when_demo_or_eval_on`（on/off、off/on、on/on 三组合）与 `test_g22_http_requires_f_service_when_on`（无凭证 401 / 带 F 凭证 503 stub）在红阶段即已通过，作为挂载与凭证边界回归保留。

## 改动文件清单

- `services/gateway/app/main.py`：导入 `APIRouter`；新增 `_public_auth_router()`；`build_public_app` 改为 `app.include_router(_public_auth_router(demo_mounted))`，`demo_mounted` 提前计算，其余 router 与 demo.router 装配不变。
- `services/gateway/app/auth/matrix.py`：新增常量 `G22_ROUTE`；`coverage_errors` 增加「demo off 误挂 G22」报错与「demo off 缺 G22 跳过」两条分支，docstring 同步；MATRIX 字典 / `lookup` / `match` / `CallerSpec` 等均未改。
- `tests/gateway/test_matrix.py`：新增 `_settings`/`_pairs` 辅助与 8 项 G22 测试（缺/现、三开关组合、off 404、on F_SERVICE、构建顺序无污染、coverage 误挂/缺失/通过）。
- `services/gateway/README.md`：覆盖校验说明由「D 路由按模式」改为「D 路由与 G22 按 DEMO/EVAL 模式」。

## 退出命令实际结果（全部实际运行）

| 命令 | 结果 | 用时 |
|---|---|---|
| `make lint` | 首次 `ruff format --check` 报 matrix.py 需重排（我多行 `errors.append` 被 format 收成一行），`ruff format` 修正后重跑：`All checks passed!` + `175 files already formatted`，exit 0 | — |
| `make test` | **406 passed / 302 skipped**，exit 0 | 7.14s |
| `make test-gateway` | **171 passed / 254 skipped**，exit 0 | 4.42s |
| `make test-gateway-db` | **17 passed**（真实临时 PG，零 skip），exit 0 | 3.87s |
| `make test-shared-runtime` | **59 passed**（真实临时 PG + 7 种 Linux 权限/healthcheck，零 skip），exit 0；输出中的 `CancelledError` 是既有取消场景预期打印，非失败 | 10.30s |
| `make contracts-check` | `PASS: contracts v1 match frozen schemas`，exit 0 | — |

未运行 `make up` / `make down` / `make test-p1` / `make test-integration`，未执行任何 docker volume / compose down -v / system prune，未删除或重建数据卷，未读取/打印 `.env`/`.secrets` 内容。

## 未覆盖项

- 只证明 G22 的**挂载与凭证边界**（off 404、on 仍 F_SERVICE、无凭证 401、带 F 凭证落到 W1 stub 503）。G22 的客户列表业务（真实 customer_id/display_name、不返 PIN/测评/余额）仍属 W1，main 尚无 W1 实现，本次未验收。
- 构建顺序覆盖 on→off→on 与 off→on（同一进程内多次 `build_public_app`）；未验证跨进程/热重载行为（与本组件无关）。
- 未运行全栈集成（up/HTTP→PG），按任务书明确禁止。

## 待决

- 无阻塞待决。一个实现备注：`_public_auth_router` 只按顶层路由对象的 `path` 过滤；当前 `auth.router` 是平铺 `APIRoute`，若 W1 将来在 `auth.router` 内嵌套子 router，过滤需同步扩展。已按现状最保守实现并留此备注。
- 独立只读评审、协调者亲自六退出与 actionlint、push/base main PR、精确 head 四 CI 全绿后 merge，均由协调者办理。

## 修复轮 1（2026-10-24，独立评审 APPROVE 后修 2 条 P2）

独立评审 `docs/reviews/L2-S1f-review.md` 结论 APPROVE，P0 0 / P1 0 / P2 2。按协调者要求修掉两条 P2，保持其余行为与范围不变。

- **P2-1**：G22 过滤改为**同时匹配方法与路径**，只跳过 GET-only 且 path 为 `/auth/demo-customers` 的顶层路由（`route.methods == {"GET"}`），不再误删同路径其他方法。
- **P2-2**：`_public_auth_router` 遇到非 `APIRoute` 顶层项（如嵌套 router）改为 **fail-closed**，直接 `raise GatewayError(Code.FORBIDDEN, 500, ...)` 拒绝构建，不再依赖 coverage 兜底。

### 红 → 绿证据

1. 先加两条红测（`tests/gateway/test_matrix.py`：`test_g22_filter_matches_method_and_path`、`test_g22_filter_fails_closed_on_non_api_route`），未改生产代码时：`.venv/bin/pytest tests/gateway/test_matrix.py -q` → **16 passed / 2 failed**，exit 1，0.78s。两条失败分别为「POST 同 path 被误删（out 为空）」与「非 APIRoute 未抛 GatewayError（DID NOT RAISE）」。
2. 修 `main._public_auth_router` 后同命令 → **18 passed**，exit 0，0.65s。首次实现误从 `fastapi` 顶层导入 `APIRoute`（0.161.1 不导出，ImportError 收集失败），改从 `fastapi.routing` 导入后通过；该 ImportError 属实现笔误，非业务红。

### 改动文件（本轮）

- `services/gateway/app/main.py`：导入 `fastapi.routing.APIRoute`；`_public_auth_router` 增加非 `APIRoute` fail-closed 与 method+path 精确过滤。
- `tests/gateway/test_matrix.py`：新增上述两条测试与 `pytest`/`APIRouter`/`Route`/`GatewayError`/`_public_auth_router` 导入。

### 退出命令实际结果（全部实际运行）

| 命令 | 结果 | 用时 |
|---|---|---|
| `make lint` | 首次 `ruff format --check` 报 main.py / test_matrix.py 需重排，`ruff format` 修正后重跑：`All checks passed!` + `175 files already formatted`，exit 0 | — |
| `make test` | **408 passed / 302 skipped**，exit 0 | 5.51s |
| `make test-gateway` | **173 passed / 254 skipped**，exit 0 | 4.58s |
| `make test-gateway-db` | **17 passed**（真实临时 PG，零 skip），exit 0 | 4.80s |
| `make test-shared-runtime` | **59 passed**（真实临时 PG + Linux 权限，零 skip），exit 0；CancelledError 为既有取消场景预期打印 | 11.17s |
| `make contracts-check` | `PASS: contracts v1 match frozen schemas`，exit 0 | — |

未运行 `make up`/`down`/`test-p1`/`test-integration`，未操作 Docker 数据卷，未读取 `.env`/`.secrets` 内容。

### 待决（修复轮 1 后）

- 原「待决」中的实现备注已由 P2-2 修复消除：非 `APIRoute` 顶层项现直接 fail-closed 拒绝构建，不再依赖 coverage 兜底。若 W1 将来确需在 `auth.router` 内嵌套子 router，需由协调者/owner 显式扩展此过滤器并同步本组件测试。
- push/base main PR、精确 head 四 CI 全绿后 merge 仍由协调者办理。

