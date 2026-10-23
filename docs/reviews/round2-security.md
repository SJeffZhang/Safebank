# 第二轮审核：安全授权闭合

审核者：Codex subagent `review_security`。日期：2026-10-23。

范围：已读 AGENTS.md、现行 PRD v1.2 及 `PRD-v2.1-wip.md` §0–§16；重点检查 F/A/D/S 身份边界、原话与表单授权、审批、并发/超时恢复、定时 grant、基金系统事件及跨会话规则。以下行号对应本轮初始 1583 行工作稿，后续修订应按章节与引文定位。只读审核 PRD/AGENTS，仅创建本报告。没有运行代码、服务、API 或代码测试；仓库尚无实现。本报告不是运行安全认证。

结论：未发现此前 app/assistant 混用、调用方自报 origin、普通客户直接读 OTP 等 P0 漏洞仍被规范允许；发现 2 项 P1 闭合阻塞和 1 项 P2 契约矛盾。修复前不建议宣称授权全链路定稿。

## S2-01 [P1] UNKNOWN 恢复要求证明“没有在途执行”，但没有产生该证明的协议

- 位置：§7.10 第 7 项，1029；§8.3 `plan_executions`，1099；§8.5 执行查询，1123–1124；§11.3，1477。
- 原文要求查询不存在且“确认没有在途执行”才重试，同时明确 404 不是在途请求失败证明。L3 只保存已提交成功结果；没有持久接收/取消状态，也没有阻断延迟 execute 的接口。
- 可复现时间线：L2 持久化 EXECUTING 并发出请求；L3 尚未提交或请求在网络上延迟；L2 超时/重启；恢复查询返回 404。此时与“原请求已永久丢失”的状态完全相同，现有接口不能区分。严格遵循规范会永久停在 UNKNOWN，并阻塞 reset；用超时推断失败则可能在宣布失败、消耗/重新授权后接受迟到请求。
- 精确修复建议：增加持久 `execution_intents` 和与 execute 共用同一 intent 行锁的 reconcile/取消栅栏。prepare 幂等保存 immutable hash、首次 admission/deadline；execute 在锁内检查状态与截止，成功把结果和 COMMITTED 一次提交。reconcile 在同锁下返回 COMMITTED，或写入不可逆 CANCELLED，使迟到 execute 必须失败；获取锁超时继续 UNKNOWN。重复 prepare 不得复活 CANCELLED、改变 hash 或刷新 deadline。只有获得确定取消证据后才能宣称 FAILED；新计划要求新的用户授权。
- 必须同时明确：同一授权作用域被 EXECUTING **及 UNKNOWN** 持久占用，不能只靠易失 advisory lock；截止在获得 intent 锁后验证；prepare/execute/reconcile 一致锁序；业务失败回滚留下的 PREPARED 也由 reconcile 终结。
- 建议验收：延迟请求在 reconcile 前后到达；事务已提交但回包丢失；进程崩溃；重复 prepare；锁等待跨截止；UNKNOWN 期间不同 plan 争抢同授权。仅重试次数/lease 不足以替代栅栏。

## S2-02 [P1] 澄清授权必须依赖 L2 pending_clarification，但图与接口没有建立它的路径

- 位置：§6.1，643、684；§7.4，921、926；§9.5 G3/G19，1247、1263；§9.6 F3，1295。
- 规范只允许存在 L2 `pending_clarification` 时合并补充回答；但 L1 的 missing/ambiguous 分支直接 clarify→END，clarify 无工具调用；G3 仅返回单个 utterance 与 token；G19 仅允许 F，且没有明确由何者从原请求登记缺失字段、候选和待澄清状态。gateway 表清单也没有该状态的存储位置。
- 可复现：用户键入“给王五转账”；L1 发现缺金额并询问；用户键入“800 元”。第二条单独没有动作和收款人，第一条无金额权限上限为 0。按 §7.4 严格执行，不能合并；如果实现者直接采信 L1 Understanding 创建缺失槽位，又违背 L2 独立确定授权且不信任 L1 的边界。歧义收款人选择卡同样没有 L2 发放动作 ID 的明确生命周期。
- 精确修复建议：让 G3 在登记原指令时由 L2 解析并持久化 pending_clarification，返回 clarification_id、缺失字段、候选和可用 action_id；候选需要读取时，限定 F/G19 以原指令 ID 请求补充，由 L2 自行取可信候选并核验，不采信 L1 自报候选/动作。typed 补充需要显式 parent/clarification ID，L2 合成完整 normalized_scope；明确旧原话是否保留、合并结果 ID、期限与取消行为。同步 F3、L1 ingest/clarify 和 gateway 表。
- 建议验收：“给王五转账”→“800”；同音选择；过期补充；取消后补充；同 sid 新独立指令；攻击 Agent 伪造 clarification_id。合法追问应可完成，历史槽位不能串接成新权限。

## S2-03 [P2] A4 状态级授权证据要求的执行摘要不在锁定 L3 接口中

- 位置：§8.5 `/internal/plans/execute`，1123；§9.1 禁止额外字段，1154；§10.2，1411。
- §10.2 明确要求 L2 把 approval_id、evaluation_id、view_hash、risk、confirmed_at_wall、authorization_epoch 等摘要随执行信封传到 L3 operation_events；但 §8.5 的锁定请求仅有 customer_id、plan_id、plan_hash、steps、expected_versions、clock_version。按禁额外字段生成契约会拒绝摘要；省略摘要又无法按既定 A4 谓词区分“确实提交”与“变化后的确认对应本次提交”。
- 修复建议：将版本化 `authorization_evidence` 明确加入 prepare/execute 共同的不可变信封并登记其字段与哈希覆盖，L3 同事务保存到 execution/operation_events。分别定义绿级、用户审批、scheduled grant、系统订单事件的证据类型；确认事实仍从可信 BFF 测试记录独立关联，不能把 L2 自报视为批准真相。
- 建议验收：契约示例往返校验；同 plan 改摘要拒绝；状态变化重新确认的 evaluation/view_hash 与 L3 记录吻合；丢回包幂等恢复不覆盖初始证据。

## 其他核查结论与边界

- app/assistant JWT、F/A 服务令牌、D 控制台分离与 8201 网络接口绑定的设计已有明确要求；未将尚未实现的安全测试当成漏洞或通过证据。
- app_form 来源由凭证决定，问卷仅 F，hint 只能引用 L2 模板，绿级仍需原话/动作，已覆盖第一轮主要越权入口。
- 适当性、订单系统履约、商户授权失效、到期风险升高、冷静期、计划原子回滚与双端客户版本均有明确规则。本报告没有把明确 P0 实验或运行前待验证项列为缺陷。
- “挂失未补卡”与无卡账户的 current-card 选择需实现测试覆盖，但正文禁止 LOST 账户外转且无卡例外限定储蓄，不在本报告认定为已成立漏洞。

修复后的完整复审尚未执行；本报告列的是初始工作稿的问题，不代表父 agent 已修订后的状态。
