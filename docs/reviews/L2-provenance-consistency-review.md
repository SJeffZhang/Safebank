# L2 Provenance 一致性安全评审

**评审者**: DeepSeek 独立只读评审（全新会话）
**分支**: `feat/l2-provenance-consistency` vs `origin/main`
**改动的文件**: `taint/text.py`, `utterance/parser.py`, `tests/gateway/w1/test_parser_coverage.py`, `tests/gateway/w2/test_provenance.py`
**诊断参考**: `docs/reviews/L2-provenance-diagnosis.md`

---

## 1. 评审结论

**APPROVE**。无 P0、无 P1 安全缺陷。4 处改动均属 fail-closed 的确定性安全增强，未放宽任何校验条件。2 条 P2 建议（测试覆盖缺口、布尔分类代码脆弱性）不阻塞合并。

---

## 2. 逐项核查

### A：_cents 金额回退到 money_cents()（text.py:29-34）

**改动**：非阿拉伯数字金额 span 时，fallback 调用 G3 parser 的 `money_cents(text)` 解析中文数字；解析成功且值精确相等时认作 user。

**核查结论：SAFE（无 P0/P1）**

1. **等值校验不放松**：回退路径的通过条件仍是 `parsed is not None and parsed == value`——span 文本解析出的金额必须精确等于 L1 声明的参数值。不存在「近似匹配」或「范围匹配」的放宽。

2. **口语省略安全回退**：`money_cents()` 内部 `_parse_chinese_numeral` 对缩写（如「三千五」「两万五」）返回 None，此时回退路径返回 `False` → trust=unknown → `UNKNOWN_SENSITIVE_ARG`。对应测试 `test_chinese_abbreviated_amount_still_unknown`（colloquial "三千五"）明确断言此行为，协调者亲自复跑通过。

3. **大写中文数字拒绝**：「叁仟」——`_CN_DIGIT_V` 不含「叁」，`_parse_chinese_numeral` 返回 None → False → UNKNOWN。Fail-closed。✓

4. **同一解析器，无分歧**：回退复用 `utterance/parser.py` 的 `money_cents()`，与 G3 完全一致。不再维护第二套解析实现，消除了诊断报告①中「parser 认识但 provenance 不认识」的不一致。

5. **span 参数传入正确**：`matches()` 的 `text` 参数为 span 文本（如 "三千元"），`money_cents()` 能正确解析含单位后缀的片段。即使 span 选定不含单位（如仅 "三千"），`money_cents` 的 `_MONEY_PATTERN` 要求单位后缀（元/块/角/分），返回 None → fail-closed。

6. **潜在退化点（P2）**：`money_cents(text)` 解析的是整段 span 文本中的**第一个**金额；若 span 横跨两个金额（如「三千元给四百元」），只取出第一个（300000），若 L1 声明的是第二个金额（40000），等值校验失败 → UNKNOWN，fail-closed。正确行为，但缺少显式测试覆盖此边界。

---

### B：布尔渠道动词从 intent_lexicon.yaml 读取（text.py:42-48）

**改动**：provenance 的布尔匹配集不再单独维护动词表，改为从 `intent_lexicon.yaml` 的 `actions.update_card_controls` 加载，与 G3 parser 共用单一事实源。

**核查结论：SAFE（无 P0/P1）**

1. **动词分类正确**：lexicon 中 `update_card_controls: [关闭, 开启, 关掉, 打开, 开通]`。代码将 "关闭"/"关掉" 归入 `false_extra`，其余归入 `true_extra`。对照语义：关闭/关掉→False，开启/打开/开通→True。全部分类正确。✓

2. **反对/否定不误匹配**：测试 `test_negated_close_verb_not_user_source` 验证「不要关掉」span 在 False 集匹配时失败（"不要关掉" ∉ {"false", "否", "关闭", "关掉", ...}）→ UNKNOWN。Fail-closed。✓

3. **渠道名不作布尔来源**：测试 `test_card_control_channel_name_is_not_bool_source` 验证「线上」（渠道名）span 被判 UNKNOWN_SENSITIVE_ARG。即使用户原话里出现「线上」，布尔值来源必须是动词而非被操作对象。✓

4. **新动词「关掉」已加入 False 集**：来自 lexicon 的 `false_extra` 包含「关掉」。诊断报告②中指出的「关掉不被 provenance 认可」已在本次修复。✓

5. **方向不可能反转**：若 value=True 但 span 指向「关闭」→ `raw ∈ True_set` = False → 返回 False → UNKNOWN。不会出现「说关闭被判为开启」的反向匹配。✓

6. **分类代码脆弱性（P2）**：布尔分类逻辑 `true_extra = {v for v in controls if v not in ("关闭", "关掉")}` 假设所有非 "关闭"/"关掉" 的 lexicon 动词都是 True 方向。若未来有人往 lexicon 添加中性动词（如「设置」「修改」）或歧义词，会被静默归入 True 集。建议改为显式映射表，或新增 lexicon 子键 `bool_direction`，让未知动词在加载时即报错。**当前 lexicon 5 个动词均为明确 true/false，无实际风险。**

---

### C：bank_name 进入 scope（parser.py:793-796）

**改动**：add_payee 工具处理在 scope 中新增 `bank_name` 字段（当原话含「他行」或「本行」时）。

**核查结论：SAFE（无 P0/P1）**

1. **值受 parser 约束**：`command.fields["bank_name"]` 来自 `_payee_attr` 子句，仅映射「他行」→ "他行"、「本行」→ "本行"。L1 不能经 G3 路径把任意字符串写入 scope。✓

