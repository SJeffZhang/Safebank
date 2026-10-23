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
