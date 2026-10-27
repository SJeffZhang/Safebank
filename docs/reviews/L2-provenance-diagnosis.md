# L2 Provenance 诊断：UNKNOWN_SENSITIVE_ARG 原因分析

**日期**: 2026-10-27
**类型**: 只读诊断，不改代码
**实证来源**: 真实 DeepSeek live 运行，G3 已 SCOPED

---

## 现象

| 用例 | 原话 | 工具/参数 | 来源 | 结果 |
|------|------|-----------|------|------|
| A | 给王五转三千元 | transfer.amount_cents=300000 | utterance span [4,6]「三千」 | UNKNOWN_SENSITIVE_ARG |
| A' | 给王五转3000元 | transfer.amount_cents=300000 | utterance span「3000」 | 通过 |
| B | 关掉我卡的线上支付 | update_card_controls.online=false | utterance span [5,7]「线上」 | UNKNOWN_SENSITIVE_ARG |
| C | 新增收款人李阿姨，账号6222020000004444，他行 | add_payee.bank_name="他行" | utterance 来源 | INTENT_SCOPE_EXCEEDED |

---

## ① 用例 A：中文数字金额校验

### 发现

**G3 parser** (`utterance/parser.py:177-195`) 的 `money_cents()` 能正确解析中文数字：

- `_MONEY_PATTERN` 的 `_CN_NUM_VALUE` = `\d+(?:\.\d{1,2})?|[零〇一二两三四五六七八九十百千万]+`
- `_parse_chinese_numeral("三千")` → 3000（元）→ ×100 = 300000（分）
- 即 G3 既认识 `"3000"` 也认识 `"三千"`，都能正确算出 300000 分

**Provenance** (`taint/text.py:22-33`) 的 `matches()` 对 `_cents` 参数**只认阿拉伯数字**：

```python
# taint/text.py:28
if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", raw):
    return False
```

- `text[start:end]` = `"三千"` → normalize → `"三千"` → 去掉币种前缀/单位后缀 → 仍是 `"三千"`
- `fullmatch(r"[0-9]+...", "三千")` → **False** → 返回 False → trust=unknown → `UNKNOWN_SENSITIVE_ARG`

`"3000"`（阿拉伯数字）则顺利通过 `fullmatch` → 100× → == 300000 → trust=user。

### 结论

**根因**：G3 parser 与 provenance 的金额解析能力不一致。G3 的 `money_cents()` 是以完整金额语法（含中文数字）解析子句的，但 provenance 的 `matches()` 对 `_cents` 参数只做阿拉伯数字纯数学转换。当 L1 把中文数字金额的 span 声明为 utterance 来源时，provenance 无法验证，判为 UNKNOWN_SENSITIVE_ARG。

这**不是**安全策略选择——两处代码都在同一代码库中、都是确定性函数。不一致纯粹是实现遗漏。

### 最小修复建议

**provenance 复用 parser.money_cents**（不改 L1、不放宽授权）：

在 `taint/text.py:matches()` 的 `_cents` 分支中，当阿拉伯数字正则不匹配时，fallback 到 `money_cents()`：

```python
if param.endswith("_cents"):
    raw = re.sub(r"^(人民币|CNY|RMB|¥|￥)", "", raw, flags=re.I)
    raw = re.sub(r"(元|块钱|块)$", "", raw)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", raw):
        # Fallback: reuse G3 parser's Chinese numeral support
        from ..utterance.parser import money_cents as parse_money
        parsed = parse_money(text)  # use original text (with 元 suffix if present)
        return parsed is not None and parsed == value
    ...
```

**为什么不放宽授权**：
1. `money_cents()` 是确定性函数（无 LLM），结果可重现
2. 校验条件不变：span 文本解析出的金额必须 **精确等于** L1 声明的参数值
3. G3 已经接受中文数字金额（`_CN_NUM_VALUE`），provenance 只是追认这个事实
4. 攻击者不能通过中文数字绕过——金额仍然被解析并严格比对，不会「三千」被解释为其他数值

---

## ② 用例 B：布尔渠道参数来源规则

### 发现

**Provenance** (`taint/text.py:34-39`) 对布尔值的匹配：

```python
if isinstance(value, bool):
    return raw in (
        {"true", "是", "开启", "打开", "补卡", "包括自己"}
        if value
        else {"false", "否", "关闭", "不补卡", "不包括自己"}
    )
```

