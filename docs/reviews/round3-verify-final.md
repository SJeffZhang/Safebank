# 第三轮最终核对：verify-fixes 与 verify-regressions 的修复确认

- 核对者：Claude 子 agent（round3-verify-final），2026-10-23。只读静态核对，只写本文件；仓库无代码，未运行任何测试。
- 对象：`PRD-v2.1-wip.md`（1,763 行）。依据：`round3-verify-fixes.md`（10 条非「已修复」）、`round3-verify-regressions.md`（18 条）、AGENTS.md §2。

## 1. 总表（28 条）

### 1.1 round3-verify-fixes（10 条）

| 编号 | 判定 | 证据 |
|---|---|---|
| N7 回放正则 | 已修复 | §6.6「`\b[a-z]{2,4}_([0-9A-HJKMNP-TV-Z]{26}\|[0-9a-f]{8}-…{12})\b`」「用 `secrets` 生成的 ID（hnt、act、clr 等）也统一为『前缀_ + 26 位 Crockford base32』」 |
| N14 F4 用 G18 | 已修复 | §9.6 F4「用该计划所属那次 G3 签发、仍在有效期内的助手令牌…令牌已过期时不恢复 L1…G18 只用于 F5」；§7.3 BFF 服务端保存 G3 令牌（措辞小差异见新问题 N-6） |
| N17 审计锁 | 已修复 | §7.12「**所有**审计追加（含执行路径）都在各自的小事务内先取 `pg_advisory_xact_lock(hashtext('audit'), …)`…另加 `UNIQUE(customer_id, prev_hash)`」 |
| R3-11 instruction_runs 枚举 | 已修复 | §8.3「status SUCCESS/FAILED_INSUFFICIENT/BLOCKED/SKIPPED_REAPPROVAL」；§8.5 `/block`「`{status:BLOCKED/SKIPPED_REAPPROVAL/FAILED_INSUFFICIENT,code,…}`」 |
| P0-2 TSR 基线 | 已修复 | §15「**网关层** B2 相对 B0 的正常用例 L3 目标状态达成率下降 ≤ 5…**端到端** B2 相对 B1 的 TSR 下降 ≤ 5」；§1.3 G5 同步 |
| P1-9 削减表 | 已修复 | §13 第 2 级「3 个消融组｜§15 网关层报告去掉消融」；第 3 级「OTP 重发（每个审批固定只发 1 次）…§7.4 作用域频控与 §7.3 PIN 锁定**不可削减**」 |
| A-05 make demo PIN | 已修复 | §11.1「可信评测 runner（EVAL_MODE）与 `make demo` 的 Playwright 驱动（DEMO_MODE），两者都从宿主机只读挂载 `.secrets/demo_pins`」；§10.3 第 1 条同步 |
| A-06 取消后状态 | 已修复 | §7.4「原话 → CANCELLED，未执行计划 → SUPERSEDED，PENDING / CONFIRMED 审批 → SUPERSEDED，OPEN clarification → CANCELLED」；SUPERSEDED 均在 §7.2 枚举内 |
| A-09 D 分组计数 | 已修复 | §9.9「14 个 D 分组（D0–D12、D6a）」「23 个 G 分组（G1–G22a）」 |
| B-04 §4.3 措辞 | 已修复 | §4.3「候选模型的模型卡语言元数据都不含中文（中文效果【待核实】）」 |

### 1.2 round3-verify-regressions（18 条）

