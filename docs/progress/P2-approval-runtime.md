# P2 审批实例密钥小修 r2

**工作目录**: `codex/p2-approval-runtime` (worktree)
**启动**: 2026-10-26，全新会话 30 轮

## 任务清单

- [ ] 1. 撤回 conftest 中 `approval_hmac_key_override` 参数（其余 fixture 保留）
- [ ] 2. 加强 service.py 实例配置校验（bytes/callable → 503，不回退 global）
- [ ] 3. plans.py / execution.py 改用 getattr 安全取 app.state 密钥
- [ ] 4. 测试：真实双 key 绑定判据（ApprovalBinding + verify_binding 各自 key）
- [ ] 5. 测试：配置校验（缺 key / 短 key / 无效 profile / 非 callable wall_now → 503）
- [ ] 6. 测试：global 有效但实例缺 key → 仍 503
- [ ] 7. 红测：旧路径 503 → 绿测：修复后通过
- [ ] 8. 运行 newtest + test-w3b/w4b1/w4b2b/w4c，ruff 修复

## 退出命令

```bash
# 每个 alarm 300，-q 末尾汇总
cd /Users/zhangshijie/Desktop/Project/Fintech2026-wt/P2-approval-runtime
perl -e 'alarm 300; exec @ARGV' make test-w3b 2>&1 | tail -5
perl -e 'alarm 300; exec @ARGV' make test-w4b1 2>&1 | tail -5
perl -e 'alarm 300; exec @ARGV' make test-w4b2b 2>&1 | tail -5
perl -e 'alarm 300; exec @ARGV' make test-w4c 2>&1 | tail -5
# newtest 单独跑
GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b/db/test_g4_instance_keys.py -q 2>&1 | tail -10
```

## 变更记录