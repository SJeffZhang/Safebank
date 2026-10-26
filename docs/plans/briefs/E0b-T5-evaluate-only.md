# E0b T5：G4-only评测隔离与真实正例回归

用户2026-10-26恢复E0评测与try-agent并行，之后先修L1再验收P2。本次只做E0b T5一个任务，不一口气完成整个E0b。你是DeepSeek V4 Pro实施worker，全新会话60轮，禁止子agent。不要通读巨大AGENTS/PRD；此处为最新定点裁定。

已完成T1–T4，原ba76448刚合入main00aca15。Makefile冲突已由协调者保留test-e0b/test-w4c。已知关键事实：evaluation/runner/l1.py:build_graph_with_ports 使用make_gateway_ports+build_gateway_graph，execute目前自动绑定真实G8。旧G8占位时没有显现，如今W4c已实现，E0必须只到真实G4、绝不发G8。不能通过让合法计划变REPLAN/篡改L2决策来阻止执行。

只改：evaluation/runner/l1.py（必要时loop.py）、tests/evaluation/runner/test_evaluate_only.py、docs/progress/E0b-runner.md。禁止services/**/packages/**/AGENTS/PRD/Makefile/CI/依赖/语料改动；缺范围先报告。

先读最多7文件：evaluation/runner/l1.py、harness.py、loop.py；tests/evaluation/runner/test_l1_one_case.py与conftest.py；services/agent/app/graph/graph.py和ports.py（只读）。可定点看gateway/adapters中的make_gateway_ports签名，不全库扫描。

第一步前5轮创建test_evaluate_only.py骨架。使用既有真实tmpfs gateway_db+obligations_db和eval_harness，fake LLM端口产出来源合法的GREEN冻结计划（gather需用真实G2 get_cards形成read记录，引用该record；不要沿用typed span伪造card_id）。先让真实G4得到ALLOW，再断言：没有任何POST /plans/*/execute请求，真实L3卡状态未变、plan_executions/ledger写入计数前后不变。该红测在当前实现应失败于发G8/真实卡变化，而不是来源不合法或收集错误。另一个合法YELLOW计划得到REQUIRE_APPROVAL并停下，既不确认也不发G8。

实现最小评测边界：可以在评测专属图/端口或transport设显式evaluate-only停止，保留真实Decision并正确归类。不要更改生产图、G4风险、授权规则；不能吞异常冒充成功、不能伪造ExecutionResult。防御到transport能明确拒发G8更佳，但须保持正常ALLOW统计可用。最终case outcome按G4，真实L1图/读取/来源/G4保留，fake只在模型生成。

退出：perl -e 'alarm 300; exec @ARGV' make test-e0b（零skip）；.venv/bin/ruff check evaluation/runner tests/evaluation/runner；.venv/bin/ruff format --check 同路径。每命令单独运行，不用;或&&串联。只看末尾汇总。测试仅临时tmpfs容器，禁长期栈up/down/删卷/reset、禁读取.env/调用live、禁git push。进度记录红→绿真实输出与未覆盖，然后本地提交允许文件，结束给SHA/测试结果/边界。若402/403/429或连接错误立即停，launcher会快照WIP。
