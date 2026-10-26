# E0b T6：逐样本证据完整归档（一个独立小任务，35轮）
新DeepSeek impl会话，不用子agent，不通读AGENTS/PRD。T5已保存检查点，当前32项31pass/1fail为独立生产审批配置503，另worktree修复中；不要修harness/global或生产。
白名单evaluation/metrics/compute.py、evaluation/runner/loop.py、tests/evaluation/runner/test_case_evidence.py、docs/progress/E0b-runner.md。最多读这3个已有文件+test_metrics.py的CaseRun构造与write_outputs用法。前5轮写新测试骨架。
任务仅解决当前cases.jsonl信息不足：CaseRun只有第一步actual_params/outcome/token=0，缺原话/完整PlanDraft/arg_sources/L2决策/findings/失败明细。给CaseRun追加向后兼容的原始证据字段（默认空），run_corpus每轮收集该轮原话（多turn/session_break保留）、实际最后一次G4请求plan_draft全步骤与arg_sources、G4响应decision/findings、所有G4尝试状态/错误code（不要把503误记成没有计划而丢诊断）。write_outputs完整写到cases.jsonl，meta能区分fake/live/replay与缺usage；字段名称清楚即可，旧指标含义本任务不改。
输出前递归去掉授权/密钥/PIN类字段和Bearer/JWT串；不能直接写headers/G3assistant_token，不序列化AgentContext或clients。所有为合成数据，但仍须测secret哨兵。不能把未取得usage的真实模型调用写成已知0；新增usage_known/usage=None等标志，本任务还不接模型端口、不跑live，保留旧tokens字段兼容并明确其是否已知。
测试以纯函数/协议fake harness为主，不依赖真实HMAC修复：已知多步PlanDraft两个不同金额/来源都出现在JSONL（而不只第一步），多turn原话完整，真实G4错误code不丢，写文件后可读且secret哨兵不出现；旧17项metric测试仍通过。可以直接测试新提取函数，并用existing run_corpus轻量替身证明调用接入；不声称真PG覆盖。T5真实测试仍已知1失败，别删除/skip它。
退出：pytest tests/evaluation/runner/test_metrics.py tests/evaluation/runner/test_case_evidence.py -q --tb=short；ruff check/format改路径。写进度实际结果，立即本地提交允许文件并停止。不要CLI/并发/LLM工厂/生产修改，不读.env/.secrets，不调模型/up/down/push。
