# L1 card last4 (尾号) 修复

**日期**：2026-10-29
**分支**：`feat/l1-card-last4`

## 问题（live 复现）

解冻 `crd_zs_1`（真实 last4 = "1234"）后，L1 回复「已为您解冻尾号 zs_1 的卡」——LLM 把卡 ID 的尾部当成了卡号尾号。

## 根因与修复

1. `services/agent/app/llm/respond.py`：EXECUTED 回复提示只给了原话与执行步骤摘要，没有真实的 last4，模型只能自己猜。修复：从 `state["reads"]` 的 get_cards 数据递归提取 `card_id → last4`（`_card_facts`），在 EXECUTED 提示中新增「card facts」段；`_EXECUTED_SYSTEM` 增加规则：只按给定尾号（如「尾号1234」）称呼卡，禁止打印卡 ID、禁止编造尾号，未知尾号时称「该卡」。无新增 LLM 调用。
2. `services/agent/app/llm/verify.py`：`_CARD_LAST4_RE` 由 `尾号\s*(\d{4})` 扩展为 `尾号\s*([A-Za-z0-9_]+)`；非恰好 4 位数字的 token（`尾号 zs_1`、`尾号crd_zs_1`、`尾号12345`、`尾号 ab12`）提取为 `CARD_LAST4` 且 `last4=None`，`check_against_facts` 给出 reason `card_tail_malformed`。
3. `verify.py` `OutputVerifier.__call__`：修复尝试后（或 llm 为 None）仍未校验通过的卡尾号短语，确定性替换为「该卡」（`_scrub_unverified_card_tails`），不再原样返回草稿。金额/百分比行为不变。

## 测试

- 新增 `tests/agent/test_respond_cards.py`：`_card_facts` 映射（`id` 与 `card_id` 两种键、无卡为空）、EXECUTED 提示含 `尾号1234`。
- `tests/agent/test_verify.py` 新增：4 条畸形尾号提取（last4=None）、`card_tail_malformed` reason、无 LLM 下「尾号 zs_1」被替换为「该卡」且不泄露 `zs_1`、合法「尾号1234」保持不变。

## 红→绿证据

- 红：实现前 `tests/agent/test_verify.py` 6 failed / 72 passed；`test_respond_cards.py` 因 `_build_executed_user_prompt`/`_card_facts` 不存在 ImportError。
- 绿：实现后 `pytest tests/agent -q -k "verify or respond"` → **147 passed**；`pytest tests/agent -q` → **508 passed, 38 skipped**；`make lint` 通过。
