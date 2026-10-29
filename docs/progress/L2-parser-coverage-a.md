# L2-parser-coverage-a

**任务**：修复 gateway G3 原话解析器，使 4 条此前被判 `INTENT_UNCLEAR`（→ FORM_REQUIRED）的写请求产出正确的 SCOPED scope。

**分支**：`feat/l2-parser-coverage`（worktree GW-parser）

## 结果

| 原话 | 预期 scope | 解析器改动 |
|---|---|---|
| 新增收款人孙九，账号6222020000007777，开户行他行 | PAYEE_ADD，name=孙九, bank_name=他行, account_no 逐字取自文本 | 是：`开户行(是\|为)?他行\|本行` 补全 `_payee_attr` |
| 我的卡丢了，帮我挂失 | CARD_LOST，card_id=crd_zs | 是：新增 `(我的?卡\|我的银行卡)(丢了\|丢失了\|丢失\|丢)` → report_card_lost |
| 取消乐听音乐的订阅 | MANDATE_CANCEL，instruction_ids=["si_music"] | **否**：已有 `取消X的订阅` 模式，只需 state 含该商户订阅（测试夹具补 `si_music`/merchant_name=乐听音乐） |
| 取消给赵六的定时转账 | SCHEDULE_CANCEL，instruction_id=si_zl | 是：新增 `取消给X(的)?定时转账` → cancel_scheduled |

## 安全性（未放宽）

- 账号 `6222020000007777` 逐字取自文本，从不编造。
- 未知商户/收款人（`取消不存在的商户的订阅`、`取消给王二的定时转账`）仍非 SCOPED。
- 否定（`我的卡没丢`）、疑问（`取消…吗？`）、引号（`他说“取消…”`）均不产生 scope——共 6 条攻击回归用例。

## 测试

新增 `tests/gateway/w1/test_parser_coverage_a.py`：4 条正向 + 6 条攻击回归（parametrize）。

红测阶段（提交 9543f90）：4 failed / 7 passed——4 条正向中 3 类模式失败，`取消乐听音乐的订阅` 及全部攻击回归当时已通过。

修复后（提交 77870b4）：11 passed。

## 门禁（全部通过，实际运行）

- `make lint`：All checks passed；`ruff format --check` 418 files already formatted
- `make test`：3266 passed, 879 skipped
- `make test-w1`：1772 passed
- `make test-w2`：256 passed

## 变更文件

- `services/gateway/app/utterance/parser.py`（+9/−2，三处 `_clause` 模式）
- `tests/gateway/w1/test_parser_coverage_a.py`（新增，145 行）

## 提交

- 9543f90 红测
- 77870b4 解析器修复
- 66d950c import 排序（ruff I001）

未 push。
