# 第三轮审核：第二轮复核 + 闭合性与内部一致性

- 审核者：Claude 子 agent（round3-closure），2026-10-23。
- 审核对象：`PRD-v2.1-wip.md`（本次读取 1,591 行，全文通读）。约束依据 AGENTS.md §2；第二轮报告 `round2-security.md`、`round2-contracts.md`、`round2-consistency.md`。
- 方式：只读静态审核，只写本报告。没有运行任何代码或测试（仓库尚无代码）。演示算式均已手算，第 6 幕份额用 Python `Decimal` 复算：`floor4(11955/1.2311)=9710.8277`，与 PRD 一致。
- 章节号均指 v2.1 当前版本；引文为原文片段。

---

## 任务 A：第二轮发现逐条复核

| 编号 | 判定 | 证据（v2.1 当前原文） | 残留问题与修改文字 |
|---|---|---|---|
| S2-01 UNKNOWN 无在途证明 | **已修复** | §7.10 第 6–7 项：「先调 L3 `/internal/plans/prepare` 持久化 intent…」「恢复工作器调用 reconcile，以同一 intent 行锁串行于 execute…写不可逆 CANCELLED 墓碑」「若 prepare 本身丢包，reconcile 在同唯一键下创建 CANCELLED 墓碑」；§8.3 `execution_intents`；§8.4「锁预先持久化的 execution_intents 行 → … CANCELLED/过 deadline 拒绝」；§8.5 prepare/reconcile；§7.4「`authorization_claims`…EXECUTING/UNKNOWN 均保留」 | 无阻塞残留。（建议在 §10.2 或 §8.7 测试最低集中补一句「延迟 execute 晚于 reconcile 到达、prepare 丢包、提交后丢回包」三个用例，§8.7 目前只写了「L3 提交后丢回包恢复」） |
| S2-02 澄清缺 L2 pending_clarification 路径 | **部分修复** | §7.4「澄清闭环：G3 登记 typed 时，L2 自己解析…创建 pending_clarification 并返回 clarification_id」；§7.2 `pending_clarifications` 表；G3 返回 `clarification_id?`；G19 `kind:CLARIFICATION`；G21；§6.1「[clarify] ── 读取 L2 clarification…→ verify_output → END」 | ① **路由依据不一致**：§6.1 进入 clarify 的条件仍是 LLM 的「missing 或 ambiguous_payees 非空」，而 L2 是否建了 clarification 由 G3 决定。两者不一致时（LLM 说缺参但 G21 返回 null，或反之）行为未定义。② 合槽后 F3 传给 L1 的是哪个 utterance_id 没写。修改：§6.1 把分支改为「`ingest` 通过 G21 读取当前 clarification；**存在 OPEN clarification 且其 parent 属于本轮原话 → clarify**；不存在时，understand 的 missing/ambiguous 只能走 respond 提示用户补充（不展示候选卡），不得进入 plan」。§7.4 补一句「G3 对补答返回的 `utterance` 即合槽后的新 UtteranceRef，F3 用它的 id 调 L1；旧 parent 不再单独传入」 |
| S2-03 授权证据不在 L3 契约 | **部分修复** | §8.5 execute 请求已含 `authorization_evidence`，「evidence 见 §10.2，绿级 approval/view/confirmed 字段为 null」；§8.3 `plan_executions.authorization_evidence` | ① §10.2 字段表写 `view_hash` 为非可选，§8.5 说绿级 view 为 null，两处冲突。② 未说明 evidence 是否进入幂等比较、prepare 是否带它。③ 事件路径（scheduled grant、商户代扣、基金履约）`/internal/events/{id}/execute` 没有证据字段。修改 §10.2 末段为：「`authorization_evidence` 为判别联合：`{type:INTERACTIVE, approval_id?, evaluation_id, plan_hash, view_hash?, risk, confirmed_at_wall?, authorization_epoch}`（绿级 approval_id/view_hash/confirmed_at_wall 为 null）；`{type:SCHEDULED_GRANT, grant_id, scope_hash, source_plan_id, approved_risk}`；`{type:SYSTEM_ORDER, order_id}`；`{type:MERCHANT_MANDATE, instruction_id, billing_id}`。它与 plan_hash 一起写入 prepare，execute 时必须与 prepare 记录逐字节相同，否则 409 IDEMPOTENCY_CONFLICT；`/internal/events/{id}/execute` 增加同名字段」 |
| C01 agent 自修不能补读 | **未修复** | §6.1 仍为「REPLAN 且全部 resolver=agent ──▶ [plan]」，plan 节点工具只有 `submit_action_plan`；§7.9「agent 自修限两次，仅补读取…」 | 修改 §6.1 该分支为「REPLAN 且全部 resolver=agent ──▶ [gather]（携带 findings；gather 只允许调用 findings.evidence_refs 指明的读工具，至多 3 次调用）→ [plan]；与 plan 共用 `replan_count≤2`」。§9.8 对 `ENTITY_UNSEEN` / `PAYEE_NOT_FOUND` 增加 `evidence_refs` 必须给出建议读工具名（如 `list_payees`）的约定 |
| C02 澄清闭环 | **部分修复** | 同 S2-02；clarify 已改为经 verify_output | 残留同 S2-02 ①②。另：候选卡点击后要经 G19 换 action_id，再经 G3 `kind=action` 登记，G19 请求中 `action_kind` 对 CLARIFICATION 的取值未定义。修改 G19：「kind=CLARIFICATION 时 `action_kind` 必须为 null，`fields` 只能是 `{field, entity_id}` 且 entity_id ∈ 当前 clarification.candidates」 |
| C03 add_payee no-op 与派生 ID | **未修复** | §6.2「L2 在 ① 之后把它解析为派生 ID」；§7.2「L2 生成并先保存 plan_id，再解析步骤引用」；W2「同目标已存在返回原 ID/no_op=true」 | 修改 §6.2 该条为：「`{ref:"s1"}` 在 ⑤ 快照后解析：若 s1 为 add_payee 且快照中已存在同 `target_fingerprint` 的收款人，引用解析为该已有 payee_id，s1 标 no_op；否则解析为 `derive(plan_id,"s1",kind)`。解析表写入 `plans.resolved_steps` 并参与 steps_hash；L3 执行时按同一规则重算，不一致返回 STATE_CHANGED」 |
| C04 D6 没有合法 execute 路径 | **部分修复** | §9.7 D6 已增加「POST `/demo/attack/plans/{id}/execute`」「确认仍由真实 F 的 G6 完成」 | ① D6 的 draft 需要 `utterance_ids`，但 runner 没有 F 服务令牌，无法调用 G3 登记原话，D6 也不登记原话。② G6 要 F+app JWT，runner 需要客户 PIN 经 BFF 登录，但 §11.1 `DEMO_LOGIN_PINS` 只给 gateway。修改 D6：「提交时 L2 按 case 文件中冻结的 `utterances[]` 用同一 G3 解析器登记原话（kind=typed，sid 由 D6 新建并绑定 case），返回 `{session_id, utterance_ids, decision}`」；§11.1 `DEMO_LOGIN_PINS` 行改为「gateway（校验）、可信评测 runner（经 BFF F1 登录以完成 G6）；不注入 agent」 |
| C05 A4 授权证据 | **部分修复** | 同 S2-03 | 同 S2-03 |
| C06 曲线遗漏冻结 | **已修复** | §8.3 末段「按账户 sequence 取每日最后一条**所有阶段**记录（含 PENDING/RELEASED）的 balance_after/available_after」 | 字段残留见新问题 N11（`phase` 列未列出） |
| C07 返回模型漏字段 | **未修复** | R3 data 模型无 `totals_scope`，正文「且标 `totals_scope=filtered_all`」；W2 result `{payee_id,target_fingerprint}`，正文「no_op=true」；R10「返回计算口径」无字段 | R3 data 改为 `{items,next_cursor,income_cents,expense_cents,totals_scope:"filtered_all"}`；W2 result 改为 `{payee_id,target_fingerprint,no_op:bool}`；R10 data 增加 `normalization_basis:{weekly_to_annual_factor:52,monthly_to_annual_factor:12,annual_to_monthly:"floor_div_12"}` |
| C08 周订阅折算无算法 | **未修复** | §5.4「间隔 28–31 天或 7 天」；R10「月费按周期统一折算」；§7.5 `cancelled_monthly_fees` | 在 §5.4 末尾增加：「折算：MONTHLY 年费 = 金额×12、月费 = 金额；WEEKLY 年费 = 金额×52、月费 = floor(年费/12)。合计先逐项折算后求和。`cancelled_monthly_fees` 模板只接受 period=MONTHLY 的订阅；含 WEEKLY 时返回 REPLAN `INTENT_SCOPE_EXCEEDED`（user）」 |
| 一致性 C1 UNKNOWN 协议 | **已修复** | 同 S2-01 | 无 |
| 一致性 C2 grant 缺失与无效未区分 | **未修复** | §7.11 仍只有「创建期间崩溃时根据 L3 outbox 补建，不因缺 grant 自动放行」「其余拒绝记 BLOCKED」 | 在 §7.11 第一条后增加：「回调时 grant 缺失：先按 `standing_instructions.source_plan_id` 查 L3 已提交执行与 outbox，幂等补建 grant 后继续；若 L3/outbox 暂不可读，返回 `DEPENDENCY_UNAVAILABLE`，scheduler 把 job 保持 RETRYING，不写 instruction_runs、不推进周期。只有 grant 状态为 REVOKED 或指纹不符时才 BLOCKED（SCHEDULED_BYPASS）。gateway 启动顺序：先消费 outbox 补建 grant，再接受 S 回调」 |
| 一致性 C3 非即期冻结时点 | **已修复** | §8.2「非即期在下单事务 pending 全额冻结（周末/15:00 后也立即占款，T 仅决定确认日期）」；§5.5「下单即冻结」 | 无 |
| 一致性 C4 多笔赎回成本分母 | **已修复** | §8.2 赎回：「`reserved_cost_cents = round_half_up((holding.cost_cents − holding.reserved_cost_cents) * redeem_units / (holding.units − holding.reserved_units))`；卖出全部可用份额时取全部可用成本」；R16 `available_cost_cents` | 无 |
| 一致性 C5 A1 fixture | **已修复** | §10.2 A1「先通过本人互转从储蓄入活期 100,000 分，使活期 700,000；…三笔各 200,000 分…剩余 100,000 > 7 天预留 4,500」。复算：SPLIT_TRANSFER 第三笔 3 笔、600,000 > 500,000 命中；CUMULATIVE 600,000 ≤ 1,000,000；单笔 200,000 ≤ 500,000；卡单笔 5,000 元、日 20,000 元均不触发 | 无 |
| 一致性 C6 授权证据 | **部分修复** | 同 S2-03 | 同 S2-03 |
| 一致性 C7 available 曲线 | **已修复** | 同 C06 | 无 |

