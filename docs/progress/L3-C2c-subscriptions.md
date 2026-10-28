# L3 C2c — R9 detect_subscriptions（订阅识别）

## 状态：R9 接线与真实 PG/HTTP 测试已完成（2026-10-28）

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

## 已完成（2026-10-28 接线与测试）

1. `reads.py`：`SUPPORTED_READS` + import + `detect_subscriptions` 分支
   （仅 `customer_id`、`lookback_days=args.lookback_days`）。提交 `5efd853`。
2. `test_reads.py` 新增 3 个 R9 真实 PG+HTTP 测试（时间均带时区）：月度订阅识别、
   非周期商户不计、他客户数据不出现。提交 `1d45e02`。
3. 真实校验：`make lint` 全绿；`make test-obligations` 69 passed（含 3 条新 R9）；
   `.venv/bin/python -m pytest tests/bankcore -q` 210 passed。
4. 额外修复：`test_analytics.py` `_sub_tx` 的 E501（110>100）长行，抽出 `suffix`
   变量（语义等价）。随 `1d45e02` 提交。

### R9 无 account_id 的说明

`DetectSubscriptions` 契约只有 `lookback_days`（PRD §9.3），无 `account_id`。
任务书第 4 条「他客户 account_id 返回 404」不适用于 R9（R4/R5/R11 才有该机制）；
隔离性由第 3 条「他客户数据不出现」测试覆盖，未放宽/删除任何断言。

## 约束

不 push、不 make up/down、不删数据卷；时区修复（账户余额曲线用 `_business_time`）
已在 `4ebe304` 单独提交完成。
