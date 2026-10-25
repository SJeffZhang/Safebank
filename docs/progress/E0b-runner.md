# E0b 进度：L1 稳定性评测 runner

## T1 评测种子加载器（2026-10-26）

- **实现**：DeepSeek worker 在会话 E0b-T1-r1 中写完主体（用满轮数，未提交），后由 E0b-T1fin-r1 修复 `KeyError: 'cus_ww'`（同样用满轮数，未提交）。现场分别快照在 `refs/wip/E0b-T1-r1`、`refs/wip/E0b-T1fin-r1`。代码由协调者核验后代为提交，作者仍为 worker。
- **文件**：
  - `evaluation/runner/seed.py`：把评测种子 JSON 加载进 tmpfs PG 上的真实 bank-backend 数据库；target_fingerprint 用 gateway 的真实指纹算法和测试用 FINGERPRINT_KEY 重新计算。
  - `tests/evaluation/runner/{conftest.py,test_seed_loader.py,fixtures/mini_seed.json}`。
  - `Makefile` 新增 `test-e0b` 目标。
- **协调者亲自复跑**：
  - `make lint`：342 files
  - `make test-e0b`：3 passed（真实 L3，零 skip）
  - `make test`：2702 passed / 708 skipped
- **未完成**：checks.yml 中的 test-e0b 一行、runner 主流程、指标、报告，留到后续单任务（T2 起）。
