# SafeBank CI 夹具与锁负控修订：独立只读审查

- 日期：2026-11-01。
- Base：`1c601881590256af1b8d1251915f9dd52b66375c`。
- 独立核对提交：`9a68ddfc0f0cf78124c098b6bdbc4ea049c40217`、`8ea8fb0eade5c1e9101f80dd1829ef2459042204`。
- 结论：**APPROVE**；未发现可操作 P0/P1/P2。
- 范围：两处默认 preflight 测试夹具、helper/HTTP 锁测试异常传播与记录文档。未重审此前已 APPROVE 的整分支功能。

## 发现与结论

1. **默认夹具与生产模型对齐，未放宽 schema/hash 校验。** 两处手写 `_PREFLIGHT_JSON` 改为 `PreflightView(executable=True).model_dump(mode="json")`，包含现有默认 `reserve_projection_available=False`。它与创建 PlanView 已采用的相同模型序列化保持一致。W4b1 的显式 `preflight` 参数分支仍保留；刻意错误输入、绑定篡改和 evaluation_repo 存储测试没有被改成模型默认值。生产 `create_for_evaluation` 的 view identity、content_hash、完整 preflight 严格相等、风险/decision/findings/最新 revision 校验全部未改。

2. **RED 锁负控更严格。** 旧 `except Exception: deadlock_detected=True` 不再把合同、配置、连接等任意失败算成锁冲突。现在仅捕获 `DBAPIError`，只接受 `orig.sqlstate` 的 `40P01`（deadlock）和 `55P03`（lock-not-available/lock_timeout）；其他 DBAPI 错误重新抛出，非 DBAPI 错误直接传播。`await asyncio.gather(*done)` 会读取已完成任务异常，即使另一任务确有合法锁错误也不能掩盖该异常。`pending` 仍取消并等待清理，再要求为空；至少一个指定 SQLSTATE 仍是通过条件。原生产 helper 调用、错误锁序安排、等待与 timeout 数值未改。

3. **HTTP GREEN 锁测试不再吞掉并发创建异常。** 新增的 `await asyncio.gather(*done)` 让并发 create_for_evaluation 已完成任务中的 schema/hash/配置等错误直接失败。原 pending 超时失败、endpoint 5xx/异常标记，以及 G6 CONFIRMED、G7 REJECTED、G10 恰一 OTP challenge 的真实业务状态核验全部保留；没有把非 5xx 当作足够的业务成功。

4. **提交边界与证据措辞准确。** 两个提交仅改四个测试文件及 acceptance 文档；`services/`、`packages/` 没有差异。文档明确子任务只运行 collect-only，`322 tests collected` 不代表 322 个已执行或数据库通过。实际 PG/完整 CI 仍归协调者独占，首轮 CI 的夹具失败不被改写为生产锁故障或历史通过。

## 独立实际检查

```text
git diff 1c601881590256af1b8d1251915f9dd52b66375c..8ea8fb0eade5c1e9101f80dd1829ef2459042204 -- tests
# 静态审查全部四文件 diff、上下文与原业务状态验证

git diff --exit-code 1c601881590256af1b8d1251915f9dd52b66375c..8ea8fb0eade5c1e9101f80dd1829ef2459042204 -- services packages
# exit 0；生产服务、共享契约/领域代码不变

git diff --check 1c601881590256af1b8d1251915f9dd52b66375c..8ea8fb0eade5c1e9101f80dd1829ef2459042204
# exit 0

.venv/bin/python -m pytest -q -p no:cacheprovider tests/gateway/w4b1/test_binding.py tests/gateway/w4b1/test_planview.py tests/gateway/w4b1/test_material_details.py
# 97 passed in 0.08s；纯模型/哈希/绑定/面板测试，无 DB
```

另以独立 Python 导入检查两处实际 `_PREFLIGHT_JSON` 均等于 `PreflightView(executable=True).model_dump(mode="json")`，且 `reserve_projection_available is False`、`scheduled_reserve_cents == 0`，检查通过。首次直接 Python 导入未设置项目 pythonpath，报 `ModuleNotFoundError: safebank_contracts`；按 pyproject 的 pythonpath 设置后通过，不计为产品红测。修正后的实际命令为：

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:packages/contracts/src:packages/bankcore/src:services/bank-backend .venv/bin/python - <<'PY'
from services.gateway.app.schemas.decision import PreflightView
from tests.gateway.w4b1.db.test_creation import _PREFLIGHT_JSON as creation
from tests.gateway.w4b2b.db.conftest import _PREFLIGHT_JSON as approvals
expected = PreflightView(executable=True).model_dump(mode='json')
assert creation == approvals == expected
assert expected['reserve_projection_available'] is False
assert expected['scheduled_reserve_cents'] == 0
print('Both default fixtures match serialized PreflightView; reserve availability false and zero reserve are explicit.')
PY
# exit 0
```

以上纯测与导入执行时 root 已集成首个提交为 `4cf3e5d68ae217b40832b4233912d752685037bc`；第二提交的单行 done 异常传播通过固定 author SHA 静态审查。两提交没有生产代码变动，纯测文件也未被这两提交修改。

## 未执行与最终门禁

本审查未执行数据库 fixture、真实 PostgreSQL 锁竞争、HTTP/DB 业务状态断言、Docker、部署、模型、浏览器或 demo；未重跑收集，也不将作者的 322 项收集或本次 97 项纯测称作 322 项数据库执行。协调者必须记录真实临时 PG、正式 W3b 与后续完整精确 head CI 的实际结果后再按原门禁合并。本审查不修改原整分支功能 APPROVE，也不替代这些执行门禁。
