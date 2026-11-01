# 11-01 年度订阅理解及SSE错误

报告25在“我有哪些订阅，一年总共要花多少”产生非法span=[0,0]，既有一次修复只反馈异常类型，最终把Pydantic原始异常透传页面。保留一次预算与严格span校验，不猜补坐标、不增加模型重试。

Understand提示明确0起始[start,end)及0<=start<end；修复只反馈固定Schema字段路径与约束，过滤任意extra-key、原输入、异常上下文和文档链接。402/403/429从invoke直接抛出，一次调用即停止。

SSE统一返回稳定code/message/type，结构错误MODEL_OUTPUT_INVALID，其他异常AGENT_REQUEST_FAILED；服务端保留诊断。限额保留PROVIDER_LIMIT_REACHED及数值status_code，不含provider正文。通用错误可能发生在执行后的回复阶段，因此不会声称业务必然未执行，提示刷新核实实际状态。HTTP鉴权错误处理不变。

旧纯测6失败/1正控通过，真实Agent PostgreSQL checkpoint+HTTP/SSE两项均失败；首次SSE夹具漏导入证书fixture造成的setup错误不计产品红测。修复后68项相关纯测通过，新增任意extra-key及三种provider限额单次调用负控在内；真实PG/SSE两项通过，核实仅understand被调用、下游零次、页面无异常细节。日志/private/tmp/sbg-nov1-understand-errors-{red,green2}.log与sbg-nov1-safe-sse-{red2,green}.log。独审及最终完整head CI另核；mock输出不代表已重新请求生产模型或重跑原演示。

完整CI在b913932的Agent PG阶段1失败/41通过：旧test_sse_wire仍要求SSE.type=TypeError，与已审公共AgentError契约冲突。只更新该断言，新增公共code/无内部类型和消息检查，并保留exc_info且明确服务器仍记录TypeError；不改产品、不去日志检查。完整Agent PG42通过/1071 deselected，日志sbg-nov1-agent-db-contract-green.log，随后对包含此测试修订的新40位head重跑全部CI，b913部分结果不称PASSED。
