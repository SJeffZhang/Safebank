# 精确 CI：预演测试夹具对齐（2026-11-01）

- 基线：`1c601881590256af1b8d1251915f9dd52b66375c`。
- 分支：`fix/retest-ci-preflight-fixture`；从已有干净 worktree 创建，原分支保留。
- 范围：仅三个测试文件与本记录；未修改生产代码、PRD、AGENTS、Schema、哈希或严格相等校验。

## 根因与修改

协调者提供的实际完整 CI 失败为 W3b `test_session_first_vs_create_for_evaluation_no_deadlock` 中 conn_b 的 `evaluation preflight does not match the view`，并非数据库死锁。本子任务未自行运行原失败 PostgreSQL 测试。

源码核对：`PreflightView.reserve_projection_available` 默认 `False`；生产 `create_for_evaluation` 将数据库 `preflight` 与 `view.preflight.model_dump(mode="json")` 严格比较。W4b2b 共享夹具与 W4b1 approval creation 夹具均仍手写旧 JSON，遗漏新增默认字段；视图序列化包含该字段。

将两处默认 `_PREFLIGHT_JSON` 改为 `PreflightView(executable=True).model_dump(mode="json")`，使默认夹具随真实契约序列化。W4b1 显式 `preflight` 参数、刻意错误输入、evaluation_repo 的空字典存储测试全部保留原样。

W3b 旧 approval-first 锁顺序负控仅捕获 `DBAPIError` 且 `orig.sqlstate` 为 `40P01`（deadlock）或 `55P03`（lock timeout/conflict）。其他 DBAPI 错误重新抛出，非数据库异常自然传播；收集 `done` 任务的结果并断言 `pending` 为空，避免契约或配置异常被误计为锁错误。保留原生产 helper 调用路径、sleep 与 timeout 数值，未延长等待。

## 本子任务已执行的非数据库检查

隔离 worktree 无本地 `.venv`，因此复用协调者 worktree 的既有 Python/Ruff 可执行文件，运行时当前目录仍为本分支；未安装依赖或创建数据库。

```sh
/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/.venv/bin/ruff check --no-cache tests/gateway/w3b/db/test_lock_order.py tests/gateway/w4b1/db/test_creation.py tests/gateway/w4b2b/db/conftest.py
/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/.venv/bin/ruff format --check --no-cache tests/gateway/w3b/db/test_lock_order.py tests/gateway/w4b1/db/test_creation.py tests/gateway/w4b2b/db/conftest.py
PYTHONDONTWRITEBYTECODE=1 /Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/.venv/bin/python -m pytest --collect-only -q -p no:cacheprovider tests/gateway/w3b tests/gateway/w4b1 tests/gateway/w4b2b tests/gateway/w4b2c
git diff --check
```

结果：Ruff check 通过；三个文件格式均已符合；`322 tests collected`，exit 0；diff 空白检查通过。collect-only 不代表测试执行通过。

## 由协调者执行的实际 PostgreSQL 门禁（待执行）

本子任务未运行 Docker、实际 PostgreSQL、生产 API、完整 CI 或演示。以下命令应在协调者掌控的已配置检出中运行；环境变量只启用仓库既有临时 PG fixture，不包含连接串或口令：

```sh
GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b/db/test_lock_order.py -q
GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b tests/gateway/w4b1 tests/gateway/w4b2b tests/gateway/w4b2c -q
```

等价正式逐套入口：`make test-w3b`、`make test-w4b1`、`make test-w4b2b`、`make test-w4b2c`。实际原红/新绿与最终精确 head 完整 CI 回执由协调者另行记录，本记录不声明数据库测试通过。
