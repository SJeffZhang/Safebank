# P2 审批实例密钥小修 r2

**工作目录**: `codex/p2-approval-runtime` (worktree)
**启动**: 2026-10-26，全新会话 30 轮

## 任务清单

- [x] 1. 撤回 conftest 中 `approval_hmac_key_override` 参数（本次未修改 conftest，无需撤回）
- [x] 2. 加强 service.py 实例配置校验（bytes/callable → 503，不回退 global）
- [x] 3. plans.py / execution.py 改用 getattr 安全取 app.state 密钥
- [x] 4. 测试：真实双 key 绑定判据（ApprovalBinding + verify_binding 各自 key）
- [x] 5. 测试：配置校验（缺 key / 短 key / 无效 profile / 非 callable wall_now → 503）
- [x] 6. 测试：global 有效但实例缺 key → 仍 503
- [x] 7. 红测：旧路径 503 → 绿测：修复后通过
- [x] 8. 运行 newtest + test-w3b/w4b1/w4b2b/w4c，ruff 修复

## 实际测试结果（2026-10-26 r2 收尾）

| 命令 | 结果 |
|---|---|
| `make test-w3b` | **40 passed**（含 7 条新 test_g4_instance_keys） |
| `make test-w4b1` | **57 passed** |
| `make test-w4b2b` | **58 passed** |
| `make test-w4c` | **38 passed** |
| `ruff check` | **All checks passed** |
| `ruff format --check` | **3 files formatted**（service.py 已修） |

## 变更记录

| 提交 | 内容 |
|---|---|
| e61f438 | WIP checkpoint（上一 worker 累积） |
| （本次） | ruff format service.py，进度文档更新 |