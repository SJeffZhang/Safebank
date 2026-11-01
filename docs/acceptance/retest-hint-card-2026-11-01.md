# 复测 40：持久 hint 到 HINT 卡片动作

基线：origin/main `8e2810e6764e3fd3e37406b31e694605751e0356`。独立工作树原分支干净，保留旧分支，创建 `fix/retest-hint-card`。

输入为 GatewayDecision / GatewayReplanHint 的真实 `model_dump(mode="json")`，输出为 UICard 的 `HINT.ref`；ID 必须取自 `hint.id`。按钮文字仍由前端 G22a 获取，卡片不采纳 hint、不执行计划，不修改网关 scope/hash/TTL/epoch 校验。

## 实测

- 原码：将旧 `ref` 假夹具换成真实 GatewayDecision/GatewayReplanHint 后，`test_replan_user_generates_hints` 1 failed（期望 HINT action，实际空 tuple）。
- 新边界测试原码：5 个预期行为失败、8 个负控通过；SSE 夹具起初因 gather 缺 `with_read_executor` 报错，修正测试协议后原码 SSE 测试在 `actions=[]` 对比处失败；不把夹具错误算作产品红测。
- 修复后 `.venv/bin/pytest tests/agent/test_cards.py tests/agent/test_retest_hint_cards.py -q -p no:cacheprovider`：33 passed。
- 受影响服务 `.venv/bin/pytest tests/agent -m 'not db' -q -p no:cacheprovider`：1085 passed，42 deselected。首次沙箱执行 1083 passed、2 failed，均为既有 loopback HTTP 测试无法 bind 127.0.0.1；经沙箱外仅本机测试复跑全绿。
- `.venv/bin/ruff check --no-cache ...`、`ruff format --check ...`、`git diff --check` 通过。初次 ruff 因缓存路径无写权限退出，改 `--no-cache` 后通过。

覆盖：真实 JSON/model 对象、非法 ID、伪造 ref 不覆盖 id、空列表、agent-only 与混合 resolver、多 hint 顺序、输入不变；实际 app SSE 路由执行编译 Gateway 图，G20/G21/G4 为 MockTransport，checkpointer/DB lock 为内存替身。卡片 action 仅 kind/ref，无可见按钮文本；没有执行 G8，也没有替用户采纳。

未覆盖：真实 G22a 取标签、前端点击、真实 G3→G4→G9→执行闭环、真实 PG、模型、浏览器和完整 CI。卡片转换不验证 hint 的业务有效性，失效/篡改 scope/hash/TTL/epoch 仍由既有网关端点拒绝。

禁止运行模型、浏览器、演示、Docker、数据库及全量 CI；这些由协调者独占。共享 AGENTS/PRD 由协调者更新。
