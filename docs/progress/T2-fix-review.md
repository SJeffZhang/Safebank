# L2 Utterance Coverage 评审修复

**日期**: 2026-10-27
**分支**: feat/l2-utterance-coverage
**评审报告**: docs/reviews/L2-utterance-coverage-review.md (REQUEST_CHANGES)
**结论**: ✅ 全部修复

## 修复内容

### P1-1/P1-2/P2-4: add_payee 名称白名单 + 元语言/动作动词守卫

`parser.py` 新增 `_valid_payee_name()`:
- 白名单：`^[一-鿿·]{1,8}$`（仅 1–8 个汉字或·）
- 元语言拒止：是什么、什么意思、怎么、能不能、可以吗、吗、呢、啥
- 动作动词拒止：转、给、冻结、关闭、开通、打开、开启、关掉、取消、赎回、申购、新增、添加

同时解决了 P2-4（无逗号吞并）：名称超过 8 字符被白名单拒止。

两处 add_payee 匹配点（verb-prefix 与 把-name-verb）均接入守卫。

### P2-1/P2-2/P2-3: 中文数字歧义检测

`_parse_chinese_numeral()` 新增:
- `prev_big_mult`: 千/万 倍数后紧跟单数字 → return None（三千五、两万五）
- 重复十（十十）→ return None
- 正常写法不变：三千五百=3500, 两万五千=25000, 五十五=55

## 测试

新增 J 节 8 条测试（`tests/gateway/w1/test_parser_coverage.py`）：
- 3 条 add_payee 元语言/吞并攻击（均应不授权）
- 3 条中文数字歧义攻击（口语省略、重复单位）
- 1 条完整写法正例（三千五百=350000分）
- 1 条已拒止的 把-句式元语言（把李阿姨加为收款人怎么操作）

### 命名规则
- 红测失败数：5（a443300）
- 修复后：8 passed/51 deselected

## 最终门禁

```
pytest tests/gateway/w1 -q: 1588 passed, 159 skipped (0 failed)
make test-w1:              1747 passed in 51.69s
make lint:                 All checks passed, 389 files formatted
```

## 提交

```
36a92ae fix(L2): P1/P2 utterance parser fixes
a443300 test(L2): add P1/P2 attack regression tests (RED, 5 failures)
```