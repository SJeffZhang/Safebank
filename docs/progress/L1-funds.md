# L1-funds：理财场景 L1 行为

分支 `feat/l1-funds`（worktree `L1-funds`）。L3 的 subscribe_product / redeem_product /
get_holdings 并行实现中；契约已定义，L1 从契约生成工具。

## 交付内容

1. **plan.py 提示词与本地校验**
   - `_SYSTEM` 增加理财规则：`subscribe_product.product_id` 必须 read 来源
     （list_products/get_product）；account 为本人 CHECKING；amount 来自原话或
     `available_less_7d_charges` 派生模板；适当性由网关判定，L1 不决定。
   - `redeem_product.units` 必须来自 get_holdings。
   - 产品问题只读（list_products / compare_products / get_product）。
   - X-3 澄清路由：`_wealth_product_clarify` 在 subscribe_product + 当前原话含「稳健型」
     且已读 list_products 中稳健/平衡产品多于一个时，抛 `ExhaustedClarify(["product_id"],
     ["理财产品"])`，不替用户挑产品。
   - `_wealth_source_problems`：本地可验证的来源规则（product_id 必须 read 且指向
     list_products/get_product；amount 只能原话或 available_less_7d_charges；units 必须
     read 且指向 get_holdings），并入 `all_problems` 与 ExhaustedClarify 门禁。

2. **gather.py 提示词**
   - 增加理财读取规则（list_products/get_product 取 product_id、get_accounts 找唯一
     CHECKING、get_holdings 取真实份额、X-3 读 upcoming_charges + list_products +
     get_risk_profile）。

3. **cards.py 映射补全**
   - 新增 `_SKIPPED_READ_TOOLS`：list_payees / list_scheduled / get_split_status /
     get_risk_questionnaire / get_risk_profile / get_profile 无合适卡片类型，显式跳过
     （数据经助手回复呈现，不进卡片）。
   - `_READ_TOOL_CARD_TYPE`（12）+ `_SKIPPED_READ_TOOLS`（6）= READ_ARGS（18）。

## 测试

- `tests/agent/test_funds_prompt.py`（新增，5 项）：plan/gather 提示词含理财规则；
  read 来源 product_id 通过；编造 redeem units 被拒；X-3 多个稳健产品走澄清。
- `tests/agent/test_card_map.py`：新增 `test_every_read_tool_is_mapped_or_skipped`。

## 门禁结果（2026-10-29）

| 命令 | 结果 |
|---|---|
| `make lint` | All checks passed；438 files formatted |
| `make test-cards` | 19 passed |
| `.venv/bin/pytest tests/agent -q` | 557 passed, 38 skipped |
| `make test` | 3413 passed, 916 skipped |

## 未覆盖 / 边界

- 无真实 LLM、无真实 PG、无真实 L2/L3；全部为 fake LLM 与捕获提示词/本地校验测试。
- `available_less_7d_charges` 派生模板的 X-3 金额来源仅做规则断言，未做端到端
  （L3 该模板尚未就绪）。
