# E0b T5最后两处夹具修复（新会话，15轮，直接动手）
不要读AGENTS/PRD/其他源码，不用子agent。上轮结束，WIP保留。协调者已经亲验：全套2failed/30passed，而run_corpus_connected单独运行1passed/3.82s。**真实问题只有两处夹具污染/漏词，不是生产规则问题**。
白名单tests/evaluation/runner/test_evaluate_only.py、evaluation/runner/{l1,loop}.py（只清lint/守卫）、docs/progress/E0b-runner.md。
1. 测试黄色G3注册和invoke原话还留着“把单笔限额调到1000元”，没有卡target，所以FORM_REQUIRED是正确行为。两处都替为“把我的卡单笔限额调到1000元”；plan现有idx=text.index('1000')保留即可。
2. 两个plan helper request_id写死req_e0b_t5_freeze/tighten，gateway_db是session复用，直接ALLOW测试先缓存G4，run_corpus再用相同ID不同sid产生409。改每次plan构造唯一request_id（如uuid.uuid4().hex），不改gateway idempotency、不清库。coord已证run_corpus单独通过。
3. 立刻跑PYTEST_ADDOPTS=--tb=short perl -e 'alarm 300; exec @ARGV' make test-e0b（应32通过零skip）；ruff check --fix/format当前允许文件，只看末尾汇总。别再重新设计图/读满库。
4. 最小变异验证：备份l1.py/loop.py，暂时同时恢复真实execute与取消transport G8拦截，单跑run_corpus_connected应因真实卡冻结/新增plan_executions失败；finally恢复。若15轮不够，先提交绿色检查点并说变异未跑，别丢进度。
5. 写进度记录此前轮数耗尽与实际红绿、fakeLLM+真G2/G4/L3、无G8；本地提交所有T5改动（l1/loop/新test/进度）后立即结束。禁env/secrets/live/长期up/down/push。不要用宽松ALLOW断言、不要跳过失败测试。
