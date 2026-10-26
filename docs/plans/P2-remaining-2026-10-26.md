# P2剩余闭环与try-agent接续（2026-10-26，协调者核实）

用户顺序：E0评测与try-agent并行 → 用真实结果优先修L1 → 完成P2；前端/BFF与定时器仍暂停。main基线00aca15。

## 已有与缺口
- L1 C1a-e、L2 G3/G4/G5/G6/G8/G9/G10与W4c已合。真实respond/verify已使用C1e Adapter，旧同名helper未在生产调用；不再按DS草稿误判去重做。
- W4c38项真实PG/L3已通过，长期栈六healthy，gateway迁移0003_plan_draft。完整黄级转账尚未从真实L1 HTTP/PG贯通至确认后一次记账。
- 生产SSE default=str把Decision/ExecutionResult变成repr是已验证的L1缺陷；修复PR #33（39真PG、旧实现4红），等待精确head四CI。
- C1e记录/回放基础库存在，但生产make_llm未按LLM_MODE接线；后续独立小组件接入，不解析repr、不暗降live。
- agent.env确实存在，目前只有AGENT_SERVICE_TOKEN/AGENT_DATABASE_URL；宿主模型key已配置，协调者联调时安全注入，worker不得读取secret。

## try-agent分段
1. T1：G1登录→G15会话→G3原话→L1 threads/SSE→G9面板。CLI协议测试45通过，独立APPROVE；不直接调用确认/执行。ALLOW不等于完成，EXECUTED结果才可报告完成。当前只证明MockTransport协议与输出，不证明真P2。
2. T2：显式人工确认（测试只限明确合成场景可自动确认）→G6 view_hash/risk_ack →同sid assistant JWT resume →G8经L1执行；以L3 DB精确分金额、对方、plan_id与单次账为准。新增隔离tmpfs三层集成，禁止手种审批取代真实G3/G4/G9/G6。
3. T3：PG重建实例resume、STATE_CHANGED/UNKNOWN、不重复evaluate/execute、身份/审批错配拒绝且DB无变化；复用已验组件但补跨层证据。
4. T4：最小真实模型record→replay三遍语义一致，replay无网络fallback，记录来源/usage/模式；随后组合回归、独审、精确head CI与P2验收报告。

E0b独立工作区，必须停在G4、无G8写入。测试或live失败是修L1输入，不放宽L2确定性规则。402/403/429立即停，不换模型/循环请求。驱动运行在现有frontend容器作为受信CLI宿主，前端页面/BFF不实施、compose不新增宿主端口。

## 真实协议核对
- Gateway服务头X-Service-Token使用F token，浏览器请求app JWT；G3返回201且utterance.id嵌套，另返回assistant_token。
- Agent只收到assistant JWT，输入POST /threads/{sid}/runs/stream的input.utterances，恢复用command.resume=approval_id。
- updates.__interrupt__在真实服务中已扁平为[{value:{approval_id},id}]；GET state的tasks另有interrupts嵌套，不混用。
- 确认面板只取G9，原始业务字段不交LLM改写；红级OTP仍由用户输入/合成演示接口，不能从agent取码。

T0原始未采纳草稿保留在docs/archive/P2-T0-ds-draft-rejected-2026-10-26.md，不作为需求或代码事实。
