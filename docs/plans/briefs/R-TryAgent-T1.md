# try-agent T1独立定点协议评审
你是全新DeepSeek V4 Pro只读评审，25轮，不用子agent，不读.env/.secrets。第一步写docs/reviews/TryAgent-T1-review.md骨架，边核对边追加。只能写报告。当前分支冻结T1 driver，协调者亲跑28 tests通过、ruff通过，但mock可能与真实API不符。请审当前HEAD相对00aca15的scripts/try_agent.py与tests/try_agent/test_driver.py、Makefile及进度，不把mock绿当真实P2通过。
只读最多6文件：driver/tests；services/agent/app/main.py:265-510真实SSE；services/agent/app/graph/state.py Decision模型；services/gateway/app/api/approvals.py G9真实面板；Dockerfile仅运行位置。
协调者发现的明确候选问题逐条核实：1. 真实main.py的updates.__interrupt__是task列表，每task有interrupts[]里面value.approval_id，driver却只查顶层value，可能拿不到审批；2. values.decision是真实Decision字典而不是字符串，driver比较字符串会全部UNKNOWN；3. CLI把所有events留内存，默认只打印EXIT，G9面板并未实际呈现给用户；4. 非200 stream没read却取.text可能ResponseNotRead且未close；5. 对ALLOW不应报告COMPLETED，必须依据execution.EXECUTED结果且无执行不得伪成功；6. 脱敏仅检查Bearer eyJ/变量名，HTTP/SSE错误响应回显真实token是否泄露，输出/out均查；7. --out/getpass实际行为测试覆盖。
允许pytest tests/try_agent -q --tb=short，无live、无Docker、无git mutation。产出P0/P1/P2、具体行号、最小复现与建议/未覆盖。结束简短报告结论。不得改实现。
