# C1e 独立评审 — Output Verification, Number Masking, Deterministic Cards & Replay

**评审日期**: 2026-10-25
**评审范围**: `git diff 0472689 17204bd`（18 files, +4136/−39）
**评审结论**: **REQUEST_CHANGES**（P1×3，P2×3）

---

## P0

无。

## P1

### P1-1 `cn2an` 中文数字提取未实现（verify.py:50–72）

**证据**: 任务书 §4（verify_output）明确要求「中文数字通过 `cn2an` → 换算为分」，`C1e-design-notes.md:122` 注明 `cn2an` 需新增依赖。实际 verify.py 的 `extract_amounts()` 只处理 `¥/￥/元/块` 的阿拉伯数字，不处理「八百元」「三千块」等中文数字。`cn2an` 不在 `pyproject.toml` 依赖中。

**失败场景**: LLM 回复「已向赵六转账八百元」，提取结果为空 → 无法校验金额事实性。

**修复建议**: 新增 `cn2an` 依赖，在 `extract_amounts()` 中增加中文数字正则（`[零一二三四五六七八九十百千万亿]+[元块]`），通过 `cn2an.cn2an()` 转换为阿拉伯数字后计算分。

### P1-2 `mask_long_numbers` 多连续分隔符绕过（verify.py:35）

**证据**: `_DIGIT_RUN_RE = re.compile(r"\d+(?:[ -]\d+)*")` 中的 `[ -]` 只匹配**单个**空格或连字符。连续分隔符如 `6222  0212`（双空格）、`6222--0212`（双连字符）会被拆成两个独立 run（各 4 位数），均不满足 ≥8 位数阈值，逃逸掩码。

**失败场景**: 攻击者构造回复 `卡号6222  0212  3456  7890`，每个 run 4 位，全部逃逸，完整卡号泄露。

**修复建议**: 改为 `\d+(?:[ -]+\d+)*`（`+` 允许连续分隔符），或在多分隔符场景下仍统计跨分隔符总位数。

### P1-3 `generate_cards` 无卡片数量上限（cards.py:264–329）

**证据**: 任务书 Q3 协调者裁定「候选上限 5」。`generate_cards()` 无任何上限逻辑。当 `reads` 包含 6+ 个 read 记录且 `needs_write=false` 时，`_cards_from_reads` 会为每个 read 生成一张卡片，超出 5 张上限。

**失败场景**: 查询「分析我的财务状况」，L1 可产生 ≥6 张数据卡片（ACCOUNT/SUBSCRIPTIONS/CARD/TRANSACTIONS 等），超出协议约定。前端卡片数量无界增长可能导致 SSE 事件过大。

**修复建议**: 在 `generate_cards` 末尾 `return tuple(cards[:5])` 或在 `_cards_from_reads` 中加计数守卫。优先保留结论性卡片（BLOCKED/PLAN_STATUS/REPLAN_HINTS），再填充数据卡片。

---

## P2

### P2-1 `test_with_llm_repair` 断言语义不足（test_verify.py:371–395）

**证据**: `test_with_llm_repair` 构造了 LLM 回复 `900.00元`（事实源余额 520000 分 = 5200.00 元），但 FakeLLM 固定返回 `800.00元`。测试仅断言 `len(result) > 0`，既未验证修复是否发生、也未验证修复结果是否与事实源一致。

**失败场景**: 若修复路径被错误跳过（回归），测试仍 1 passed，覆盖空洞。

**修复建议**: 至少断言 `"800.00" in result`（FakeLLM 返回的修复后值出现在最终输出中），且 `"900.00" not in result`。

### P2-2 `_scrub_sensitive` 误 redact `token_usage` 等响应字段（replay.py:452–457）

**证据**: `_is_sensitive_key` 使用子串匹配（`sensitive.lower() in lower`），敏感关键词含 `"token"`。DeepSeek API 响应的 `usage` 块含 `completion_tokens`、`prompt_tokens`、`total_tokens` 等字段，均被误判为敏感键并替换为 `<REDACTED>`。

**失败场景**: 录制文件中 `response.usage` 的 token 计数全部丢失，回放时无法诊断 token 消耗量。不回放时不影响功能。

