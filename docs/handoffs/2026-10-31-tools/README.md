# 10-31交接辅助脚本

完整状态、限制和旧工具见上层`2026-10-31-freeze-handoff.md`。

- `clock_advance_proof.py` 在隔离gateway内执行，凭据仅从env读取；固定request IDs和三次推进目标，仅适用于新reset的数据集。它会触发真实定时/代扣/基金事件。
- `form_live_smoke.py` 在隔离operator内执行，PIN从stdin读取，F令牌从env读取。它会真实转账200元给赵六并冻结张三卡，输出只有合成业务ID/结果。仅在新隔离数据集用于验收。

推荐由本机Python加载`DemoEnvironment`并读取当前私有overlay，将脚本内容作为`python -c`参数、PIN作为compose的stdin数据传入。不要在命令行直接写PIN，不要把PIN/OTP/token打印到日志。相同环境已有业务修改时先检查状态，不要盲重跑。这些脚本不取代SQL核验、六场景runner或43条报告。

旧工具`../2026-10-30-tools/`已在main：local_ci.sh、run_full2.sh、drive_try_agent.py、snap3.sh、full_cases2.tsv。私有compose覆盖文件和密钥未复制。