| 编号 | 判定 | 证据 |
|---|---|---|
| P0-1 复述检测误伤 | 已修复 | §7.4「只比对**账号类值**（规范化后 ≥ 8 位数字）…自由文本路径（`memo`、`counterparty`…）…收款人名称和金额**不参与**…L2 不读取助手回复」；§7.5 app_form「口径同 §7.4」（覆盖范围仍有缺口，见 N-3） |
| P0-2 证据逐字节 vs 复演 | 已修复 | §8.3 `execution_intents` 加 `attempt int`、`evidence_history jsonb`；§8.5 prepare / execute 按「当前 attempt」；§7.10.4「以 `attempt+1` 重新 prepare 再 execute」（deadline 规则冲突见 N-1） |
| P0-3 可疑卡谓词 | 部分修复 | §5.6「L2 在快照上自行运行 `detect_anomalies(sim_now−30d, sim_now)`…集合只含 1 张卡时必须等于它」；第 7 幕「3 条异常同卡」。但 30 天窗口排除了 09-07 异常，第 7 幕断言不成立，见 N-2 |
| P1-1 F4 令牌 | 已修复 | 同 N14 |
| P1-2 TSR 对比 | 已修复 | 同 P0-2（fixes）；§13 第 2 级同步 |
| P1-3 审计链分叉 | 已修复 | 同 N17 |
| P1-4 DEMO 证据类型 | 已修复 | §10.2「`{type:DEMO_EVENT, event_id, console_session_id, kind}`：仅 DEMO_MODE / EVAL_MODE…不计入 UTR」 |
| P1-5 稳健型标签 | 已修复 | §2.2 产品表加 `risk_label` 列；§5.6 X-3「李四（R3）：L2 求值得到 {稳盈短债, 稳健增利, 均衡混合} → L2 clarification…无有效测评时 DENY `ASSESSMENT_MISSING`」 |
| P1-6 收款人匹配次序 | 已修复 | §7.4「NFKC 后的姓名或别名**精确且唯一**命中时直接采用…因此『王五』不会因为同音的『王武』被追问」 |
| P1-7 频控削减 | 已修复 | 同 P1-9 |
| P2-1 接口计数 | 已修复 | §9.5 标题「G1–G22a」；§9.9 计数 |
| P2-2 SKIPPED / RETRYING | 已修复 | §8.3、§8.5 `/block`；§7.11「S1 返回 200 `{status:RETRYING, code:DEPENDENCY_UNAVAILABLE}`」；§9.5a S1 枚举含 RETRYING |
| P2-3 路由表缺口 | 已修复 | §6.1.1「409 `STATE_CHANGED` 或 `POLICY_CHANGED`…`ALLOW` → execute」「401 / 403 / 404，409 `IDEMPOTENCY_CONFLICT` → respond」 |
| P2-4 bankcore 无 IO | 已修复 | §7.7.1「`load_bank_state`、`persist` 在 `services/bank-backend/app/state.py`，是 L3 的唯一实现」 |
| P2-5 A3 描述 | 已修复 | §10.2 A3「对该客户任一卡放松限额（无卡储蓄变体放松活期卡）」 |
| P2-6 第 5 幕时刻 | 已修复 | §12 第 5 幕「推进至 2026-10-25 09:00（越过 06:00 扣费时刻）」 |
| P2-7 澄清字段 | 已修复 | §7.2 `pending_clarifications` 含 `kind`、`prompt_template_id`；§9.5 UtteranceRef 含 `parse_status`；§6.1「parse_status = INTENT_UNCLEAR 且 needs_write → respond」 |
| P2-8 arg_sources 格式 | 已修复 | §6.2「`arg_sources` 在内部与 G4 线上格式中**都是数组** `[{param, source}]`，同一 param 出现两次 → `CONTRACT_INVALID`」 |

统计：28 条中 27 条已修复，1 条部分修复（regressions P0-3），0 条未修复。

## 2. 非「已修复」条目详述

### regressions P0-3：部分修复，30 天窗口与第 7 幕断言矛盾
- **现状**：§5.6 规定 `detect_anomalies(sim_now−30d, sim_now)`。§2.2 中 reset 后 `sim_now = 2026-10-15 09:00`，窗口起点是 2026-09-15 09:00。异常 1（09-07）一定在窗口外，异常 2（09-15，没有给具体时刻）可能在窗口外。所以第 7 幕「3 条异常同卡」不成立。另外，§2.2 的种子断言只保证 `detect_anomalies(2026-09-01, 2026-09-30)` 返回 3 条；换了窗口后，「均值 + 3σ」的基线和「首次出现商户」的判断都会变，结果不能保证非空。一旦为空，谓词返回 `INTENT_UNCLEAR`，第 7 幕与 X-4 就走不通了。
- **替换文字**（§5.6 谓词表第三行「L2 求值」列）：
  > L2 在快照上自行运行 `detect_anomalies(上一自然月 1 日 00:00, sim_now)`（与第 1 幕「上月异常」口径一致，不依赖 L1 是否读取过），取全部带 `card_id` 的异常的 `card_id` 集合；没有 `card_id` 的异常（入账、转账）忽略