**修复建议**: 将 `_SENSITIVE_KEYS` 中的 `"token"` 改为更精确的匹配（如 `"access_token"`、`"service_token"`、`"assistant_token"`、`"jwt"`），或改为整词/前缀匹配而非子串匹配。

### P2-3 `_AMOUNT_TEXT_RE` 不保护带小数点+元的金额（verify.py:30）

**证据**: `_AMOUNT_TEXT_RE = r"(?:¥|￥)[\d,.]+|[\d,]+\s*(?:元|块)"`，其中 `[\d,]+` 不含 `.`。若 LLM 产生 `10000000.00元` 这种带小数点 + 元后缀的金额表述，`_AMOUNT_TEXT_RE` 无法匹配（`[\d,]+` 停在 `10000000`，后续 `\s*` 无法匹配 `.`），金额不被保护，其中 `10000000`（8 位）被掩码为 `****0000`。

**失败场景**: 极端金额（≥1000 万元）以 `.00元` 格式出现时，「10000000 元」被错误掩码。实际发生概率极低——演示种子最大余额 ~6000 元，且此类金额通常用 `¥` 前缀格式。

**修复建议**: 改为 `[\d,.]+\s*(?:元|块)`，或将金额保护与 extract 逻辑统一（金额提取已正确支持此格式）。

---

## 已核通过项

1. **E1 回放键**：`replay_key()` 严格 `sha256(model ‖ canonical(messages) ‖ canonical(tools) ‖ canonical(params))`，`test_different_model_different_key` 与 `test_different_temperature_different_key` 验证 model 和 temperature 均参与计算。种子白名单 26 个 ID 全部保留，运行时 ID 正确归一化。

2. **E2 输出校验**：抽取值与事实源比对（±1 分 / ±0.1% 容差），LLM 修复仅一次、修复结果再校验，UNKNOWN/FAILED 语义检查到位，校验失败后正确掩码并返回原文。

3. **E3 掩码（E3 补充）**：不再依赖 `\b`，紧贴中文、标点、行首行尾的 ≥8 位数字全部命中。5 个协调者指定样例（卡号紧贴中文、转账中文两侧、空格分隔、连字符分隔、标点相邻）全部测试通过。日期和金额例外正确保护，例外偏差已在 ADR-004 说明。P1-2（多连续分隔符）为边界缺口。

4. **E4 卡片**：CardAction 仅 HINT/CLARIFY/NAVIGATE/OPEN_APPROVAL 四种，无可见文字。`_UNTRUSTED_FIELDS` 包含 name/memo/merchant_name/alias 等，递归过滤。data 仅取 ReadEnvelope.data 或 Decision/ExecutionResult 子集。

5. **E6 录制清洗**：`_scrub_sensitive` 覆盖 JWT/API key/Authorization/service_token，小写不敏感，嵌套递归（depth≤10）。测试验证 JWT payload 与明文 token 均 `<REDACTED>`。文件头含 schema_version/profile。

6. **冻结文件**：packages/contracts、services/gateway、docker-compose.yml、AGENTS.md、PRD.md 零改动。

7. **E8 范围**：make demo 保持 exit 2。

---

## 定点验证（评审亲跑）

```
.venv/bin/pytest tests/agent/test_replay.py::TestReplayKey -q  →  5 passed
.venv/bin/pytest tests/agent/test_verify.py::TestMaskLongNumbers -q  →  14 passed
.venv/bin/pytest tests/agent/test_replay.py -q  →  46 passed
.venv/bin/pytest tests/agent/test_verify.py -q  →  55 passed
.venv/bin/pytest tests/agent/test_cards.py -q  →  18 passed
.venv/bin/pytest tests/agent/test_respond.py -q  →  31 passed
```

---

## 未确认

- 全角数字（U+FF10–U+FF19 如 `６２２`）、零宽字符（U+200B）分隔数字、中文数字账号（「六二二二零二」）可绕过掩码——当前均未处理，建议在已知缺口文档中显式列出，不阻塞本次合并。
- `ADR-005-frontend-choice.md` 中「Python Fastify」应为「Python FastAPI」或 Node.js Fastify（typo，非 blocker）。


