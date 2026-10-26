# L1 P2 SSE结构化输出验收（2026-10-26）

基线 main `00aca15`；分支 `codex/l1-p2-wire`。修复客户端读不到结构化Decision的缺陷，SSE values中的Decision/ExecutionResult保持JSON对象，updates interrupt保持原wire结构。

协调者实际命令：
- `make lint`：365 files，exit0。
- `PYTEST_ADDOPTS=--tb=short perl -e 'alarm 300; exec @ARGV' make test`：2727 passed/748 skipped，20.06s，exit0。
- 同样有界 `make test-agent-db`：39 passed/454 deselected，2.45s，exit0，真实tmpfs PG。
- 变异：备份当前main.py，临时使用`git show 00aca15:services/agent/app/main.py`旧内容运行新增HTTP/PG测试，4 failed/1 passed，1.48s；finally恢复当前内容，未用checkout丢弃WIP。

新增5条测试通过真实app_factory/HTTP/SSE/PG检查Decision、ExecutionResult、REQUIRE_APPROVAL及GET state一致。LLM/L2端口测试注入，完整P2三层live/确认前后数据库断言仍未完成；无新接口、无授权规则变化。独立DS审查 APPROVE（报告 docs/reviews/L1-P2-sse-wire-review.md），无P0/P1；精确head四CI待完成，未宣称组件已发布。已知P2：GET state全结构等价/异常类型回归/中断独立覆盖仍可增强，bytes备用编码尚未统一，均不涉及当前金融模型主路径。