小计：已修复 7（S2-01、C06、一致性 C1/C3/C4/C5/C7），部分修复 6（S2-02、S2-03、C02、C04、C05、一致性 C6），未修复 5（C01、C03、C07、C08、一致性 C2）。

---

## 任务 B：新问题（闭合性与内部一致性）

本轮没有发现 P0。P1 共 9 条，P2 共 11 条。

### P1

**N1 · L1 遇到 STATE_CHANGED 时重新 evaluate，与 L2 已自动复演冲突**
- 位置：§6.1「409 STATE_CHANGED → [evaluate]（重新生成审批，最多 1 次）」；§7.10 第 5 项「返回 409 STATE_CHANGED 及最新 Decision。若仍需批准，生成新审批」；D21。
- 问题：L2 已经复演，并在 409 里返回新 Decision 和新审批。L1 再走 evaluate 会生成新的 request_id 和 plan_id，把 L2 刚生成的审批作废（SUPERSEDED），还会多出一份计划。次数也不一致：L1 写 1 次，L2 写 2 次。
- 修改：§6.1 改为「409 STATE_CHANGED → 读取响应体中的最新 Decision，按 evaluate 之后的同一组分支路由（REQUIRE_APPROVAL → await_approval，使用新的 approval_id）；不重新调用 /plans/evaluate；本 run 最多 1 次，超过转 respond」。§7.10 第 4 项写明「L2 内部自动复演最多 2 次；超过后返回 409 STATE_CHANGED 并附最新 Decision」。

