# L3 C2c — R9 detect_subscriptions（订阅识别）

## 状态：纯函数部分完成，R9 接线与真实 PG/HTTP 测试待做

## 本次已完成（2026-10-28，续做）

- 运行现状：`tests/bankcore/test_analytics.py` 原 71 项中 5 项失败，66 通过。
- 修复实现缺陷：`detect_subscriptions` 候选扣费只取 `MANDATE`（原 `_CONSUMPTION_KINDS`
  把 `CARD_SPEND` 也算作扣费，导致到店消费污染周期判断）。`CARD_SPEND` 仅作使用信号。
- 修复测试数据缺陷：
  - weekly 两条测试日期 `10-06/10-13/10-20` 中 `10-20` 晚于 `sim_now`(10-15)，
    改为 `09-22/09-29/10-06`；
  - `test_r9_mandate_not_counted_as_usage` 的近期 MANDATE `10-01` 与 `09-25` 只隔 6 天，
    破坏月度周期，重构为 `06-25/07-25/08-25/09-25` 四条月度 MANDATE + `07-17` 到店消费。
- 复跑（真实命令与结果）：
  - `.venv/bin/python -m pytest tests/bankcore/test_analytics.py -q -k r9` → **25 passed, 46 deselected**
  - `.venv/bin/python -m pytest tests/bankcore/test_analytics.py -q` → **71 passed**
- 检查点提交：`555bc98 feat(L3): R9 detect_subscriptions pure function and tests`

## 采用阈值（需向协调者报告，PRD 未定义项）

- 典型金额方法：**median**（默认，另支持 `floor_mean`），PRD 未规定 → 记为未定义项。
- `variance_tolerance_bp=500`（5%）、`min_occurrences=3`、月度区间 28–31 天、周 7 天
  （以上为 PRD §5.4 已定）。

## 待做

1. `services/bank-backend/app/reads.py`：`SUPPORTED_READS` 加 `detect_subscriptions`；
   import 加 `detect_subscriptions`；新增 `elif tool == "detect_subscriptions"` 分支
   （仿 `detect_anomalies`，参数 `lookback_days=args.lookback_days`）。
2. `tests/obligations_data/test_reads.py`：R9 真实 PG+HTTP 测试（带时区时间）。
3. 校验：`perl -e 'alarm 900; exec @ARGV' make test-obligations | tail -3`、
   `make lint`、`.venv/bin/python -m pytest tests/bankcore -q | tail -1`。
4. 向协调者报告采用阈值、PRD 未定义项、测试数、提交 SHA。

## 约束

不 push、不 make up/down、不删数据卷；时区修复（账户余额曲线用 `_business_time`）
已在 `4ebe304` 单独提交完成。
