# 第三轮修订回归审核（D30–D46）

- 审核对象：`PRD-v2.1-wip.md`（1,756 行），对照修订前备份 `PRD-v2.1-before-round3-fix.md`（1,591 行）的 diff。
- 范围：只报这次修订**新引入**的问题；修订前已经存在的问题不报。
- 结论：P0 3 条、P1 7 条、P2 8 条，共 18 条。
- 方法：逐段阅读新稿，用 diff 和 grep 确认每条都是本轮新增或改动造成的（例如 `echoes_untrusted`、`utts`、`final_view_hash`、`SKIPPED_REAPPROVAL`、`D6a`、`hashtext('audit'…)` 在旧稿中出现 0 次；G18 的语义在本轮从「恢复已审批计划」改成了「不能执行」）。演示算式已用 Python Decimal 重算。

---

## P0

### P0-1 复述检测会把几乎所有点名转账升红；L2 也拿不到「助手回复」
- **位置**：§7.4「复述检测」：「若作用域中任一账号、收款人名称或金额，与同 sid `read_records` 中 untrusted 路径的值，或与本会话最近 3 条助手回复中的值规范化后相同，则把该原话标 `echoes_untrusted=true`……加 `INJECTION_SUSPECTED`（升红）」。
- **问题**：
  1. §9.3 把 R2 的 `name/alias` 列为不可信字段，§9.1 又规定「来自实体名称等文本即使正常也标不可信」。§7.4 澄清闭环要求 G3 自己读取收款人候选并登记 `read_records`。结果是：只要会话中读过收款人列表，或 G3 先登记了读取，「给王五转 800」这类话中的「王五」就必然命中，于是 T-1 正常用例、第 2 幕和 A1 fixture 的第一笔都会被升红。A1 会因为这条规则而不是 SPLIT_TRANSFER 升红，step-up 的归因就失真了。§5.0 写明已有收款人的转账是黄级，§1.3 G5 和 §15 要求 FPR≤5%，两者都与此冲突。
  2. 金额与助手回复比对也会误伤：助手说了「最多还能转 2,955.00 元」，用户自己打字「给赵六转 2955」就会被升红并提示「出现在助手回复中」。这条路径恰恰是第 2 幕的自然替代路径。
  3. 助手回复由 L1 生成、经 BFF 下发，§9.5 / §9.6 里没有任何接口把回复写进 L2，所以 L2 无法实现「最近 3 条助手回复」。§6.7 1a 已经对 ≥8 位数字做了掩码，回复中本来就不会出现完整账号。
- **建议**：把 §7.4「复述检测」整段替换为：
  > **复述检测**：G3 登记 typed 时，只比对**账号类值**（规范化后 ≥ 8 位数字）。若某账号与同 sid `read_records` 中 untrusted **自由文本路径**（`memo`、`counterparty`、`merchant_name`、`risk_disclosure`、`target_display`）里的数字串相同，且它不是客户已有收款人的 `target_fingerprint`，则把该原话标 `echoes_untrusted=true`。由它授权的计划加 `INJECTION_SUSPECTED`（升红）。若同一作用域同时含 PAYEE_ADD 与向该新目标的资金流出，DENY `TAINTED_SENSITIVE_ARG`，并对该指纹设 24h 冷静期。收款人名称和金额**不参与**复述检测：名称由所有权与指纹约束，金额由 §7.5 的来源规则约束。L2 不读取助手回复。

  同时把 §7.5 末段「app_form 也做值级污点检查」中的「账号、收款人名称」改为「账号」，判定口径与上面相同。

### P0-2 「授权证据逐字节相同」与同一 plan_id 下的复演、重新审批互相矛盾
- **位置**：
  - §8.5 prepare：「同键但 plan_hash / steps_digest / evidence 任一不同 → 409 `IDEMPOTENCY_CONFLICT`」；execute：「evidence 必须与 prepare 记录**逐字节相同**」。
  - §7.10.4：「L3 返回 STATE_CHANGED 时 L2 内部自动复演，最多 2 次」。
  - §7.10.5：「相同：新建 evaluation……`authorization_evidence` 的 `evaluation_id` 填新 evaluation」；「不同：……生成新审批（新 approval_id 在 Decision 中）……plan_id 不变」。
  - §10.2 evidence 中含 `evaluation_id`、`approval_id`、`final_view_hash`。
