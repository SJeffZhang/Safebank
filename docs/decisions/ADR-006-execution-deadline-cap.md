# ADR-006：prepare 显式携带不可变授权截止

2026-10-23；采纳。P1实现发现 PRD §8.5 要求从首次原话/会话期限保存 deadline_cap_wall，但原请求只给 deadline_wall=min(审批/原话/会话)，L3 无法恢复原话/会话截止，无法正确允许新审批 attempt 延长至原授权上限。

在内部 prepare 请求增加必填 deadline_cap_wall，由可信 L2 计算并传递；首次持久化后不可改变。deadline_wall 不得超过cap；同attempt不能延长，attempt+1仅在PREPARED且不越cap时更新。L3不替LLM决定授权。同步PRD §8.5与共享契约；无已部署P1调用方需要迁移。
