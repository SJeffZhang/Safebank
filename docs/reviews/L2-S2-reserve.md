# S2 预留纯函数独立评审

2026-10-23，review_s2，HEAD8a7d6ddd4b71dacadf1df20b1f8e29846cbbb794，对照PRD与S2任务书。

结论：REQUEST_CHANGES，1项P1。R1：reserve.py:57直接比较time_of_day与next_run_at.time()，weekday/月末校验和递推亦依赖输入offset。PRD0.1业务时区Asia/Shanghai；同一瞬间2026-10-18T06:00+08:00改为2026-10-17T22:00Z，合法ScheduledView在scheduled_occurrences和projected_shortfall均报inconsistent local execution time。应先归一业务时区，再校验本地时刻、weekday、anchor_day和递推；补ONCE/WEEKLY/月末与年界多offset等价测试，错误本地时刻仍拒绝。

评审实际定向73 passed（0.08s）并独立probe复现；首次probe缺PYTHONPATH失败，补路径后复现。协调者先前完整四退出均exit0，不能覆盖该未测边界。预留公式、7天边界、实际账单金额、月末锚点、本人互转、负上限、295500场景符合任务书。S3生产快照仍待实现。原owner修复轮1中，未开PR。
