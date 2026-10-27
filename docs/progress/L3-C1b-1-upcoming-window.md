# L3 C1b-1: 义务窗口扩展到 1–30 天

**日期**: 2026-10-27
**分支**: feat/l3-c1b-upcoming (基于 main 25a8954)

## 变更

| 文件 | 变更 |
|---|---|
| `packages/bankcore/src/safebank_bankcore/reserve.py` | 新增 `UpcomingWindow` dataclass、`_compute_window` 共享计算核心、`upcoming_window(state, *, days, account_id=None)` 公开函数；`projected_shortfall` 改为调用 `_compute_window` |
| `packages/bankcore/src/safebank_bankcore/__init__.py` | 导出 `UpcomingWindow` 和 `upcoming_window` |
| `tests/bankcore/test_reserve.py` | 新增 34 项测试 |

## 接口

```python
def upcoming_window(
    state: BankState, *, days: int, account_id: str | None = None
) -> UpcomingWindow
```

- `days`: 1–30，越界抛 `ValueError`
- `account_id`: 可选，仅返回该账户的义务与投影
- `UpcomingWindow`: `accounts: Mapping[str, AccountProjection]` + `obligations: tuple[Obligation, ...]`
- `max_outflow_cents` 可能为负，如实返回不截断

## 退出命令结果

- `make test-reserve`: 120 passed ✅
- `make test`: 2931 passed, 784 skipped ✅
- `make lint`: All checks passed ✅
- `make contracts-check`: PASS ✅

## 提交

```
3b308f6 feat(L3): add UpcomingWindow, upcoming_window + validation tests
d8780f4 feat(L3): add upcoming_window days=1/7/30 and monthly/weekly tests
2dbafd0 feat(L3): add upcoming_window boundary, leap year, outflow, account filter tests
70a3f70 style: ruff format and lint fixes
```

## 未覆盖

- 边界天数值（days=30 极端值）通过参数化测试覆盖
- 30 天窗口内月频多次出现（受限于日历，最小月间距 28 天 ≤30 天，但实际能触发 2 次的场景极少——仅 1 月 31→2 月 28 对，与现有 `test_monthly_retains_original_31_anchor_through_short_month_and_leap_day` 重复，未新增独立测试）