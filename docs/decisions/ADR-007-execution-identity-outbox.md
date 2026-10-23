# ADR-007：解析后摘要、迁移与 outbox 恢复

日期：2026-10-23。状态：采纳，P1 实测通过。

PRD §6.2 / §8.5 要求 `steps_digest` 绑定解析后的实体。prepare 从唯一状态加载器取得目录/已有收款人，使用 bankcore 纯函数解析新建与复用 ID 后计算摘要；execute 在客户锁和版本检查后重算，不一致返回 STATE_CHANGED。原始请求另存内部 `request_digest`，用于校验重试身份，排除不参与业务身份的 arg_sources。该字段不改变 HTTP 请求。0002_identity 迁移要求执行表为空，拒绝将旧摘要猜测为新语义；0001 的 DDL 已固化到 schema_v1.json，避免未来模型变化修改历史迁移。

outbox 的 `after` 是一轮分页位置，不能当永久提交水位：不同客户事务可能按相反顺序提交 sequence。接口只返回未 ack 的已提交事件；消费者每轮/重启均从 after=0 扫描，按 event_id 幂等处理再 ack，避免漏掉晚提交的小序号。下一页仅用于本轮遍历。此规则补入 PRD §8.5，P2 消费器必须遵循。