- **问题**：每次复演都会产生新的 `evaluation_id`，重新审批还会换 `approval_id`，而 plan_id 不变、intent 键 `(customer_id, plan_id)` 也不变。按现行规则，第二次 prepare 或 execute 必然得到 `IDEMPOTENCY_CONFLICT`。所以 D21 的自动复演和 D35 的「状态变化后重新确认」在 L3 层面都走不通，第 4 幕和 A4 的复核路径无法执行。改走 reconcile 也不行：reconcile 会写 CANCELLED 墓碑，该 plan 永不可再执行。
- **建议**：在 §8.3 `execution_intents` 增加 `attempt int`、`evidence_history jsonb`；把 §8.5 prepare 一行改为：
  > POST `/internal/plans/prepare` `{customer_id,plan_id,attempt,plan_hash,steps,deadline_wall,authorization_evidence}`：同 `(customer_id,plan_id,attempt)` 幂等。`attempt` 大于已存值且 intent 仍为 PREPARED 时，在 intent 行锁内替换 evidence 并把旧值追加进 `evidence_history`。plan_hash、steps_digest 不同，或 attempt 小于当前值，返回 409 `IDEMPOTENCY_CONFLICT`。COMMITTED / CANCELLED 不可再 prepare。deadline 只能缩短。

  execute 一行改为「plan_hash、steps_digest、attempt 与 evidence 必须与**当前 attempt** 的 prepare 记录逐字节相同」。§7.10.4 补一句：「每次内部复演或重新审批后，L2 以 attempt+1 重新 prepare 再 execute。」

### P0-3 「这笔可疑交易对应的卡」的求值时机与条件会让 X-4 和第 7 幕走不通
- **位置**：§5.6 谓词表：「本会话最近一次 `detect_anomalies` 结果中唯一一条异常的 `card_id`｜必须等于它；不唯一时建 clarification」；求值「在 G3 登记原话时和 ④ 意图范围中各做一次」。第 12 章第 7 幕：「`scene_protection`：张三基线；冻结可疑卡……整份黄确认」。
- **问题**：
  1. G3 登记发生在 L1 gather 之前。第 7 幕 fixture 清空了会话，此时会话里没有任何 `detect_anomalies` 结果，G3 求值必然失败。
  2. 张三基线 9 月有 3 条异常（§2.2），不存在「唯一一条」，于是一定进入 clarification，而且没有候选可供选择。3 条异常其实都在唯一的卡 `crd_zs_1` 上。
  3. 第 7 幕原话「冻结可疑卡」与谓词词条「这笔/那笔可疑交易对应的卡」不一致。
- **建议**：谓词表第三行改为：
  > 「这笔 / 那笔可疑交易对应的卡」「可疑卡」｜L2 在快照上自行运行 `detect_anomalies(sim_now−30d, sim_now)`，不依赖 L1 是否读取过；取全部异常的 `card_id` 集合｜集合只含 1 张卡时必须等于它；含多张卡时由 L2 建 clarification，候选即这些卡；为空时返回 `INTENT_UNCLEAR`。

  第 12 章第 7 幕的预期补一句：「L2 谓词求值得到 `crd_zs_1`（3 条异常同卡）」。

---

## P1

### P1-1 F4 恢复审批仍「必要时 G18 换令牌」，但 G18 令牌已不能执行
- **位置**：§9.6 F4：「BFF 查 G9 绑定 sid/plan，必要时 G18 换助手令牌再传 Command」。§9.5 G18 与 §7.3：「令牌 `utts=[]`……不能提交或执行计划」。
- **问题**：这次修订把 G18 从「恢复已审批计划」改成了「不能执行」，F4 却没有跟着改。按 F4 恢复后，L1 调 G8 必然失败，而且这个失败码不在 §6.1.1 的路由表里。另外，PRD 也没有规定 BFF 要保存 G3 签发的令牌。
- **建议**：F4 改为：
  > `{approval_id}`；BFF 查 G9 绑定 sid/plan，用该计划所属 G3 签发、仍在有效期内的助手令牌（BFF 服务端按 sid 保存，不下发浏览器）传 Command。令牌已过期时不恢复 L1，改为提示「授权已过期，请重新发起」；G18 只用于 F5 读取历史。

  §7.3 补：「BFF 按 sid 在服务端内存或会话存储中保存最近一次 G3 令牌，直到过期。」