**N2 · L1 没有处理 evaluate/execute 的 HTTP 错误和 UNKNOWN**
- 位置：§6.1 execute 只列出 APPROVAL_REJECTED / APPROVAL_EXPIRED / OTP_LOCKED / STATE_CHANGED；G8 可返回「202 `{plan_id,status:UNKNOWN}`」；§9.8 E409 有 UTTERANCE_EXPIRED、AUTHORIZATION_CONSUMED、APPROVAL_SUPERSEDED、APPROVAL_USED、EXECUTION_CANCELLED、EXECUTION_DEADLINE_EXPIRED，另有 E422 CONTRACT_INVALID、E503 DEPENDENCY_UNAVAILABLE；§9.5「UNKNOWN…轮询 G5 恢复」。
- 问题：这些返回值都没有图上的去向，也没写由谁轮询 G5。
- 修改：在 §6.1 节点规格后增加一张表：「evaluate：422 CONTRACT_INVALID → plan（计入 replan_count）；409 UTTERANCE_EXPIRED/AUTHORIZATION_* → respond（提示重新发起）；503 → respond（模板「服务暂不可用，未执行任何操作」）。execute：202 UNKNOWN → respond 输出「确认中」卡片后 END，由 App 通过 F8/G5 轮询，L1 不轮询；其余 E409 → respond 按 code 映射固定文案；503 → 同 UNKNOWN 处理」。