`"线上"`（channel name）不在 `{"false", "否", "关闭", "不补卡", "不包括自己"}` 中 → False → trust=unknown。

**但是**，即使 L1 把 span 选在动词上（`"关掉"` [0,2]），依然不匹配——`"关掉"` 也不在 False 集中（只有 `"关闭"` 在）。

**对比 G3 parser** (`utterance/parser.py:330-365`)：从 `LEXICON["actions"]["update_card_controls"]` 加载动词表来匹配控制子句，包括 `关掉`、`关闭`、`打开`、`开启`、`开通` 等。Provenance 的布尔匹配集只硬编码了部分动词（`"开启"/"打开"/"开通"` 在 True 集，`"关闭"` 在 False 集），但 **`"关掉"` 两者都不在**。

### 结论

**双重缺陷**：
1. **L1 错选了 span**：L1 把 `online=false` 的来源 span 指在渠道名 `"线上"`（[5,7]）上，而不是动词 `"关掉"`（[0,2]）。渠道名本身不表示布尔值——它是被操作的**对象**，动词才是**操作方向**的来源。
2. **Provenance 布尔匹配集不完整**：即使 L1 修了 span，`"关掉"` 也不在 False 匹配集中。G3 parser 通过 `intent_lexicon.yaml` 的 `actions.update_card_controls` 维护了完整动词表，provenance 却维护了另一套不完整的硬编码集合。

### 应改 L1 还是 L2

**两边都改**：

| 改动 | 理由 |
|------|------|
| **L1**：布尔渠道参数的来源 span 应指向**动词**（关掉/开通），非渠道名（线上/境外） | 这是来源语义正确性问题：布尔值的「真假」由动词传达，渠道名只传达「哪个渠道」。L1 模型需要理解这种映射 |
| **L2 provenance**：布尔匹配集应与 G3 parser 的 action 动词表一致，或直接从 `intent_lexicon.yaml` 读取 | provenance 不应维护第二套不完整动词表；「关掉」和「关闭」在 G3 中语义等价，provenance 应同样认可 |

**如果只改一边**：
- 只改 L2（加 `"关掉"` 到 False 集）：L1 仍然可能选错 span（指渠道名），且渠道名 `"线上"` 既不表示 true 也不表示 false——provenance 不应强行认渠道名为布尔来源
- 只改 L1（指向动词）：即使 L1 选对了 `"关掉"`，provenance 仍不认

**建议**：优先改 L2（补全 provenance 动词集，与 intent_lexicon.yaml 同步），然后改 L1（修正 span 指向）。L2 的改动是确定性安全增强（不依赖模型行为）；L1 的改动是提示/训练优化（让模型理解 span 语义）。

---

## ③ 用例 C：bank_name 不在 G3 scope

### 发现

**G3 parser** (`utterance/parser.py:549-568`) 正确解析了 `_payee_attr` 子句：
- `"他行"` → `Command("_payee_attr", fields={"bank_name": "他行"})`
- 与 `add_payee` 主句合并后，`payee_main.fields = {"account_no": "...", "bank_name": "他行"}`

**但 add_payee 工具处理**（`parser.py:782-792`）只把 `payee_name` 和 `account_no` 放进 scope fields：

```python
elif tool == "add_payee":
    name = (command.target or "").strip()
    put("payee_name", name)
    account_no = command.fields.get("account_no")
    if account_no:
        put("account_no", account_no)
    # ← bank_name 未 put 进 scope fields！
    kinds.append("PAYEE_ADD")
```

所以 G3 返回的 scope 是 `{payee_name: "李阿姨", account_no: "6222020000004444"}`，**不含 `bank_name`**。

L1 把 `bank_name` 声明为 utterance 来源后，意图范围阶段（⑤）检测到 `bank_name` 不在 scope 中，判 `INTENT_SCOPE_EXCEEDED`。

### 结论

**根因**：G3 parser 的 `_payee_attr` 能解析 `"他行"/"本行"` 为 `bank_name`，但 add_payee 工具处理器没有把这个字段放入 scope。这是 **G3 parser 的遗漏**，不是 L1 的错——`"他行"` 确实来自用户原话，只是 G3 scope 没收进去。

`"他行"` 应该映射为 `bank_name` 字段（parser 已经做了这个映射，只是没放进 scope）。