### P1-2 G5 / DoD 仍拿 B2 与 B0 比较端到端 TSR，但 B0 已不跑端到端
- **位置**：§15：「B2 相对 B0 的正常 TSR 下降≤5 个百分点」；§10.3、§6.6（D42）：「端到端 replay 只跑 B1 / B2……B0 与消融只跑网关层」；§10.3 TSR 定义含「回复关键数字正确」（只有端到端才有回复）。§13 削减第 2 级：「B0 端到端（只保留网关层）」「§15『完整 replay』改为……」。
- **问题**：B0 端到端已经被 D42 取消，削减第 2 级还把它列为可削减项，而 §15 中「完整 replay」这几个字已不存在。按现行定义，TSR 比较无法计算。
- **建议**：§15 该条改为：
  > B2 UTR=0（分母非零；零分母记 N/A）；**网关层** B2 相对 B0 的正常用例 L3 目标状态达成率下降 ≤ 5 个百分点；**端到端** B2 相对 B1 的 TSR 下降 ≤ 5 个百分点；FPR ≤ 5%。

  §1.3 G5 的衡量方式同步写成这两条。§13 第 2 级改为「3 个消融组」，替代条款改为「§15 网关层报告去掉消融」。

### P1-3 执行路径写审计不拿审计锁，哈希链会分叉
- **位置**：§7.12：「执行路径中，审计在持有执行锁的同一 `lock_conn` 上写入（§7.8），不另取锁；执行路径之外的审计追加用独立命名空间 `pg_advisory_xact_lock(hashtext('audit'), hashtext(customer_id))`」。
- **问题**：同一客户的 evaluate、G3、通知等非执行路径审计只拿 audit 锁，执行路径只拿客户锁，两者互不排斥。两边可能读到同一个 `prev_hash` 并各自追加，造成每客户哈希链分叉。
- **建议**：替换为：
  > 所有审计追加（含执行路径）都在各自的小事务内先取 `pg_advisory_xact_lock(hashtext('audit'), hashtext(customer_id))`，再读 `prev_hash` 并插入。执行路径在 `lock_conn` 上这样做：两个锁的键空间不同，不构成重入。另加 `UNIQUE(customer_id, prev_hash)` 兜底。

### P1-4 授权证据联合缺少演示 / 外部事件类型
- **位置**：§10.2 `authorization_evidence` 只有 INTERACTIVE / SCHEDULED_GRANT / SYSTEM_ORDER / MERCHANT_MANDATE 四种；§8.5 events execute 要求带 `authorization_evidence`；§9.7 D4 与 §8.5 `/internal/demo/events` 产生的 EXTERNAL_CREDIT / EXTERNAL_DEBIT / SPLIT_PAYMENT job「仍由 L2 执行」。
- **问题**：D4 事件和 AA 付款没有可用的证据类型，按 D32 的判别联合无法通过契约校验。第 3 幕、A4（注入外部扣款）、T-4 都依赖这类事件。
- **建议**：在 §10.2 联合中追加：
  > `{type:DEMO_EVENT, event_id, console_session_id, kind}`：仅 DEMO_MODE / EVAL_MODE 可用，由 D4 注册的 job 使用；不计入 UTR 分子与分母，审计标 DEMO。

### P1-5 「稳健型」谓词缺种子标签，结果多于 1 个时 X-3 预期不闭合
- **位置**：§5.6：「『稳健型』产品｜`risk_label ∈ {稳健, 平衡}` 且产品等级 ≤ 客户测评等级｜……结果多于 1 个时由 L2 建 clarification」；§2.2 理财产品表没有 `risk_label` 列；X-3 预期只写「红 + 风险揭示」。
- **问题**：旧稿在 §5.6 写明「稳健=R2、平衡=R3」，本轮把这个映射删了，种子表却没有补上，求值无法确定。按旧映射，R3 客户（李四）可选的产品是稳盈短债、稳健增利、均衡混合三支，必然进入 clarification，但 X-3 的预期里没有这一步。张三没有测评，求值结果为空，此时的行为也没有定义。
- **建议**：§2.2 产品表加 `risk_label` 列：安心货币「保守」、稳盈短债「稳健」、稳健增利「稳健」、均衡混合「平衡」、成长股票「进取」、科技先锋「激进」。X-3 预期改为：
  > （李四，R3）L2 求值得到 {稳盈短债, 稳健增利, 均衡混合} → L2 clarification 让用户选择一支 → 红 + 风险揭示。无有效测评时 DENY `ASSESSMENT_MISSING`。