**N3 · 「语义相同则保留原确认」与审批不可变绑定 evaluation_id/versions 矛盾**
- 位置：§7.10 第 1 项「approval 绑定 …evaluation_id、versions、clock_version…得 binding_mac」；第 5 项「语义完全相同仅内部版本变化时可建立新 evaluation，保留原确认与到期时间」。
- 问题：新 evaluation 的 evaluation_id 和 versions 与审批的 binding_mac 不一致。是改审批行（违背不可变），还是执行时放宽 MAC 校验，没有定义。authorization_evidence 该填哪个 evaluation_id 也不确定。
- 修改：第 5 项改为「语义完全相同时：新建 evaluation（revision+1），审批行不改；执行复核改为校验 `approval.binding_mac` 对应原 evaluation，且 `new_evaluation.view_hash == approval.view_hash`，并在 evaluations 记录 `equivalent_to=原 evaluation_id`；authorization_evidence 的 evaluation_id 填新 evaluation，另带 `approved_evaluation_id`」。

**N4 · 原话未指定源账户时，意图范围怎么比对没有定义**
- 位置：§7.6「转账对 `(from_account_id, target_fingerprint, amount_cents)` 做**多重集合**匹配」；§3.4 原话「给王五转 3000，再给赵六转 4000」没有源账户；X-2「每月 1 号自动转到我的储蓄账户」。
- 问题：normalized_scope 里的 from_account_id 从哪里来，L1 能否自选源账户，都要开发者自己决定。
- 修改：§7.6 增加「原话未指定源账户时，scope.from_account_id = 客户唯一的 CHECKING 账户；客户有多个 CHECKING 时创建 clarification（required_fields=[from_account_id]）。用户指定源账户时按指定值。计划中 from_account_id 与 scope 不同 → INTENT_SCOPE_EXCEEDED（user）」。