### 修复

**只改 L2 G3 parser**（`parser.py`，add_payee 工具处理块，约第 791 行后）：

```python
bank_name = command.fields.get("bank_name")
if bank_name:
    if not put("bank_name", bank_name):
        return Parsed()
```

这样 scope 就包含了 `bank_name`，provenance 的 utterance 来源校验就能匹配到 scope 中的字段。

L1 不需要改——它正确地识别了 `"他行"` 是 `bank_name` 的来源，是 G3 没把这个字段放进 scope。

---

## ④ parse("新增收款人") 复现

### 发现

逐行追踪 `parse("新增收款人", state, customer_id)`（`parser.py:528-816`）：

1. `_clause("新增收款人")`：匹配 `add_payee` 动词模式 `rf"({add_verb})(?P<name>.+)"` → `Command("add_payee", target="收款人")`
2. `_valid_payee_name("收款人")`：
   - `_PAYEE_NAME_RE.fullmatch("收款人")` → True（3 CJK 字符，符合 `^[一-鿿·]{1,8}$`）
   - `_PAYEE_NAME_META_RE.search("收款人")` → False（不含 "吗"/"呢"/"啥"/"吧"/"嘛" 等）
   - `_PAYEE_NAME_ACTION_RE.search("收款人")` → False（"收" 不在 `转|给|冻结|关闭|开通|打开|开启|关掉|取消|赎回|申购|新增|添加` 中）
3. → `_valid_payee_name` 返回 **True**，允许 name="收款人"
4. add_payee tool 块：`put("payee_name", "收款人")`
5. `scope_is_complete`：PAYEE_ADD 只要求 `payee_name` 存在 → True
6. → `status = ParseStatus.SCOPED`，scope.fields = `{payee_name: "收款人"}`

**结论**：`parse("新增收款人")` 返回 **SCOPED** 且 `payee_name="收款人"`。

但「收款人」是类别词（category word，意为『收款人/收款对象』），不是人名。G3 parser 的 `_PAYEE_NAME_ACTION_RE` 没有覆盖「收」字（它只在 `_PAYEE_NAME_META_RE` 中检查了语气词）。修复方式：将 `"收"` 加入 `_PAYEE_NAME_ACTION_RE`，或用与 `_PAYEE_NAME_META_RE` 类似的思路新增类别词黑名单（含「收款人」「付款人」「自己」「他人」等）。

**注意**：这与刚合入的 PR #41（`feat/l2-utterance-coverage`，commit bb7724b）修复 `吧/嘛` 问题属同一模式——都是 `_valid_payee_name` 的假阴性（把非人名判为合法名字）。

### 复现方式

```python
from services.gateway.app.utterance.parser import parse
# 需要一个 minimal BankState with at least one CHECKING account for the customer
# 预期: result.status == ParseStatus.SCOPED and result.scope.fields["payee_name"] == "收款人"
```

---

## 总结

| 用例 | 根因 | 应改 | 优先级 |
|------|------|------|--------|
| **A** | provenance `_cents` 只认阿拉伯数字，G3 的 `money_cents()` 具备中文解析能力 | **L2 provenance**：复用 `money_cents()` | P1（每次中文金额都失败） |
| **B** | ①L1 span 指渠道名非动词 ②provenance 布尔动词集不完整 | **优先 L2**：provenance 动词集从 lexicon 读取；**同时 L1**：span 指向动词 | P1（渠道操作全通不过） |
| **C** | G3 add_payee 工具处理没把 `bank_name` 放进 scope | **L2 G3 parser**：加 `put("bank_name", ...)` | P1（含「他行/本行」的 add_payee 均超范围） |
| **④** | `_valid_payee_name` 未拒类别词「收款人」 | **L2 parser**：`_PAYEE_NAME_ACTION_RE` 加 `"收"` 或新增类别词黑名单 | P2（可被 exploit 但需 L1 配合产出此计划） |

四个问题的共同特征：**G3 parser 的解析能力不等于 provenance/scope 的校验能力**。parser 能正确理解的内容（中文金额、「关掉」动词、「他行」属性、「收款人」是类别词），在下游的 provenance 校验或 scope 收集中被遗漏或使用不完整的信息。修复方向是 **使下游校验与 parser 能力对齐**，而非削弱校验。