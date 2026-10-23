# W1 修复轮1实现说明

此为实现者的修复记录，不代替独立评审批准。基线 d9a1b56，未推送。

| 原评审项 | 根因修复 | 主要回归 |
|---|---|---|
| P1-1 | 完整分句命令语法；未知分句整体FORM_REQUIRED | test_review_r1_parser.py::test_r1_each_whole_clause_must_be_a_supported_command；原七幕test_parser.py |
| P1-2 | 完整金额槽和单位倍率；整数分精确换算 | test_r2_explicit_money_units_are_exact / test_r2_unsupported_or_fractional_cents_never_authorize |
| P1-3 | 完整来源槽；仅省略才默认唯一CHECKING | test_r3_explicit_source_variants_never_default_to_checking / test_r3_unknown_explicit_source_cannot_be_defaulted |
| P1-4 | 完整目标NFKC相等；精确唯一优先近音近形 | test_r4_full_target_slot_is_not_a_name_substring；原精确/同音/NFKC表驱动 |
| P1-5 | 多转账任一歧义整体表单；禁止共享槽覆盖 | test_r5_multiple_transfer_slots_with_ambiguity_require_complete_form；真实PGtest_r5_multi_target_ambiguity_cannot_be_promoted_to_authority |
| P1-6 | OPEN SLOTS非空共同守卫；合槽/G3/loader重检完整性 | 空G19、已持久化空action的G3、覆盖transfer/缺金额/缺执行时刻回归 |
| P1-7 | 先作废旧scope/UI，再解析；执行真实状态/claims保留 | 真实PG改口+旧JWT loader/G20、查询不撤销、L3不可用回归 |

红绿过程与精确命令结果见 `docs/progress/W1.md`。最终HEAD见交付消息；提交前五条退出结果如下。

共享运行接线/状态/W4锁与真实L3数据仍按旧进度登记为待联调，不归本轮七项W1缺陷的修复证明范围。


| 最终退出命令 | 结果（退出码均0） |
|---|---|
| `make lint` | check/format通过，165 files |
| `make test-gateway` | 1555 passed / 23 skipped |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db` | 17 passed，真实PG |
| `make test` | 1674 passed / 71 skipped |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-w1` | 1514 passed / 0 skipped（含23真实PG；本轮新增12 PG） |

七项实现修复与回归已完成。未声称共享替身已移除、真实W4锁已联调或真实L3全链路已验收；等待协调者复跑和原评审定点复审。本轮没有修改scope_ids/source.used_by接口，仍由ACTIVE utterance IDs标识claim，source.used_by保存采纳的utterance ID。


## 修复轮2：第7项改口分隔符边界（基线cc32e55）

完整读取根评审追加结论：1–6已解决，7因“不对”仅接受逗号边界仍未解决。本轮仅修parser的改口前缀与相关测试；未合main、未修改来源接口/共享实现。

修复方式：`_correction_body`用一个锚定前缀匹配返回新命令正文，`is_correction`与`_command_text`共用它；NFKC后支持“不对+逗号/句号/分号/换行/空格+改成/改为/换成”。保留原逗号/单独“不对”及直接改成/改为/换成语义；元语言问句排除仍生效。API/service既有“先撤旧授权再尝试新解析”和L3故障降级因此使用同一边界，无法解析的中文金额不给新授权。

红绿命令（PATH全值为`/Applications/Docker.app/Contents/Resources/bin:$PATH`）：

- `.venv/bin/pytest tests/gateway/w1/test_review_r1_parser.py -q -k correction_boundary --tb=short`：修前12 failed / 3 passed / 45 deselected。失败均是新分隔符未被识别。
- `PATH=... GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w1/db/test_review_r1.py -q -k r7 --tb=short`：修前24 failed / 14 passed / 5 deselected；12项L3可用时旧loader仍接受，另12项L3不可用返回503。修后38 passed / 5 deselected in 15.09s。
- `.venv/bin/pytest tests/gateway/w1/test_review_r1_parser.py tests/gateway/w1/test_parser.py -q --tb=short`：修后100 passed in 0.39s；覆盖识别与剥离一致，原七幕语法回归保持通过。

真实PG参数化为5种分隔符×3种替换词×L3可用/故障，共30项，逐项核验：旧utterance SUPERSEDED、旧JWT loader拒绝、G20无旧、新utterance INTENT_UNCLEAR且空action_kinds/FORM_REQUIRED、未执行计划/审批SUPERSEDED、EXECUTING/UNKNOWN及CLAIMED保留。另7项普通查询/不对吗/元语言/引用不误撤。PG仍使用已披露W4锁和pending状态替身、FakeL3，不冒称生产依赖或真实资金链已联调。

最终五退出结果见下方。本轮结束后冻结工作树供协调者复跑和原评审定点复审。


修复轮2最终退出检查（代码稳定后运行，全部退出码0）：

| 实际命令 | 实际输出 |
|---|---|
| `make lint` | All checks passed；165 files already formatted |
| `make test-gateway` | 1570 passed, 54 skipped in 1.88s |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db` | 17 passed in 3.51s |
| `make test` | 1689 passed, 102 skipped in 3.02s |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-w1` | 1560 passed in 20.43s；0 skipped，含54真实PG |

普通目标的skip包含W1 PG默认关闭；`test-w1`明确开启并实际收集54项（旧11、本轮评审文件43），不以skip作通过证据。相比轮1新增15纯函数案例、净增31 PG案例。`git diff --check`通过；本轮仅parser、两个原W1回归文件、两份授权文档共5文件，无共享/依赖变更，无main合并。最终SHA见交付消息，待协调者亲自复跑及cc32e55..本轮HEAD定点复审；提交后停止改动。
