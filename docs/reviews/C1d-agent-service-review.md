## 修复轮 5 定点复审（2026-10-25，e5edf16..5f88a8c）

> 范围：3 文件，+258/−40（仅 `tests/agent/test_service.py`、进度与评审文档）。生产代码零 diff。

### P2-1 修复验证：`test_concurrent_same_thread_lock_rejects` — PASS

- `test_service.py:746–750`：手动 `psycopg.connect(agent_pg.runtime_url)` 持锁，`pg_try_advisory_lock(hashtext('ses_conc'))` → True。
- `test_service.py:735`：`monkeypatch.setenv("AGENT_DATABASE_URL", agent_pg.runtime_url)` → handler 的 `_try_thread_lock` 读同一 env → 连**同一数据库**。这是关键修复（进度日志记载此前因缺 monkeypatch 连到不同库）。
- `test_service.py:761–766`：stream handler 中 `_try_thread_lock` 发现锁被外部持有 → `pg_try_advisory_lock` 返回 False → `HTTPException(409)` → Starlette 在 SSE 响应开始后捕获，封装为 `RuntimeError("Caught handled exception, but response already started.")`。测试断言 `pytest.raises(RuntimeError, match="response already started")`。
- `test_service.py:769–770`：`pg_advisory_unlock` + `hold_conn.close()` → 锁已释放。
- `test_service.py:772–780`：新 app 实例、同一 thread_id → POST stream → 200。确认锁释放后可正常恢复。
- 协调者红绿核验证实：把 `main.py` 的 `pg_try_advisory_lock` 改为恒真后 1 failed，还原后通过。

### P2-2 修复验证：`test_production_app_fail_closed_without_agent_service_token` — PASS

- `test_service.py:224`：仅 pop `AGENT_SERVICE_TOKEN`，保留有效 JWT key、`DEEPSEEK_API_KEY`、`AGENT_DATABASE_URL`。
- `test_service.py:227–229`：使用有效 JWT（Ed25519 签名、有效 `sub`/`sid`/`exp`）。JWT 鉴权通过，唯一失败来源是 `_build_production_ports` 中的 `RuntimeError("AGENT_SERVICE_TOKEN is not configured")`。
- `test_service.py:232`：改用 `POST /runs/stream`（而非旧的 `POST /threads`），因为只有 `stream_run` 调 `_resolve_ports` → `_build_production_ports`；`create_thread` 只查 saver，永远不触发 token 校验。
- `test_service.py:232`：断言 `pytest.raises(RuntimeError, match="AGENT_SERVICE_TOKEN is not configured")` — 精确匹配，不再使用 `status_code != 200` 这种无法区分失败原因的宽断言。
- `test_service.py:238–243`：finally 清理，仅 `AGENT_SERVICE_TOKEN` 按条件恢复。

### 新增 P2（不影响合并）

- **P2-5**：`test_service.py:218` 与 `test_service.py:731` — `import pytest as std_pytest` 冗余。`pytest` 已在模块顶层导入（`test_service.py:17`），`pytest.raises(...)` 可直接使用，无需局部别名。不影响行为，但增加不必要的 import 行。同样，`test_service.py:732` 的 `from starlette.testclient import TestClient` 与 `test_service.py:21` 的 `from fastapi.testclient import TestClient` 指向同一类，冗余。

### 未确认

- `RuntimeError("Caught handled exception, but response already started.")` 是 Starlette 内部消息字符串。如果将来 Starlette 升级变更措辞，`match="response already started"` 仍可匹配（子串匹配），但不如 `match="Caught handled exception"` 更稳定。当前不阻塞。

### 结论

**APPROVE** — P2-1 与 P2-2 修复正确。HTTP 层并发锁拒绝测试现在真实验证同一数据库上的锁冲突（手动持锁 → handler 409 → 释放后恢复 200）。fail-closed 测试使用有效 JWT + 具体 RuntimeError 匹配，不再宽断言。无新 P0/P1。P2-5（冗余 import）为纯风格问题，不影响正确性或安全性。