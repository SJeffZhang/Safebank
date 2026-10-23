# W2a 真实 W1 接线与 memo 裸¥ P2 独立只读评审

日期：2026-10-24。范围：`24a6fdb..adeb4b5`（feat/l2-provenance，HEAD adeb4b5）。只读评审，唯一写入本报告。亲跑 `tests/gateway/w2/test_memo_sensitive.py tests/gateway/w2/test_provenance.py` = 106 passed（0.23s）。DB 专项（test_real_w1_integration.py / test_read_ledger.py）依赖 docker PG，本 worker docker 命令被拒，未运行；结论基于源码静态核对 + 协调者六退出亲跑记录。

## 逐项核对

1. **删除 fixture_revocation → 真实 runtime** ✓：`tests/gateway/w2/db/test_read_ledger.py` 重写为用 `tests.gateway.w1.db.support.harness`（真实 `GatewayRuntime`、真实 PIN 哈希、app+assistant JWT、真实 `/auth/login`→`/sessions`→`/sessions/{sid}/utterances`），不再 monkeypatch `check_revocation`。logout 后同请求断言 401 `UNAUTHENTICATED`，走真实 `check_revocation`（tokens.py:241，接入 api/auth.py:74、api/sessions.py:81、utterance/runtime.py:49）。grep 确认新测试无 `monkeypatch.setattr(check_revocation)`/`fixture_revocation`。✓
2. **G3→loader→②③④ 来源链** ✓：`test_real_w1_integration.py` 8 项真实 PG，经真实 `load_for_plan`（loader.py:33，内部 `session_for` 查撤销/跨 sid/customer）构造 authorization，再跑 Entity/Provenance/Intent。覆盖 typed 金额/目标、同名澄清 G19 action+G3 合槽（root 唯一 claim、action/parent 仅证据）、hint 采纳/未采纳/错误 used_by、parent span 追溯、跨 sid/customer 404、改口 `AUTHORIZATION_CONSUMED`+`SUPERSEDED`、logout 401、失效 parent 阻断 intent。✓
3. **裸¥ P2 修复** ✓：`_MONEY_PATTERN`（provenance.py:28–31）`[¥￥]` → `[¥￥](?=[0-9])|[0-9][¥￥]`，裸符号不升黄、邻近数字仍升黄；新反例 6×3、正例补 `报销1¥`/`报销￥1`，106 passed 佐证。真实金额/8 位数字路径（`numbers()`、元/人民币/CNY/RMB 前后缀）未动。DENY/UNKNOWN 本身未被削弱（provenance.py:206–211 仍分别加 TAINTED/UNKNOWN）。**但见 P1**。
4. **default reserve 仍 None** ✓：provenance.py:89 `reserve: Callable | None = None`，:254 `if self.reserve is None: raise DEPENDENCY_UNAVAILABLE`，无提前调用；进度文明确 W2b 未完成。✓

## 发现

P0：无。

