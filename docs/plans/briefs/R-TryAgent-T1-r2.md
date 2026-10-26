# TryAgent T1修复独立定点复审（20轮）
全新DeepSeek只读review，不用子agent，只更新docs/reviews/TryAgent-T1-review.md追加修复核对节。当前HEAD debd001，对比60d57d7..debd001，仅读driver/tests/进度+原评审。协调者已指出interrupt疑点不成立，原报告不得重复当待修。
核P0 decision结构化解析/旧repr failclosed；ALLOW_READY而非无执行COMPLETED（完成还应plan_id匹配execution）；面板真实默认呈现、不改写；HTTP/SSE错误不泄漏真实服务token/JWT/PIN，包括--out文件；非200流有read+finallyclose，连接异常无traceback；getpass/out真实main测试；不接受ALLOWED/COMPLETED等非法决策别名。不得建议解析repr猜决策。真实L1 SSE编码缺陷已由独立分支efde4d7修复并39真PG通过，尚未合入，本driver独审不伪称完整P2。
只读pytest tests/try_agent -q --tb=short与必要ruff可运行，不调用live/Docker/外部URL，不读.env/.secrets。逐条ADDRESSED或可复现发现，结束给APPROVE/REQUEST_CHANGES、未覆盖。报告骨架先写，不改生产或提交。