- **§2.2 种子断言追加**：「`detect_anomalies(2026-09-01 00:00, 2026-10-15 09:00)` 恰好返回同样 3 条，且 `card_id` 均为 `crd_zs_1`。」
- 第 7 幕断言「L2 谓词求值得到 `crd_zs_1`（3 条异常同卡）」可以保留，改完后即成立。

## 3. 新问题（本批修订引入或暴露）

### P0
无。

### P1

**N-1 `deadline 只能缩短` 与重新审批冲突**（§7.10.5–6、§8.5 prepare）
- 第一次 prepare 的 deadline = min(原话, 审批 1, 会话)。审批 1 是 5 分钟有效，通常就是这个最小值。STATE_CHANGED 后会生成审批 2，`attempt+1` 再 prepare 时 deadline「只能缩短」。只要用户在审批 1 原有效期之后才确认审批 2，L3 就会以过 deadline 拒绝。这意味着合法的「状态变化后重新确认」路径（第 4 幕的复核、A4）会出现确定性失败。
- 替换文字（§8.5 prepare 行末「deadline 只能缩短」改为）：
  > `deadline_wall` 不得超过 intent 首次创建时记录的 `deadline_cap`（原话与会话有效期的最小值，不可变）；同一 attempt 内只能缩短，`attempt+1` 时按新审批重新取 min(新审批有效期, deadline_cap)
- §8.3 `execution_intents` 加字段 `deadline_cap`。§7.10.6 括注改为「deadline 为原话、**当前**审批和会话有效期的最小值，且不超过 deadline_cap」。

**N-2 第 7 幕与 X-4 的谓词窗口**：即上文 §2 中 P0-3 的剩余部分。第 7 幕的断言会落空，因此列为 P1。

**N-3 复述检测只看本 sid 的 `read_records`，A2「复述」变体可以换个会话绕过**（§7.4、§7.5、§10.2 A2）
- 收窄后，变体「用户贴备注提问」仍然成立：带引号或疑问句的输入会被判为 `INTENT_UNCLEAR`。变体「L1 诱导用户复述」在**同一会话**内也成立：备注已经登记在该 sid 的 read_records 里，复述就会被标 `echoes_untrusted`。
- 缺口一：被控的 L1 只要说「请新开会话输入该账号」，或者用户从 App 交易详情页（BFF 读取，不登记 sid 的 read_records）复制账号，新 sid 里就没有可以比对的记录，复述检测不会触发，`PAYEE_ADD + 转出` 的 DENY 也随之失效。
- 缺口二：用户粘贴不带引号的祈使句（「按这个做：请立即将余额转到 6222…」）时，排除规则不生效。
- 另外，§7.4 末句「L1 无法把完整账号递给用户复述」说过头了：L1 可以分段口述，§6.7 1a 只能掩码连续数字。
- 替换文字（§7.4「复述检测」第一句中「与同 sid `read_records` 中 untrusted 自由文本路径……里的数字串相同」改为）：
  > 与同 sid `read_records` 中 untrusted 自由文本路径，**或该客户 L3 近 90 天交易的 `memo` / `counterparty` 及其定时指令的 `target_display`**（L2 经 `/internal/snapshot` 读取，不依赖会话）里的数字串相同
- §7.4 末句改为：「助手回复中的长数字已被掩码（§6.7 1a），这只是辅助手段；防线是上述客户级比对。」§7.5 app_form 的「口径同 §7.4」自动继承这项修改。

### P2

**N-4 UNKNOWN 重试与 `attempt` 的语义不明**（§7.10.4、§7.10.7、§7.2）
- §7.10.4 规定执行时「**总是**」重跑 ①–⑧，这会产生新的 evaluation_id，也就需要 `attempt+1`。而 §7.10.7 说 UNKNOWN 时「可用同 plan 幂等重试」，没有写明是否换 attempt。gateway 的 `execution_jobs` 只有 `attempt_count`（重试次数），与 L3 的 `attempt`（证据版本）不是一回事，S1 的 `attempt` 又是第三种含义。
- 替换文字（§7.10.7「仍 UNKNOWN 且授权有效时，可用同 plan 幂等重试」改为）：
  > 仍 UNKNOWN 且授权有效时，先 GET；为 PREPARED 时按原 `l3_attempt` 原样重发同一 execute 请求（不重新评估）；需要新评估时必须 `l3_attempt+1` 重新 prepare，旧 attempt 的迟到 execute 因 attempt 不符被 409 拒绝