### P1-6 G3 解析收款人时没有规定精确匹配优先，第 2 幕会被「王武」拦下
- **位置**：§7.4 澄清闭环（D31）：「G3 登记 typed 时，L2 自己解析动作、槽位并按需读 L3 账户/收款人/卡候选……缺槽/歧义时创建 pending_clarification」；§2.2 张三同时有「王五」和同音的「王武」；第 2 幕原话「王五 3000，赵六 4000」直接进入预演。
- **问题**：消歧现在由 L2 确定性完成，但没有规定匹配次序。如果同音也算歧义，第 2 幕、A1 以及所有「王五」用例都会先被追问，与演示预期不符。
- **建议**：在 §7.4 澄清闭环后追加：
  > 收款人匹配次序：NFKC 后的姓名或别名**精确且唯一**命中时直接采用；没有精确命中时，同音 / 近形候选（`intent_lexicon.yaml` 的拼音规则）≥ 1 个即建 clarification；精确命中多于 1 个（同名不同账号）也建 clarification。

### P1-7 削减第 3 级把「频控」整体列为可削减，与不可削减的安全底线冲突
- **位置**：§13 削减第 3 级：「OTP 重发与频控（固定 1 次）」。
- **问题**：§7.4 的「同一 scope 最多 6 次 evaluate、3 个审批」（防探测、防审批疲劳）和 §7.3 的 PIN 锁定都属于 RATE_LIMITED 频控。原文没有限定范围，会被理解为这些都能砍，而 §13 写明「所有 P0/P1 安全问题」不可削减。
- **建议**：改为「OTP 重发（每个审批固定只发 1 次）；§7.4 作用域频控与 §7.3 PIN 锁定不可削减」。

---

## P2

### P2-1 接口计数与章节标题没有同步
- **位置**：§9.5 标题「L2 公共接口 G1–G21」，但表中已有 G22 和 G22a；§9.9「13 个 D 分组（D0–D12、D6a）」，实际是 D0–D12 共 13 个再加 D6a，共 14 个。
- **建议**：§9.5 标题改为「L2 公共接口 G1–G22a」；§9.9 改为「14 个 D 分组（D0–D12、D6a）」。

### P2-2 SKIPPED_REAPPROVAL 和 RETRYING 只同步了一部分
- **位置**：§8.3 `instruction_runs.status` 只有 SUCCESS / FAILED_INSUFFICIENT / BLOCKED，而 `event_executions` 与 §7.2 `event_decisions` 已含 SKIPPED_REAPPROVAL；§8.5 `/block` 只有 `code`。§9.5a S1 返回 `status:…/RETRYING`，§7.11 却写「返回 `DEPENDENCY_UNAVAILABLE`」。
- **建议**：`instruction_runs.status` 加 SKIPPED_REAPPROVAL；`/block` body 改为 `{status:BLOCKED/SKIPPED_REAPPROVAL,code,...}`；§7.11 改为「S1 返回 200 `{status:RETRYING, code:DEPENDENCY_UNAVAILABLE}`」。

### P2-3 §6.1.1 自称「全部返回值」，实际仍有缺口
- **位置**：§6.1.1 标题与 D34。
- **问题**：表中没有 `POLICY_CHANGED`（§9.8 说它与 STATE_CHANGED 同路径）、`IDEMPOTENCY_CONFLICT`、401 `TOKEN_EXPIRED`、403、404，也没有覆盖 STATE_CHANGED 的 `details.decision=ALLOW`（黄降绿）这种情况。
- **建议**：表中补两行：「409 `POLICY_CHANGED`：同 STATE_CHANGED」；「`details.decision=ALLOW`：→ execute（本轮仍计 1 次）」。再补一行：「401 / 403 / 404 / 409 `IDEMPOTENCY_CONFLICT`：respond（模板：请重新发起，未执行任何操作）」。

