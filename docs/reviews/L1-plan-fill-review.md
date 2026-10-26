# L1-plan-fill 独立只读评审

**范围**: `df8eddc..9416ced` — `services/agent/app/llm/plan.py` 与 `tests/agent/test_llm_adapters.py`
**评审标准**: ①校验只拦真正错误 ②memo 关键词不误拒/不漏 ③不复制 L2 授权/不静默改值/不增预算 ④重试耗尽行为不变 ⑤不改原有测试/共享夹具
**结论**: **APPROVE**（P0 无，P1 1 条，P2 3 条）

---

## ① 校验只拦真正错误，不会拒绝合法计划

- **`_required_value_problems`**:
  - `"" / "__NULL__"` 检测：只对非 `_NULLABLE_PARAMS`（memo/phone/alias/end_at）生效。`to_account_id` 为 kind=none 时在 PlanDraft 中是 `None` → `if value is None: continue` 正确跳过。可空参数合法使用 `__NULL__` → `if field_name in _NULLABLE_PARAMS: continue` 正确跳过。✅
  - ID 前缀检测：`_ID_PREFIX` 映射 `from_account_id→acc_`、`card_id→crd_`、`payee_id→pye_` 等，与 PRD §9.1 前缀约定一致。`payee_id` 在 PlanDraft 中是判别对象而非普 string → `isinstance(value, str)` 为 False，前缀检测不适用（正确）。✅
  - `schedule_transfer` 的 `from_account_id` 同样以 `acc_` 开头 → 合法；`card_id` 以 `crd_` 开头 → 合法；`merchant_id` 以 `mer_` 开头 → 合法。✅

- **`_semantic_problems`**:
  - 仅检查 utterance 来源的 memo（read/derived/hint/action 放行）→ 不会误拒从已读数据派生的 memo。✅
  - 关键词列表 `{"备注", "附言", "留言", "memo", "note"}` — 覆盖常见中文与英文请求。✅

--- 合法计划不误拒 ✅ ---

## ② memo 关键词规则

- **关键词覆盖**: "备注"/"附言"/"留言"/"memo"/"note" 覆盖了常见 memo 请求。✅
- **P2-1**: 判别跨越全 utterance text 而非 span 内文本。若用户 utterance 别处含"备注"但 memo span 取自不含关键词的片段，检查通过（漏报）。同时若用户用较少见的词如"附注"请求 memo，关键词列表不包含，memo 会被拒绝后重试去掉（误拒，memo 被静默丢弃）。两方向均属低概率边界；前者模型还需通过 span verbatim 检查，后者在 live 中未见实例。
- **P2-2**: `_OPTIONAL_PARAM_KEYWORDS` 仅覆盖 `memo`。若模型为 `phone`/`alias`/`end_at` 声明 utterance 来源，`_semantic_problems` 因 param 不在表中直接跳过，完全靠 prompt 约束而无本地校验。风险低——live 中未观察到模型擅自填这些字段，且 prompt 已明确禁止。

--- memo 关键词: 核心覆盖有效，有 2 条 P2 边界 ---

## ③ 不复制 L2 授权 / 不静默改值 / 不增加请求预算

- **结构校验 ≠ 授权**: 新检查为前缀/空值/关键词匹配——属于格式与结构合法性，不是金融授权。L2 仍独立做污点追踪、预演、工作流检查、审批。✅
- **不改值**: 两个新函数只返回 problem strings，由 PlanAdapter 以 `ReferenceError` 抛出进入既有重试循环；不修改 PlanDraft 的任何字段。✅
- **预算不变**: `max_retries` 仍为 1（最多 2 次 LLM 调用）。新增检查复用同一 retry loop 与 `ReferenceError` 异常类，未增加额外 LLM 往返。⚠️ 但见 §④。✅

--- 不越界 L2 授权，不改值，理想路径预算不变 ✅ ---

## ④ 重试耗尽后的行为

- 耗尽时行为与原先一致：`if attempt == self._max_retries: raise` → 原始 `ReferenceError` 向上传播。✅
- **P1**: 三个检查函数**串行短路**——`_reference_problems` 先执行，有错误立即 raise；`_required_value_problems` 和 `_semantic_problems` 在它之后才运行。若一次 LLM 响应**同时存在**参考来源错误和 ID 前缀/空值错误，只有参考错误被报告给模型重试；模型修正参考来源后重试，若仍存在值错误则 attempt=1 直接失败，没有修正机会。即两轮 LLM 调用的第二轮只收到第一类错误的反馈，第二类错误从无反馈就被拒绝。
  - 影响：live 中如果模型同时写出错误 span 和 `from_account_id="pye_zs_ww"`（如测试 `test_plan_retries_on_wrong_id_prefix_from_account` 场景），如果该计划恰好也有 span 越界等问题，模型不会收到前缀错误提示。
  - 缓解：live 实测 6/6 正常走到 G4 REQUIRE_APPROVAL，表明实践中同时多类错误的概率不高。但不排除未来语料复杂度上升后触发。
  - 修复：三个检查各自独立收集 → 合并一个 problems 列表 → 一次 raise（或至少把值/语义检查放在参考检查之前，因为它们比 span/pointer 问题更容易被模型理解并修正）。

- **P2-3**: 重试反馈消息固定为「上次计划的来源引用与给定数据不符」——当错误来自 `_required_value_problems`（`__NULL__` 或前缀错误）或 `_semantic_problems`（unsolicited memo）时，消息不完全准确（这些不是"来源引用"问题）。反馈体中的 `problems` 详情仍然正确，只有前缀文本偏了。不影响功能但降低调试可读性。

--- 重试耗尽行为一致，P1 为串行短路导致部分错误无反馈 ---

## ⑤ 原有测试断言 / 共享夹具未修改

- `git diff` 确认：test 文件中所有新增行在 `@@ -758,3 +758,475 @@` 之后——原有 758 行未改。✅
- `conftest.py` 零 diff。✅
- 原有 `test_normalized_enum_string_not_false_rejected`（变更前文件末尾）未受任何影响。✅

--- 原有测试/夹具未改 ✅ ---

## 总结

| 等级 | 条数 | 描述 |
|------|------|------|
| P0 | 0 | — |
| P1 | 1 | 三个检查串行短路：同时有 ref+value 错误时，value 错误无反馈 |
| P2 | 3 | ① memo 关键词仅匹配全文本而非 span + 缺"附注"等罕见词 ② `_OPTIONAL_PARAM_KEYWORDS` 仅覆盖 memo ③ 重试反馈消息前缀不准确 |

**判定**: APPROVE。P1 可在后续迭代中改为合并收集、不阻塞本分支合入（live 实测 6/6 通过且复合错误概率低）。三条 P2 均为边界情况，不影响核心校验有效性。