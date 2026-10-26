# try-agent T1协议驱动验收（2026-10-26）

范围：G1/G15/G3/L1 HTTP与SSE/G9，默认显示L2面板并停在PENDING_APPROVAL，不由CLI调用G6/G8。绿级L1可能已执行，完成必须有execution.status=EXECUTED；无执行的ALLOW为ALLOW_READY，旧repr与未知决策不猜测成功。

- 实现提交debd001（基线60d57d7）；协调者亲跑pytest tests/try_agent -q --tb=short：45 passed/0.05s；ruff check/format：通过（3文件）。
- 首轮28 mock测试绿但独审REQUEST_CHANGES，真实SSE Decision结构、无执行误报完成、stream读取/关闭、默认面板不可见、out/getpass覆盖缺口已修。
- R-TryAgent-T1-r2独立APPROVE，亲跑45协议测试，无新增P0/P1/P2；原interrupt嵌套疑点经真实源码核对不成立。
- 全部协议测试为MockTransport，未做真实栈/live/确认执行，不能据此标P2完成。生产L1结构化SSE修复另见PR #33；T2接确认/resume并做真PG/L3记账断言。
- 命令：make try-agent ARGS='--customer-id <合成客户> --text <原话>'，在现有frontend容器运行受信CLI；默认getpass，UI/BFF仍暂停。完整测试与CLI用法见进度。
