# T2a 转账类原话解析覆盖 — 完成

**提交**: a79be4b（分支 `codex/l2-utterance-coverage`）

## 改动

| 文件 | 改动 |
|---|---|
| `services/gateway/app/utterance/parser.py` | +76/−11：新增 `_parse_chinese_numeral()`、扩展 MONEY 和 `money_cents` 支持中文数字金额、「把 <money> 转给 <payee>」句式 |
| `tests/gateway/w1/test_parser_coverage.py` | −3（仅去掉 3 条 A 类 xfail 标记） |

## 转绿的 3 条 xfail

| 测试 | 输入 | 关键 |
|---|---|---|
| `test_transfer_bang_wo_ba_amount_zhuan_gei_payee` | 帮我把1000转给王五 | 新增「把 <金额> 转给 <收款人>」模式 |
| `test_transfer_gei_X_zhuan_chinese_numeral_yuan` | 给王五转三千元 | 中文数字「三千」→ 300000 分 |
| `test_transfer_zhuan_chinese_numeral_kuai_gei_payee` | 转五百块给赵六 | 中文数字「五百」→ 50000 分 |

## 实现要点

- **中文数字**：仅接受无歧义形式（三千、五百、一千二百、两万五千、三千零五十）；
  拒绝含「零」（零一）、裸乘数（百、千）、中阿混写（3千）、口语省略（两万五）。
- 「把」模式放在 `_transfer_clause` 内，不影响 `_clause` 内其他工具的模式匹配。
- 所有攻击回归测试保持通过（否定、疑问、条件、引号、注入、元语言、历史、边界中文金额）。

## 覆盖测试结果

```
tests/gateway/w1/test_parser_coverage.py: 35 passed, 6 xfailed, 0 failed
```

## 已知问题

- 3 条 `test_r2_explicit_money_units_are_exact` 失败为预存（十进制金额单位，与本改动无关）。
- 6 条 xfailed 为 B/C 类（卡渠道、新增收款人），待 T2b/T2c 实现。