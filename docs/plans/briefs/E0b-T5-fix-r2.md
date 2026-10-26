# E0b T5 修复轮2：修正API事实，真正接入G4-only边界

全新DeepSeek会话，40轮，DS thinking6000，禁止子agent。上轮60轮用尽，WIP自动快照refs/wip/E0b-T5-r1，不丢弃或checkout文件。协调者亲跑test-e0b为2 failed/29 passed；失败原因不是行为红测而是读参数非法。只允许evaluation/runner/{l1,loop}.py、tests/evaluation/runner/test_evaluate_only.py、docs/progress/E0b-runner.md。

逐项执行，不满库探索，当前实现关键缺陷与精确改法：
1. 原build_graph_with_ports（l1.py约136）仍绑定真实execute，run_corpus仍调用它！新增build_evaluate_only_graph只在测试用没有保护真实runner。必须让run_corpus实际使用的路径停在G4；禁止以独立未接入helper冒充完成。
2. 新_EvaluateOnlyExecute返回UnknownExecution是捏造执行结果，不能采用。使用评测专属明确终止信号EvaluationComplete（可以异常），真实execute入口之前终止，run_corpus仅将此专用信号识别为评估完成；保留G4 decision与数据，不吞其他异常。不要修改生产图。transport层额外拒发G8可做。
3. CardsQuery只有account_id，合法G2 args={"account_id":None}，无customer_id；不能用assistant_token="prelim"假授权、更不能从事件循环线程同步调用RecordingBridgeTransport（死锁）。删除_fetch_card_id/_fetch_payee_id前置调用。只在graph运行的to_thread中gather(state,context)调用真实client.read_tool，context就是当前sid/JWT；从result构造services.agent.app.graph.state.ReadRecord，返回list[ReadRecord]。plan读取state['reads'][0].source_ref/data.cards[0].id，不用闭包伪造prelim读记录。
4. PlanDraft内部read source字段名是pointer，不是path；typed来源kind=utterance，不是typed。先仅冻结例达到真实G4 ALLOW（必须断言==ALLOW，不能in所有决策），再核卡仍ACTIVE/plan_executions和transactions计数不变/transport无G8。原assert not eval_exec.called不合理：终止端口可被调用，核心是零G8与零L3变化；不要伪造UNKNOWN换绿。
5. 黄级正例改用update_card_limits而非不存在transfer_external。按tests/gateway/w4c/db/support.py:_limit_tighten_step精确形状：tool=update_card_limits,args={card_id:读真实卡id,single_limit_cents:100000,daily_limit_cents:None}；card_id read来源pointer=/cards/0/id；single_limit_cents来源utterance/ref本utt/span准确覆盖'1000'。G3原话用自然中文将卡单笔限额调到1000元（不手种scope），仅根据真实G4失败修来源，不放宽L2；黄测必须==REQUIRE_APPROVAL并无确认/无G8/无L3变化。卡表原限额500000。
6. 额外最小测试必须经run_corpus真实入口运行ALLOW冻结，用真实L3断言未执行，证明边界接入runner；不只测试新增helper。
7. 前5轮先修测试合法读参数与来源类型，然后运行PYTEST_ADDOPTS=--tb=short perl -e 'alarm 300; exec @ARGV' make test-e0b。只看末尾失败摘要，禁止多次通读同一文件。环境GATEWAY_DB_TESTS/OBLIGATIONS由Makefile设，不要裸pytest导致skip。
8. 行为红绿/变异：在合法ALLOW+run_corpus测试跑通后，临时恢复真实execute，确认测试因G8/卡状态改变而失败；备份还原，不要checkout吞WIP。退出同test-e0b零skip、ruff check/format两目录。写进度真实结果；完成本地提交并结束。禁读.env/.secrets、live、up/down/reset/删卷、git push。轮数不足时先提交清晰检查点，报告真实失败，不宣称完成。