## 修复轮 2 定点复审（2026-10-25，17204bd..5d1c4de）

**结论: APPROVE**（P0 无，P1 无，P2×2 新发现，均不阻塞）

### 上轮缺陷逐条验证

| 编号 | 缺陷 | 状态 | 证据 |
|---|---|---|---|
| P1-1 | 中文数字提取未实现 | **已修复** | 新增 `_chinese_to_value()`（verify.py:48–119），支持零–九、十百千万亿、缩写（三万五→35000）、十二等 leading-unit 形式。`extract_amounts` 新增 `_CHINESE_AMOUNT_RE` 匹配 `[零〇一二两三四五六七八九十百千万亿]+\s*[元块]`。8 项中文测试通过（八百→80000 分、十二→1200 分、一百零五→10500 分、三十万→3000000 分、两百万→20000000 分、三万五→350000 分、非金额中文→不提取、一千200→后续讨论）。 |
| P1-2 | 多连续分隔符绕过 | **已修复** | `_DIGIT_RUN_RE` 从 `[ -]` 改为 `[ \t\-]+`（支持连续空格/tab/连字符）。新增 `_FULLWIDTH_DIGIT_TABLE`（U+FF10–U+FF19→ASCII）与 `_ZERO_WIDTH_RE`（U+200B–U+200D/U+2060/U+FEFF 删除）。`mask_long_numbers` 先 NFKC 归一化→删零宽字符→全角转 ASCII→再匹配。5 项 bypass 测试通过（双空格/双连字符/tab/全角数字/零宽字符），所有均正确掩码。 |
| P1-3 | 卡片无 5 张上限 | **已修复** | `generate_cards()` 末尾 `return tuple(cards[:5])`（cards.py:329）。`test_card_cap_at_5`（8 reads→5 cards）通过。 |
| P2-1 | `test_with_llm_repair` 断言空洞 | **已修复** | 新增 `"800.00" in result`（修复后值出现）与 `"900.00" not in result`（错误值不出现）。换回旧 verify.py（17204bd）后 `-k test_with_llm` 为 1 passed（只跑旧版弱断言），新版该测试单独运行 1 passed 且断言更严格。 |
| P2-2 | `scrub` 误清 token_usage | **已修复** | `_SENSITIVE_KEYS` frozenset 删除 `"token"`（replay.py:421）。新增 `test_preserves_token_usage`：`completion_tokens`/`prompt_tokens`/`total_tokens` 均保留。 |
| P2-3 | `_AMOUNT_TEXT_RE` 不保护 `.00元` | **已修复** | `[\d,]+` → `[\d,.]+`。新增 `test_p23_decimal_yuan_preserved`（`10000000.00元` 保留不掩码）与 `test_p23_plain_10m_not_protected`（纯数字仍掩码）。 |

### 协调者指定的核验项

#### 1.「两千元五角」部分提取风险评估

**复现**（`/tmp/verify_edge.py`）：

```
两千元五角 → [{'raw': '两千元', 'cents': 200000}]   ← 「五角」丢失（50 分）
两万元五角 → [{'raw': '两万元', 'cents': 2000000}]  ← 「五角」丢失（50 分）
```

**根因**: `_CHINESE_AMOUNT_RE` 模式 `[零〇一二两三四五六七八九十百千万亿]+\s*(元|块)`。中文「角」「分」不在匹配字符集中。当前实现覆盖了「元」「块」两个主单位，子单位「角」「分」属未实现范围（任务书未要求、设计笔记未提及、现有依赖无支持）。不依赖 `cn2an`（协调者裁定：自行实现、不新增依赖）。

**风险评估: P2，不阻塞**。理由：
- 角/分子单位最大误差为 90 分（9 角）= 0.9 元，不具备有意义的金融风险
- 银行金额以分为最小单位，实际交易极少出现「五角」这种子单位口语表述
- L2 是真正的授权关卡，extract 仅用于校验 LLM 回复的事实性（非资金授权）
- 若需修复：在 `_CHINESE_AMOUNT_RE` 增补 `([零一二三四五六七八九]\s*角)?([零一二三四五六七八九]\s*分)?`，但此扩展在当前的 cn2an-less 自实现下需手动构建角/分映射。建议在 P3 做（不阻塞 P2 验收）