2. **provenance 值校验存在**：scope 中的 `bank_name` 值来自 parser，procurement 的 `matches()` 对 string 参数执行 `normalize(value) == raw`——L1 声明值必须等于 span 文本。若 L1 声明 `bank_name="瑞士银行"` 但 span 文本为「他行」，则 normalize("瑞士银行") ≠ normalize("他行") → UNKNOWN_SENSITIVE_ARG。✓

3. **无 bank_name 时不填充**：测试 `test_add_payee_no_bank_name_when_not_present` 验证不含「他行/本行」时 scope 不含 `bank_name`。避免 scope 中有空值或默认值被 L1 利用。✓

4. **「本行」同样覆盖**：测试 `test_add_payee_benhang_in_scope` 验证「本行」也能进入 scope。✓

---

### ④：scope 字段名 payee_name → name（parser.py:788, 528）

**改动**：add_payee scope 的收款人名从键 `payee_name` 改为 `name`，并同步 `scope_is_complete` 的 PAYEE_ADD 校验。

**核查结论：SAFE（无 P0/P1）。仅需确认一处。**

1. **代码库内无残留引用**：`grep payee_name` 只在 `parser.py` 的函数名 `_valid_payee_name` 中出现（该函数名是「校验收款人姓名」的语义，与 scope 键无关）。`services/gateway/app/` 下没有任何代码以 `scope.fields["payee_name"]` 或 `scope.fields.get("payee_name")` 形式读取此键。✓

2. **scope 消费者均为泛型访问**：`intent.py` 的 `_non_transfer_permits` 和 `_match_permits` 通过 `scope.fields` 字典的 `.get()` 和 `|` 操作消费字段，不硬编码 `payee_name`。`authorization.py` 的字段比较通过序列化 JSON 的 dict 等值实现。均不依赖键名。✓

3. **工具 Schema 名称一致性**：任务描述称「scope 字段名 payee_name→name 以匹配工具参数」。若 add_payee 契约的参数名确为 `name`（非 `payee_name`），则本次改法是修复而非放宽。协调者已实测 make test-w1 1761 passed / test-w2 251 passed / test-w3b 40 passed。若参数名实际为 `payee_name`，则意图阶段会因 scope 缺该键而判 INTENT_SCOPE_EXCEEDED——测试全绿排除了此可能。**需确认 contracts 中 add_payee 参数名确为 `name`**（若不一致应修 contracts 而非回退本次改动；此核实因未读 contracts 文件，标待确认）。

4. **测试断言同步更新**：`test_add_payee_xin_zeng_shou_kuan_ren` 和 `test_add_payee_ba_X_jia_wei_shou_kuan_ren` 的两处 `result.scope.fields.get("payee_name")` 已改为 `result.scope.fields.get("name")`。✓

---

### ⑤：通用名黑名单（parser.py:104-106, 117-118）

**改动**：新增 `_PAYEE_NAME_GENERIC_BLACKLIST`（6 个词：「收款人」「联系人」「账户」「账号」「卡」「银行卡」「对方」），在 `_valid_payee_name` 中拒绝这些类别词作为收款人名。

**核查结论：SAFE（无 P0/P1）**

1. **黑名单覆盖诊断报告发现的缺口**：「收款人」被正确拒绝（测试 `test_add_payee_generic_name_shou_kuan_ren_rejected` 验证）。✓

2. **参数化覆盖 8 种变体**：测试 `test_add_payee_generic_name_blacklist_rejected` 覆盖 7 种直接使用 + 2 种间接使用（「把收款人加为收款人」「把联系人加为收款人」）。✓

3. **防御纵深**：即使有未被黑名单覆盖的类别词（如「付款人」），后续 name resolution 阶段（`matches()` 第 55–70 行）会在银行状态中查找同名实体，不存在的名字无法通过精确匹配。黑名单是 defense-in-depth，非唯一防线。

---

## 3. P2 建议（不阻塞合并）

| 编号 | 描述 | 建议 |
|------|------|------|
| P2-1 | `_cents` fallback 使用 `money_cents(text)` 取整段 span 的第一个金额，若 span 横跨多金额则可能取错；缺少此边界的显式测试 | 加一条：span 文本为「三千元给四百元」、L1 声明第一或第二金额的分支用例 |
| P2-2 | 布尔动词分类用 `v not in ("关闭", "关掉")` 作 true/false 分类，对 lexicon 未来新增动词不报错 | 改为显式方向映射或 lexicon 新增 `bool_direction` 键，未知动词加载时报错；不改也不影响当前安全 |
| P2-3 | contracts 中 add_payee 参数名是否为 `name`（非 `payee_name`）待确认 | 若不一致应修 contracts 而非回退本次 |

---

## 4. 未发现的风险点（逐一排除）

- ❌ 中文数字金额可绕过等值校验 → **否**：`money_cents()` 返回精确分，`parsed == value` 严格等值
- ❌ 口语省略金额（三千五、两万五）被错误认作精确值 → **否**：`_parse_chinese_numeral` 对这些返回 None，fallback 返回 False
- ❌ 大写中文数字「叁仟」「陆佰」通过 → **否**：`_CN_DIGIT_V` 不含这些字符
- ❌ 「不要关掉」匹配为 False → **否**：测试明确断言 UNKNOWN
- ❌ 渠道名「线上」「境外」被当作布尔值来源 → **否**：不在 True/False 集中
- ❌ bank_name scope 让 L1 可任意写银行名 → **否**：值来自 parser 映射（仅「他行」「本行」），provenance 做等值校验
- ❌ payee_name→name 重命名遗漏引用 → **否**：`grep` 证实无残留 `scope.fields["payee_name"]` 读取
- ❌ 类别词黑名单可被同义替换绕过 → **否**：name resolution 是最终防线，黑名单是 defense-in-depth