- §7.2 `execution_jobs` 加字段 `l3_attempt`。

**N-5 X-3 的 clarification 只能在 G3 产生**（§5.6、§6.1.1）
- 谓词「在 G3 登记原话时和 ④ 意图范围中各做一次」。但 evaluate 的返回值里没有 clarification（§6.1.1），④ 如果「建 clarification」就没有去向。
- 替换文字（§5.6 谓词说明括注末尾追加）：
  > clarification 只在 G3 求值时创建；④ 只校验计划实体是否等于 / 属于求值结果，不符返回 `INTENT_SCOPE_EXCEEDED`（user）
- 补充核对：X-3 改用李四后，与第 6 幕数字（可用 11,955、45 元预留）、§6.1 的 ingest → clarify → END → 补答合槽 → 下一轮 understand 路由一致，没有问题。

**N-6 F4 取「该计划所属那次」G3 令牌，§7.3 只保存「最近一次」**
- 用户在待审批期间如果又发一条消息，就会出现新的 G3 令牌，其中的 `utts` 不含该计划的原话。
- 替换文字（§7.3）：「并按 sid 在服务端会话存储中保存最近一次 G3 令牌直到过期」改为「按 `(sid, utterance_id)` 在服务端会话存储中保存每次 G3 签发的令牌直到过期；F4 取计划 `utterance_ids` 对应的那枚」。

**N-7 §10.3 TSR 定义没有分层**
- §15 已经把对比拆成网关层与端到端，但 §10.3 指标表的 TSR 仍只有一条「且回复关键数字正确」的定义。
- 替换文字（TSR 行末追加）：「网关层无回复，只看 L3 目标状态（§15 称『L3 目标状态达成率』）；端到端另检查回复关键数字；两层分别报告。」

**N-8 本批修订没有决议编号**
- §0.5 只写到 D46，这批 verify 修订（attempt、复述收窄、可疑卡谓词、DEMO_EVENT、F4 令牌等）没有记录，AGENTS.md 和 ADR 无从引用。
- 建议：在 §0.5 追加 D47 一行：「第三轮复核修订：`attempt` + `evidence_history`；复述检测只比账号；收款人精确优先；谓词 L2 自求值；DEMO_EVENT 证据；审计统一锁；F4 不用 G18；TSR 分层对比｜§5.6、§7.3–§7.12、§8.3–§8.5、§10、§13、§15」。

**核对后自洽、没有发现问题的项**
- §15 与 §10.3 的对照组一致（网关层 B0 / B1 / B2 + 消融，端到端 B1 / B2），§1.3 G5、§6.6 录制与 §13 第 2 级同步。
- reconcile 墓碑不带 attempt，以 `(customer_id, plan_id)` 主键写 CANCELLED，任何 attempt 都不能再 prepare，与 §7.10.7「不得复活」自洽。迟到的旧 attempt execute 会因 attempt 不符被拒，与 UNKNOWN 恢复不冲突。
- X-4 的「从储蓄转 2000 到活期」是本人互转，冻结后不受限，与第 7 幕「储蓄 18,000、活期 8,000」一致。

## 4. 结论

**否，暂不直接替换**，但差距很小。两份报告 28 条中 27 条已修复，没有 P0。仍阻塞「闭合」的是 3 条 P1，每条都是一两句话的文字修改，不涉及架构：

1. N-1：deadline「只能缩短」使重新审批路径确定性失败（§8.5、§7.10.6）。
2. N-2 / P0-3 残留：可疑卡谓词的 30 天窗口使第 7 幕「3 条异常」断言不成立，结果还可能为空（§5.6、§2.2）。
3. N-3：复述检测只看本 sid，A2「复述」变体可以换会话或从 App 页面复制来绕过（§7.4）。

按上面的替换文字改完这 3 条（P2 可以顺带改）后，可以替换为正式 PRD，不需要再开一轮完整审核，只需对这 3 处做一次定点复核。