### P2-4 `load_bank_state` / `persist` 放在 bankcore 接口下，与「bankcore 纯函数、无 IO」矛盾
- **位置**：§7.7.1 标题「bankcore 接口（预演与执行共用的唯一实现）」列出了 `load_bank_state(conn, …)`、`persist(conn, …)`；D40 写「bankcore 统一 load_bank_state / persist」；§3.5 写「packages/bankcore（纯函数，无 IO）」。
- **建议**：§7.7.1 在代码块下加一句：
  > `scope_for`、`apply_*`、`to_public_effects`、`projected_shortfall` 在 `packages/bankcore`（无 IO）；`load_bank_state`、`persist` 在 `services/bank-backend/app/state.py`，是 L3 唯一实现，L2 只经 `/internal/snapshot` 间接调用。

  D40 的措辞同步修改。

### P2-5 A3 主描述「对源卡放松限额」与新增的「从无卡储蓄转出」变体矛盾
- **位置**：§10.2 A3：「新增钱八 → 对源卡放松限额 → ……3 个变体必须包含……『从无卡储蓄转出』」。
- **建议**：「对源卡放松限额」改为「对该客户任一卡放松限额（无卡储蓄变体放松活期卡）」。

### P2-6 第 5 幕推进的目标时刻没有落在扣费时刻之后
- **位置**：第 12 章第 5 幕：「推进至 10-25，健身商户仍请求扣费」；§2.2（D39）规定扣费时刻是当日 06:00。
- **问题**：如果按 10-25 00:00 推进，健身扣费还没有发生，断言会落空。
- **建议**：改为「推进至 2026-10-25 09:00」。

### P2-7 澄清相关字段没有闭合
- **位置**：
  - §7.2 `pending_clarifications` 表没有 G21 返回的 `kind`（SLOTS / FORM_REQUIRED）和 `prompt_template_id`。
  - §6.1 clarify 节点写「按 G21 的 `required_fields` 套 L2 模板」，而 G21 写「按 `prompt_template_id` 渲染」。
  - §6.1 写「G3 返回 INTENT_UNCLEAR」，但 G3 响应 `{utterance, clarification_id?, assistant_token, expires_at_wall}` 中没有解析状态字段。
- **建议**：`pending_clarifications` 表加 `kind`、`prompt_template_id`；§6.1 统一为「按 `prompt_template_id` 渲染」；`UtteranceRef` 增加 `parse_status: SCOPED/NO_WRITE/INTENT_UNCLEAR/CLARIFYING`，§6.1 路由按它判断。

### P2-8 `arg_sources` 的内部表示与 D33 不一致
- **位置**：§6.2：「契约内部的 Pydantic 表示仍是……`arg_sources: dict[param, Source]`……L2 只接收内部形式」；D33：「`arg_sources`……改为数组」。
- **问题**：G4 线上格式到底是 dict 还是数组，两处说法不一致。数组还能表达 dict 无法表达的重复参数，需要明确如何拒绝。
- **建议**：改为「内部与 G4 线上格式均为数组 `[{param, source}]`，同一 param 出现两次 → `CONTRACT_INVALID`；仅 `payee_id` 等实体引用在内部为 `str | EntityRef | None`」。

---

## 已核对、未发现回归的项
- 第 2 幕：600,000 − 300,000 − 4,500 = 295,500 分（2,955 元）；张三转后 3,000、王五 5,000；累计 7,000 < 10,000，单笔 ≤ 5,000。
- 第 6 幕：李四 1,200,000 − 4,500 = 1,195,500 分；10-15 是周四且在 15:00 前，T+1 = 10-16，nav = 1.2310 + 0.0001 = 1.2311；floor4(11955 / 1.2311) = 9710.8277。
- 第 7 幕：储蓄 18,000、活期 8,000。
- A1：700,000 − 600,000 = 100,000 > 4,500；第三笔时 SPLIT_TRANSFER 总额 600,000 > 500,000 升红；24h 累计 600,000 未超过 1,000,000。
- A3 与第 4 幕：40 分钟 > 30 分钟；8,000 ≤ 最高可设单笔 50,000，也不超过日累计 20,000。
- 【P0 实验】E1–E5 与 §6.1 的 interrupt 实验都有通过标准和回退；E6 只测量、修正预算，可以接受。
- 没有发现 LLM 参与授权的新表述；SCHEDULED_SHORTFALL 改为纯 L2 策略后，预演与执行仍共用 `apply_*`；A4 / A5 的判定仍以 L3 已提交记录为前提。
