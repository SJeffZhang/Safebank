# TryAgent T1修复轮2：真实wire与可见输出
全新DeepSeek impl40轮，不用子agent，不通读AGENTS。当前冻结60d57d7，独审报告docs/reviews/TryAgent-T1-review.md为REQUEST_CHANGES，报告有不成立项和不安全建议，以本裁定为准。只改scripts/try_agent.py、tests/try_agent/test_driver.py、docs/progress/TryAgent-driver.md。先补行为红测再修，完成即本地提交停止。

协调者核实：真实main.py:449-455已把interrupt扁平为[{value:{approval_id},id}]，当前driver解析正确，不改；真实values.decision是Pydantic经default=str变成repr字符串，这是L1服务缺陷，后续单组件修L1，**驱动绝不能用正则解析repr来猜决策/成功**。驱动支持结构化Decision字典，旧repr/未知枚举明确PROTOCOL_ERROR/UNKNOWN，不能报成功。测试必须用真实Decision.model_dump(mode=json)以及一个repr反例，不再mock裸ALLOW就当全部正确。

只修四组：
1. 路由读取structured decision.decision与execution.status；ALLOW仅是授权，不是完成，只有ExecutionResult.status=EXECUTED且plan_id匹配才COMPLETED。尚未执行时标ALLOW_READY（此T1不自行调G6/G8，但真实L1在绿级可能已G8执行，不能假定无执行）。DENY/REPLAN/clarification按真实state字段，UNKNOWN不得报成功；删除ALLOWED/COMPLETED等无效决策别名。
2. main默认实际显示L2 G9面板/有效reply/结局（现在events只内存，用户只见EXIT）。面板来自G9原JSON，脱敏后输出，但不重写业务值。不自动审批。--out同一脱敏events。
3. 所有HTTP错误不回显rawresponse文本（上游可能回显secret），只显示状态+可信code；httpx流用with client.stream或try/finally显式close，非200先read再访问内容；连接错误有明确退出不traceback泄露。清理没用的header helper或真正用起来，别增加错误body日志。
4. 测试补实际main() stdout与--out文件+getpass、服务token/appJWT/assistantJWT/PIN哨兵均不出现（上游回显这些值也不能漏）、non200真正ByteStream避免静态bytes掩盖ResponseNotRead；确认仍零driver G6/G8，正常pending面板可见。

L1生产wire修复归另一任务，当前不改services/**；报告说明其阻塞真实driver验收，不伪装CLI已闭环。退出 .venv/bin/pytest tests/try_agent -q --tb=short；ruff check/format --check这两路径；python -m scripts.try_agent --help。禁止live/.env/.secrets/up/down/reset/push。只看末尾汇总。写真实红绿与未覆盖，提交后结束。
