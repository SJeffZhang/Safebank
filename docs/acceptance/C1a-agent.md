# C1a：LangGraph 图骨架、状态与中断恢复

2026-10-24。实现业务最终 `8ed1f7c`，原 DeepSeek reviewer `89c4f3b6-fe87-4656-8ac1-3c2d19db8aff` 最终 APPROVE，无未闭项；报告 docs/reviews/C1a-agent-review.md。

真实 LangGraph StateGraph + Runtime context + 内存 checkpointer，通过显式类型化端口运行 ingest/understand/gather/plan/evaluate/await_approval/execute/respond/verify_output。未提供依赖即失败；每轮重置字段；新输入使旧 pending interrupt 失效；恢复必须验证 thread/session/customer 绑定且不中途重跑已执行节点。未开放业务 HTTP，现有健康探针不代表完整银行 Agent。

协调者在 8ed1f7c 亲验五退出全部 exit 0：make lint（212 formatted）；pytest tests/agent -q **22 passed，零 skip/xfail**；make test **1959 passed / 461 skipped（6.06s）**；make contracts-check PASS；actionlint。日志 `/private/tmp/sbg-codex-c1a-r2-20261024`。最终独审另亲跑 22 passed。

协调者早期额外探针发现错 customer/sid 仍可调用 execute，原 worker 在 interrupt 前加入图内恢复绑定，并以真实 LangGraph 拒绝与正确恢复回归关闭 P0。最终三种拒绝场景均断言 execute/respond/verify_output 为零；错 thread 用例明确走 fresh ingest guard，未冒称 resume guard 覆盖。

LangGraph input_schema 不强制运行时必填键，裸 graph.invoke({}) 会合并旧状态；唯一新输入边界 invoke_new_input 在触碰 checkpoint 前验证 utterances，并由 ingest 兜底。C1d 必须使用经审查的同步/异步等价入口，禁止直接暴露裸 invoke；该义务已写入任务书与 README。运行时助手 JWT/服务令牌不入 state/configurable/checkpoint。

边界：只内存 saver 与 fake 端口。真实 DeepSeek 计划生成、真实 L2 HTTP、PG saver/线程锁、服务入口/SSE、回复校验与回放留 C1b–e。make demo 尚未实现，本组件不能宣称 demo/replay 通过；序列化告警留 C1d 处理。后续同步 main 只包含启动脚本与协调文档，不改已验业务，精确发布 head 四 CI 待核。
