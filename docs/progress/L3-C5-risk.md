# L3 C5 风险测评 — 执行测试（worker 报告）

## 任务
补 C5 接线测试：`submit_risk_assessment` + `get_risk_questionnaire` + `get_risk_profile`
经真实 tmpfs Postgres + uvicorn HTTP 执行验收。

## 修复项（每项一条根因）
1. `create_app()` 报 `unexpected keyword argument 'admin_url'` — 测试沿用旧签名，改为
   `create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)`（与
   `test_obligations_data.py` 一致）。
2. `clean_database` fixture 里我此前留下的占位行 `sa_select(m).where(m.metadata.tables == "")`
   会运行时报错 — 已删除，truncate 改为 `text(f"TRUNCATE {names} CASCADE")`。
3. `reads.py` 的 `get_risk_questionnaire`(332) / `get_risk_profile`(346) 已接线，`get_profile`
   risk 摘要同源（customer 表），无需新增 — 测试直接覆盖四种有效期状态。
4. ruff I001 导入排序 — `ruff check --fix` 修复后提交。

## 覆盖
- 事实行 + 客户摘要同事务（score 16→R3，risk_valid_until "2027-10-15"，1 条操作事件）
- 同计划 20 次执行 → risk_assessments 1 行、plan_executions 1 行
- 非法答案（q99）→ status FAILED / CONTRACT_INVALID，无事实行、客户 risk_level 仍 NULL
- get_risk_profile 四种状态：未测评 / 有效 / 过期 / 边界当日
- get_risk_questionnaire：rq_v1、6 题、a/b/c/d 选项、validity_months 12
- 预演 apply_plan 与持久化 reload 一致（record id/score/level/valid_until）

## 门禁（本机实测）
- `ruff format packages services tests` → 397 files（1 文件重排）
- `make lint` → All checks passed
- `make test-obligations` → **73 passed, 1 warning in 18.95s**
- `pytest tests/bankcore -q` → **172 passed in 0.16s**

## 提交
- `77fa712` test(L3): C5 risk execution tests against real PG
- `74f2d30` test(L3): sort imports + format C5 risk exec test
