# L1 SSE结构化编码独立评审（新DS会话20轮）
只读评审当前HEAD相对00aca15的services/agent/app/main.py与tests/agent/test_sse_wire.py；只写docs/reviews/L1-P2-sse-wire-review.md。第一步写报告骨架，逐项写结论。不要子agent，不通读AGENTS/PRD，不读.env/.secrets，不调用live。
问题：旧json.dumps(state,default=str)将Pydantic Decision/ExecutionResult转repr字符串，try-agent无法可靠解析。改为_state_json_default：BaseModel.model_dump(mode=json)、Decimal字符串、datetime/date ISO，不支持类型TypeError；updates/values共用。禁止改授权/状态/路由。
协调者真实PG test-agent-db=39 passed（新增5项），旧main变异test_sse_wire=4failed/1passed，已恢复。检查这是否充分证明真实HTTP/SSE/GET state一致、已执行与仅ALLOW不混淆、整数分/Decimal不float、无token泄露/无不必要helper能力、原interrupt扁平格式保持。无需重跑Docker；可离线读模型/测试与定点unit/ruff。重点不要因为已有测试绿就跳过独立检查；给可复现P0/P1/P2，或APPROVE，准确注明自己跑没跑的命令。