**N5 · 商户扣费没有时刻，第 6 幕「预留 45」不能确定**
- 位置：§2.2「李四有爱看视频（每月 20 日 30.00）与乐听音乐（每月 22 日 15.00）」；R11 区间「`(sim_now,sim_now+days]`」；reset 时刻 10-15 09:00；§12 第 6 幕「保留未来 7 天 45」。
- 问题：7 天窗口的上界是 10-22 09:00（含）。22 日扣费如果晚于 09:00，就不在窗口内，预留变成 30，可申购上限变成 11,970，第 6 幕的数字不再成立。merchant_billing 和种子表都没给扣费时刻。
- 修改：§2.2 代扣表头下方加「所有商户扣费与种子定时转账的执行时刻为当日 06:00（Asia/Shanghai）」；§8.3 `merchant_billing` 增加 `time_of_day` 字段。（据此复算：张三 18 日 06:00、20 日 06:00 在窗口内，合计 45；李四 20 日 06:00、22 日 06:00 在窗口内，合计 45；第 2 幕 600,000−300,000−4,500=295,500 和第 6 幕 1,200,000−4,500=1,195,500 成立。）

**N6 · 取消定时转账的基础等级：§5.1 写黄，§9.4 写绿**
- 位置：§5.1 T-3 行「`schedule_transfer`、`cancel_scheduled`(write)…｜黄」；W4「绿；instruction_id」；§7.10 第 8 项「自动执行冻结、取消定时或 AA 创建」。
- 修改：§5.1 T-3 基础等级改为「schedule 黄 / cancel 绿（保护性）」。

**N7 · 回放规范化正则漏掉了多数易变 ID 前缀，replay 会无法命中**
- 位置：§6.6「`(pln|utt|apv|ses|req|msg)_[0-9A-Z]{26}`」；§9.1 运行时前缀还有 evl、src、hnt、act、ord、tx、trf、evt、ntf、spl 等；`msg` 不在 §9.1 前缀表里；clarification_id、assessment_id、advance_job_id 都没有前缀。
- 问题：plan 节点的输入包含「reads 摘要 + 上次 Decision 摘要」，里面会有 src_/evl_ 等 ID。make demo 不是 EVAL_MODE，ULID 不固定，§6.6 又规定「replay 未命中时报错…不回退」，演示会直接失败。
- 修改：§6.6 正则改为「`\b[a-z]{2,4}_[0-9A-HJKMNP-TV-Z]{26}\b` 与 UUID」；§9.1 前缀表增加 `msg`、`clr`（clarification）、`asm`（assessment）、`adv`（clock advance job）、`job`（event job）；并写明「DEMO_MODE 与 EVAL_MODE 一样使用 seeded ULID」。

**N8 · L2 的 S 回调接口不在「唯一登记表」中**
- 位置：§7.1「接口以 §9.5–§9.7 为唯一登记表；未登记的路径、工具或方法默认拒绝」；§7.11「S 回调 L2 `/internal/scheduled/authorize {event_id}`」；§9.5–§9.7 都没有这个接口。
- 问题：按 §7.1 的字面规定，这个接口会被默认拒绝，而且它的返回形状没有定义。
- 修改：在 §9.5 后新增「§9.5a L2 内部接口（仅 :8201，S 身份）：S1 POST `/internal/scheduled/authorize` `{event_id}` → `{event_id,status:EXECUTED/BLOCKED/FAILED_INSUFFICIENT/RETRYING,code?,instruction_run_id?}`；对同一 event_id 幂等」，并把 §7.1 改为「§9.5–§9.7 与 §9.5a 为唯一登记表」。

**N9 · strict 可选字段的哨兵回退方案覆盖不全，P0 实验也没有通过标准**
- 位置：§6.6「可选字段：【P0 实验】先试 `anyOf:[T,{"type":"null"}]`…不行就…哨兵值（字符串 `""`、整数 `-1`、数组 `[]`）」；W7 `online:bool?` 等布尔可空字段；W1 `payee_id:string|EntityRef|null`；R4 `mom_change_bp:int?`。
- 问题：① 布尔可空字段和联合类型可空字段没有哨兵；W7 中 null 表示「不改」，不能用 false 代替。② `""` 与 memo 的合法空串冲突。③ 「不行」没有判定标准，三个 P0 实验都缺样本量和通过线。
- 修改：哨兵一句改为「`bool?` → 字符串 enum `KEEP/ON/OFF`；`string|EntityRef|null` → 增加判别字段 `{kind:"id"|"ref"|"none"}`；`string?` 的哨兵为 `"__NULL__"`；`int?` 只出现在非负字段时用 -1，有符号字段不进入 LLM Schema」。在 §6.6 开头补充通用判定规则：「每个 P0 实验用 30 次固定请求，在 strict 端点上 0 次 400、Schema 校验通过率 100% 才算通过，否则走回退；结论写 ADR-002」。

