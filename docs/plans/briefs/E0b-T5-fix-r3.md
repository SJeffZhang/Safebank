# E0b T5收尾r3（只做此项，25轮）
新DeepSeek会话，不用子agent，不读大AGENTS/PRD。r2已用尽41轮，WIP保留；协调者亲跑3failed/29passed。白名单仍evaluation/runner/l1.py、loop.py、tests/evaluation/runner/test_evaluate_only.py、docs/progress/E0b-runner.md。不要再设计，按下面已核事实修。
1. **真实get_cards返回{items:[...]}，不是cards！** 证据services/bank-backend/app/reads.py:134-142。测试gather取result.data['items']，plan取reads[0].data['items'][0]，来源pointer='/items/0/id'。此前W4c手种cards记录形状不能复制到真实G2。
2. 黄级原话用“把我的卡单笔限额调到1000元”，来源span按text.index('1000')与+4准确生成；REQUIRE_APPROVAL会interrupt而不是调用execute终止信号，删除“黄级必须raised EvaluationComplete”这个错误断言，保留严格decision==REQUIRE_APPROVAL、零G8/零L3变化。若解析FORM_REQUIRED如实记录，不手造scope放过。
3. run_corpus当前evaluate_only默认False仍不安全。评测入口必须默认且不可绕过G4-only：删False执行分支或拒绝False，transport默认也拒G8，不能只改测试显式传True。
4. test_evaluate_only_run_corpus_connected现在仍直接graph.invoke，没有测run_corpus！替换为真实await run_corpus(h,cases,ports_factory=...,repeat=1)，不传evaluate_only开关，fake ports在gather用真实G2 client+当前context（可外面构建另一个RecordingBridgeTransport+GatewayClient，gather只在to_thread中调用，读记录同DB/同sid有效）。断言结果ALLOW，真实卡ACTIVE、plan_executions/transactions前后不变，真实transport零G8。切勿图helper当runner。
5. ALLOW测试捕获EvaluationComplete合法；黄色interrupt不需捕获。不要伪造UNKNOWN，不放宽==ALLOW/==REQUIRE_APPROVAL，不改services。
立即修后跑PYTEST_ADDOPTS=--tb=short perl -e 'alarm 300; exec @ARGV' make test-e0b（零skip），ruff check与format，结果写进度。绿色后临时取消G4-only execute/transport守卫验证真实run_corpus用例失败，备份还原。若轮数将用尽，先提交检查点并报告真实失败；完成本地提交后停止。禁live/.env/secret/长期up/down/删卷/push。
