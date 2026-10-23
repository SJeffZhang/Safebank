# 0002_wave1 汇总清单（协调者，尚未实施）

用户禁止W1–W4编写迁移；仍由协调者统一、串行追加，不修改0001。四路完成独立检查点与待决清单后先完成必要迁移，再做完整联调与最终退出，不能在缺列状态假装组件完成。涉及执行状态的新枚举在独立架构审查前不定稿。

| 来源 | 表 / 字段 | 类型与兼容策略 | 必需理由 |
|---|---|---|---|
| W3/W4 | evaluations.view_json | JSONB，可空以兼容已有W0行；新评估必须完整写入，缺值不得确认/执行 | 保存当时实际PlanView供G9与view_hash一致性核验，不能向公开Decision JSON夹内部字段 |
| W4 | otp_challenges.code_ciphertext | Text nullable；重发同事务替换 | D5模拟短信需要重启后可读取；nonce+ciphertext封装，AEAD密钥从OTP_HMAC_KEY域分离，AAD用无歧义编码绑定approval_id/view_hash；验证仍只用code_digest，密文不向A/F输出 |
| W4 | execution_jobs.authorization_evidence | JSONB nullable兼容旧无业务W0行；新任务必填，UNKNOWN恢复缺证据拒绝继续提交 | 同attempt重试必须逐字节使用原证据，不能临时重建成不同请求 |
| W4 | execution_jobs.deadline_cap_wall | timestamptz nullable兼容旧行；新任务必填且后续不可扩展 | 持久原话/持久JWT期限上限，重新审批和恢复不得延长 |
| W4 | execution_jobs/plan状态CHECK | 未定；先独立协议审查 | 原EXECUTING不可回退与L3明确STATE_CHANGED后的重新审批冲突，需要证明未提交、claims持久占用和重试状态一起闭合 |

已消除的候选增量：
- W1无新增表列；澄清状态合法边由S1实现。
- W2 clock_version暂不新增：保持PRD7.5 ReadEnvelope；versions+observed_at+有效sid检查，G2夹读检查clock_version，reset必须撤销所有会话/读取上下文。
- W3首次收款人使用现有全历史transactions；拒绝窗口取评估/审批拒绝审计wall时间，不用plans.created_at_wall。

迁移验收：空库upgrade两次、由0001带数据升级且数据保留、运行角色权限、CHECK/索引约束、旧缺证据行failclosed、state/tables与迁移一致，真实PG。不删或重建数据卷，不TRUNCATE现有业务库。迁移及应用schema版本/ready检查同PR，独立评审、亲自复跑、CI全绿merge commit。

L3义务/产品/AA的新增存储属于独立银行领域迁移链，不能混进gateway 0002_wave1；需先在对应任务书列具体表与增量，再串行实现与验收。