### P2

**N10 · X-3 派生模板用 R11 总额，但 L2 预留还包括定时转账**
- 位置：R11「仅有效授权」，只返回代扣（billing_id/instruction_id）；§7.7「逐日、逐账户投影已授权支出」，包含定时转账；§7.5 `available_less_7d_charges`。
- 问题：7 天内有定时转账时，按模板算出的金额会触发 SCHEDULED_SHORTFALL。
- 修改：R11 增加 `scheduled_transfers_total_cents` 和 `reserve_total_cents`（等于 §7.7 算法结果）；模板改为「available − reserve_total_cents」。

**N11 · `transactions` 缺少 phase 列和 status 枚举**
- 位置：§8.3 `transactions`「UNIQUE(transfer_id,account_id,phase)；phase 为关联账本阶段」，但字段列表里没有 phase；末段用到 status PENDING/SETTLED/RELEASED，没有全集。
- 修改：字段列表加 `phase`，并写「status ∈ {POSTED, PENDING, SETTLED, RELEASED}」。

**N12 · 字段名与表结构不一致、对他人余额的暴露口径不清**
- 位置：AccountView `linked_card_id?` 与 accounts `current_card_id`；CardView `replacement_id?` 与 cards `replaces_id`（方向相反，未说明怎么推导）；W1 result `to_available_after_cents?`。
- 修改：AccountView 字段统一改名为 `current_card_id?`；CardView 注明「replacement_id 由 cards.replaces_id 反查」；W1 注明「`to_available_after_cents` 只在 to_account_id（本人）时填写，payee 目标一律 null」。

**N13 · FROZEN 卡放宽限额/渠道没有对应规则码**
- 位置：§8.2「FROZEN 卡可调低限额/关闭渠道，不可放开」；§7.7 CARD_STATE_CONFLICT 的触发条件只写「非 ACTIVE 卡刷卡/外转，或 LOST 卡修改/解冻」。
- 修改：触发条件改为「…，或 FROZEN 卡 LOOSEN 限额/渠道，或 LOST 卡任何修改/解冻」。

**N14 · G18「恢复已审批计划」的理由不成立**
- 位置：§7.3「超过 10 分钟后由已登录浏览器通过 G18 为同 sid 换新令牌，以恢复已审批计划」；§7.10 第 6 项「deadline 为原话、审批和会话有效期的最小值」（原话 10 分钟、审批 5 分钟）。
- 问题：超过 10 分钟后，原话已过期，计划不能再执行。
- 修改：改为「G18 只用于原话过期后让 L1 读取 state、展示历史与终态（F5），不能延长任何执行期限」。

**N15 · 计划/审批状态没有统一枚举，`scope_id` 来源未定义**
- 位置：§7.4 列出 EXECUTED/FAILED/DENIED/REJECTED/EXPIRED、REPLAN、EXECUTING/UNKNOWN；§7.10 有 SUPERSEDED/CONFIRMED；`plans.status`、`approvals.status` 没有全集；`authorization_claims(scope_id…)` 没说 scope_id 取什么。
- 修改：§7.2 增加「plans.status ∈ {EVALUATED, REPLAN, DENIED, AWAITING_APPROVAL, APPROVED, EXECUTING, UNKNOWN, EXECUTED, FAILED, REJECTED, EXPIRED}；approvals.status ∈ {PENDING, CONFIRMED, REJECTED, EXPIRED, SUPERSEDED, USED}；scope_id = 授权来源 ID（typed/合槽后的 utterance_id、hint_id 或 form_action_id）」。

**N16 · grant 的「批准风险等级」没有存储位置，种子 grant 的等级未定义**
- 位置：§7.11「grant…精确绑定…批准的风险等级」「风险不高于创建批准等级」；§7.2 `scheduled_grants` 字段没有 risk；§2.2 `si_zs_mom`「grant=seed」。
- 修改：`scheduled_grants` 增加 `approved_risk`；§2.2 写「si_zs_mom 的种子 grant approved_risk=YELLOW；商户代扣种子 grant approved_risk=YELLOW」。