#### 2. NFKC 后 span 偏移是否导致掩码替换错位

**验证结论: 安全，无偏移风险。** 代码逻辑：

1. `_AMOUNT_TEXT_RE` / `_AMOUNT_RE` 在 `working`（已 NFKC + 删零宽 + 全角转 ASCII）上检测
2. 检测命中后记录保护区间（`protected_spans`）
3. `_DIGIT_RUN_RE` 在同一个 `working` 上匹配数字 run
4. 替换在同一个 `working` 上执行，保护区间按位置跳过

**所有操作共享同一份 `working` 字符串**——没有跨原始/归一化文本的位置引用，因此 NFKC/全角转换不产生 span 偏移。亲跑验证：全角数字「６２２２０２１２３４５６７８９０」→ 正确掩码为 `****7890`，零宽字符插入「6222​0212​3456​7890」→ 正确掩码为 `****7890`。

#### 3. 中文数字与阿拉伯数字混写边界

**复现**：

```
一千200元 → [{'raw': '200元', 'cents': 20000}]    ← 「一千」（100000 分）被忽略
```

**根因**: `_CHINESE_AMOUNT_RE` 模式 `[零〇一二两三四五六七八九十百千万亿]+\s*(元|块)` 匹配到「一千」后期望 `\s*(元|块)`，但下一字符是 `2`（数字），匹配失败。随后 `_AMOUNT_YUAN_RE` 捕获「200元」→ 20000 分。最终只提取了 200 元而非 1200 元。

**风险评估: P2，不阻塞**。理由：
- 「一千200元」是非自然的中文表述（应写「一千二百元」或「1200元」）
- LLM 在正常生成中产生此类混写的概率极低
- 即使发生，`extract_amounts` 提取的是「事实对比」用的候选值，L2 才是真正的授权关卡
- 修复成本高（需处理中文数字→阿拉伯数字→中文数字的歧义边界），收益低

**建议**: 在 ADR-004 已知缺口中明文记录此边界，P3 考虑引入 `cn2an` 做完整中文数字解析。

### 测试覆盖

```
.venv/bin/pytest tests/agent/test_verify.py   →  70 passed
.venv/bin/pytest tests/agent/test_replay.py   →  47 passed
.venv/bin/pytest tests/agent/test_cards.py    →  19 passed
.venv/bin/pytest tests/agent/test_respond.py  →  31 passed
────────────────────────────────────────────────
  Total                                        167 passed in 0.73s
```

新增中文数字测试 8 项、bypass 修复测试 5 项、小数点元测试 2 项、卡片上限 1 项、token usage 保留 1 项——共计 17 项回归/边界测试，全部通过。

### 回归核验

换回 17204bd 的 `verify.py` 后：
- `-k "chinese or p12 or p23"` → **13 failed / 2 passed**（中文数字 8 + bypass 5 全部失败；小数点元 2 项中 P2-3 修复项失败、原保护项通过）
- 还原 5d1c4de 后 13 failed 全部消除

### 冻结区检查

`packages/`、`services/gateway/`、`docker-compose.yml`、`AGENTS.md`、`PRD.md` 本轮 0 diff。仅改动 `services/agent/app/llm/` 和 `tests/agent/`，在白名单范围内。

### 新发现

无 P0/P1。P2×2：

- **P2-4「两千元五角」丢失「五角」（50 分）** — 角/分子单位不在当前 `_CHINESE_AMOUNT_RE` 匹配范围。最大误差 90 分（0.9 元），不构成金融风险。建议 P3 处理，不阻塞合并。
- **P2-5「一千200元」中阿混写只提取「200元」** — 混写格式 `中文数字 + 阿拉伯数字 + 元` 为非自然中文，LLM 正常生成概率极低。即使发生，仅影响回复的事实性校验（非授权）。建议 ADR-004 记录此已知边界，不阻塞合并。