P1 — `trust == "user"` 门控越界且减少已锁信息，理由不成立
- 证据：`provenance.py:218` 新增 `and trust == "user"`，使 `MEMO_SENSITIVE_CONTENT/RISK_YELLOW` 只对 user 来源追加；`test_memo_sensitive.py` 相应把 `test_sensitive_read_text_keeps_source_deny_alongside_yellow`、`test_sensitive_text_without_valid_user_source_keeps_replan` 的「黄 finding 并存」断言改为 `assert not any(MEMO_SENSITIVE_CONTENT)`。
- 「黄色覆盖 DENY」的解释不成立：`pipeline/decision.py:10–14` 是空占位 `return None`（W3 未实现，无任何聚合把 findings 映射为最终风险级）；`context.py:200–201 has_hard_deny()` 只查「存在 DENY」；`pipeline/__init__.py:62–63` 在 DENY 时短路 snapshot/preflight/workflow，decision/audit 恒运行。findings 是平铺列表，RISK_YELLOW 与 DENY/REPLAN 只会共存，**不存在黄覆盖 DENY 的机制**。
- 越界/减少已锁信息：本次仅批准裸¥ P2（正则即可单独完成）；此前已 APPROVE 且锁定的语义是「warning 只追加、与 DENY/REPLAN 共存、不替换不降级」（docs/reviews/L2-W2-memo-review.md:7）。门控把 untrusted/unknown 来源的黄 finding 一并移除，偏离 PRD §7.5:1040 / §9.8:1515「含 ≥8 位数字或金额格式时升黄」（无 trust 条件）。任务书 W2-real-integration.md:14「来源DENY/UNKNOWN不被黄finding覆盖」按协调者口径应解为「黄不覆盖 DENY/REPLAN 的决策优先级」，而非「黄不得与 DENY/UNKNOWN 并存」。
- 修复：撤销 `and trust == "user"`，恢复两处「并存」断言（保留裸¥正则修复）；若确需门控，须协调者明确批准并同步 PRD §7.5 与任务书措辞，不得静默改锁语义。

P2 — `test_real_w1_integration.py:6` docstring「P1 only implements five read tools and no real write backend」事实错误：P1 已实现 7/14 写工具（transfer、add_payee、update_card_limits、update_card_controls、freeze_card、unfreeze_card、report_card_lost，见 policies/tools.yaml）。应改为「P1 仅实现子集（5/18 读、7/14 写），其余读写工具未实现」并说明此处用契约 FakeL3 是因为 ②③④ 只消费 snapshot，而非「无写后端」。

## 未确认

- 本 worker 无法运行 docker PG，故 8 项真实 W1 联测与重写后的 G2 DB 测试未亲跑（协调者六退出亲跑中）；其正确性依据静态核对测试构造与生产路径（harness/load_for_plan/check_revocation）一致，不据为已亲验。
- 任务书 W2-real-integration.md:14 的「不被黄finding覆盖」原文确属两可，若协调者最终裁定保留门控语义，须同步 PRD §7.5；本报告按「越界、应回退」给出结论。

结论：**REQUEST_CHANGES**。P0 无；P1 一项（trust 门控越界+理由不成立，需回退或显式批准+PRD 同步）；P2 一项（docstring 事实措辞）。

## 修复轮 1 定点复核（adeb4b5..118603f）

- **P1 关闭** ✓：`provenance.py:218` 已删除 `and trust == "user"`，memo/alias 黄级恢复为只要 `numbers()` 或 `_MONEY_PATTERN` 命中即追加，与 DENY/REPLAN 平铺并存（不覆盖、不降级）。两处并存断言已恢复原测试名 `..._alongside_yellow` / `test_sensitive_text_without_valid_user_source_keeps_replan` 并重新断言 `MEMO_SENSITIVE_CONTENT/RISK_YELLOW` 存在。亲跑 `test_memo_sensitive.py + test_provenance.py` = **106 passed**（0.25s）。
- **P2 关闭** ✓：`test_real_w1_integration.py` docstring 更正为「P1 implements only a 5/18 read and 7/14 write backend subset…do not claim full L3 integration」。
- **裸¥ 正则保持** ✓：`_MONEY_PATTERN` 仍为 `[¥￥](?=[0-9])|[0-9][¥￥]`（provenance.py:31），裸符号不升黄、邻近数字仍升黄。
- **未越界** ✓：`git diff --stat` 仅 4 文件（docs/progress/W2-provenance.md、provenance.py −1 行、test_real_w1_integration.py docstring、test_memo_sensitive.py 断言）；`decision.py`/`context.py`/`PRD.md`/`pipeline/__init__.py` 无 diff，DENY 聚合优先级与阈值未改；`reserve` 默认仍 None（W2b 未做）。
- 未运行：DB 专项（docker 被本 worker 拒），由协调者六退出亲跑覆盖。

结论：**APPROVE**。原 P1/P2 均已关闭，无新增 P0/P1/P2。