**N17 · 执行锁与审计锁可能重入导致自锁（第二轮非阻塞建议，仍未处理）**
- 位置：§7.8「按所有涉及客户 ID 排序取得数据库会话级 advisory lock，覆盖…结果归档」；§7.12「客户 advisory lock 保证追加顺序」。
- 修改：§7.12 改为「审计追加使用独立的锁命名空间 `pg_advisory_xact_lock(hashtext('audit'), customer_key)`，并且在执行锁之后获取；或者在持有执行锁的同一连接内写审计」。

**N18 · 文档头与修订记录过时**
- 位置：第 3 行「状态：修订中，待第二轮审核」；第 4 行「v1.2 存档于 docs/archive/PRD-v1.2.md」，但 `docs/archive` 目录尚不存在；§0.5 只记到 D29，第二轮修订（intent/reconcile、澄清闭环、authorization_evidence、赎回成本、A1 fixture）没有决议编号；§3.6 列 ADR-001…005，§13 只定义 001/002/003/005，ADR-004 无归属，ADR-003 又说「不再重复投入 POC」。
- 修改：状态改为「第三轮审核后修订中」；存档链接注明「定稿时创建」；§0.5 追加 D30–D3x 记录第二轮修订；§3.6 写明 ADR 清单（001 L3 自研、002 LLM、003 策略引擎不用 Invariant（直接引用 V-INV，不再做 POC）、004 删除或指定用途、005 前端）。

**N19 · 健身闲置判定依赖「7–8 月」到店记录，日期范围不准**
- 位置：§2.2「7–8 月有 6 次到店消费，近 60 天没有」；§5.4「近 60 天…没有，且 60–120 天前有」。
- 问题：reset 时刻 10-15 往前 60 天是 08-16。8 月下旬的到店记录落在近 60 天内，会让健身不再判为闲置。
- 修改：改为「2026-07-17 至 2026-08-15 之间有 6 次到店消费」。

**N20 · 申购用「确认净值」，赎回用「T 日净值」，口径不统一，预估份额用哪个日期也没写**
- 位置：§8.2 申购「确认日用确认净值按同公式得份额」（第 6 幕用 10-16 净值 1.2311）；赎回「按 T 日净值固定到账金额」；W13 `estimated_units`。
- 修改：申购一句改为「确认日用**确认日当日** nav(confirm_date) 计算份额（沙箱简化）；W13 的 estimated_units 用 nav(confirm_date) 预估并标注净值日期」；赎回保持 nav(T)。两处都注明是沙箱简化，不是真实基金规则。

---

## 演示算式复核记录

- 第 2 幕：600,000 − 300,000 = 300,000 < 400,000 → INSUFFICIENT_FUNDS；提示 300,000 − 4,500 = 295,500 ✓。B1 各步从初始快照算：600,000 − 400,000 = 200,000 ≥ 4,500 ✓。执行后张三 300,000、王五 200,000 + 300,000 = 500,000 ✓。前提是 N5 修复。
- 第 4 幕：卡单笔 5,000→8,000，不超过日限 20,000，也不超过最高 50,000 ✓。40 分钟 > 30 分钟，不触发新收款人规则 ✓。100,000 分 < 单笔红线 ✓。
- 第 5 幕：识别健身 199 元；视频 10-18、音乐 10-20 在推进到 10-25 之前照常扣费 ✓。
- 第 6 幕：1,200,000 − 4,500 = 1,195,500 ✓；10-15 周四 09:00 < 15:00，T = 10-15，确认日 10-16 09:00，nav = 1.2310 + 0.0001 = 1.2311 ✓；份额 9710.8277 ✓（Python Decimal 复算）。前提是 N5 修复。
- 第 7 幕：储蓄 20,000 − 2,000 = 18,000，活期 6,000 + 2,000 = 8,000 ✓。
- A1：见任务 A 一致性 C5 行 ✓。
