# E0b T5缺失导入收尾（8轮，新会话）
不要读其他文件、AGENTS/PRD，不用子agent。当前仅剩一个确定错误，亲跑31passed/1failed：tests/evaluation/runner/test_evaluate_only.py:156 NameError uuid；freeze helper内有局部import uuid，黄色helper用uuid.uuid4()但模块未import。
第一步把import uuid加到该测试模块顶层，必要时删局部重复import；不要改其他逻辑。白名单该测试文件和docs/progress/E0b-runner.md（如时间够记录）。然后ruff check --fix/format evaluation/runner tests/evaluation/runner（只样式自动整理）；PYTEST_ADDOPTS=--tb=short perl -e 'alarm 300; exec @ARGV' make test-e0b，只看最后汇总。
一旦全绿，立即git add evaluation/runner/l1.py evaluation/runner/loop.py tests/evaluation/runner/test_evaluate_only.py docs/progress/E0b-runner.md，本地提交feat(E0b): enforce evaluation-only G4 boundary with real positive cases，再结束。无需变异/额外阅读/重设计，本轮不扩scope。若失败输出真实最后错误。禁live/env/secrets/up/down/push。
