# L1 默认付款账户 + REPLAN-user 回复修复

分支：`fix/l1-default-account`（自 main fork）

## 背景（live 测试发现的两个缺陷）

1. **付款账户猜测**：原话「给王五转100元」未指定付款账户时，L1 的
   `PlanAdapter` 直接把模型返回的 `from_account_id`（有时是储蓄账户
   `acc_zs_sav`）写入计划。按 PRD §7.6：原话未指定源账户时，源账户应为
   客户的**唯一活期账户（CHECKING）**，否则由 L2 判 INTENT_SCOPE_EXCEEDED。
2. **半句回复**：gateway 返回 REPLAN 且 resolver=user 时，
   `RespondAdapter._respond_replan_user` 在 LLM 返回空文本时回退到固定模板
   `respond_template("REPLAN_USER")`（只有「需要您确认以下信息：」，冒号后
   空无一物）。

## 修复

### plan.py — 默认活期账户

- `_SYSTEM` 第 46–48 行改为：付款账户未指定时选唯一活期账户（type=CHECKING）
  并以 read 来源引用；用户显式指定（如「从储蓄账户」）时保留。
- 新增 `_DEFAULT_SOURCE_TOOLS = {transfer, schedule_transfer}`、
  `_NAMED_SOURCE_KEYWORDS = (储蓄, 活期, 支票, 定期)`、
  `_user_named_source_account`、`_checking_accounts`、
  `_default_source_account_overrides`。
- `__call__` 中在 `PlanBody.model_validate` 之后、`_to_draft` 之前调用
  `_default_source_account_overrides`：
  - 非 transfer/schedule_transfer 或用户已命名源账户 → 不改；
  - 恰好一个 CHECKING → 覆盖 `from_account_id` 为读到的 CHECKING id，并加
    `ArgSource(read, ref=<source_ref>, pointer=/accounts/N/id)`；
  - 多个 CHECKING → `ExhaustedClarify(["from_account_id"], ["付款账户"])`；
  - 零个 CHECKING → 不改（交给 L2 判定）。
- `_PARAM_LABELS` 增加 `from_account_id: 付款账户`。

### respond.py — 确定性 REPLAN-user 回复

- 新增 `_REPLAN_USER_CODE_LABELS`（INTENT_SCOPE_EXCEEDED / ENTITY_UNSEEN /
  UNKNOWN_SENSITIVE_ARG / TAINTED_SENSITIVE_ARG / INTENT_UNCLEAR /
  PAYEE_NOT_FOUND → 中文解释）与 `_finding_codes`、`_replan_user_items`。
- `_respond_replan_user` 改为**确定性**（不调 LLM）：
  `respond_template("REPLAN_USER") + "\n" + 逐条 "- " 列表`；
  未知 code 回退「请补充必要信息后重新发起」。不打印任何内部 id/令牌。

## 测试

- `tests/agent/test_default_account.py`（新增 3 条）：
  未指定源 → 选 CHECKING 并带 read 来源；指定「从储蓄账户」→ 保留储蓄账户；
  两个 CHECKING → `ExhaustedClarify`。
- `tests/agent/test_respond.py`（改写 2 条）：
  REPLAN+user → 确定性回复（LLM 不被调用）；INTENT_SCOPE_EXCEEDED →
  冒号后有非空解释且不含内部 id 前缀。

红测先行：实现前 4 failed / 31 passed；实现后全部转绿。

## 门禁（tail -1）

| 命令 | 结果 |
|---|---|
| `make lint` | All checks passed；ruff format --check 通过（1 文件格式化后） |
| `.venv/bin/pytest tests/agent -q` | 526 passed, 38 skipped |
| `make test` | 3336 passed, 901 skipped |
| `make test-agent-db` | 39 passed, 525 deselected |

提交：
- `812250d` 红测
- `5434062` 修复实现
- `6f2c4c3` ruff format（plan.py）

未覆盖/备注：L2 判定 INTENT_SCOPE_EXCEEDED 的端到端（跨真实 gateway）不在本
组件范围；零 CHECKING 场景交由 L2 处理。

## 修复轮 1（独立评审 REQUEST_CHANGES 后）

评审 `docs/reviews/L1-default-account-review.md` 结论 REQUEST_CHANGES，两条：

- **P1** `_user_named_source_account` 用「全部原话子串」判定，误判四类场景：
  目标账户（「转到他的活期」）、收款人名含关键词（「转给储蓄客户王五」）、
  否定（「不要用活期」）、跨轮（上一轮提过「活期」）。误判时默认活期规则被
  静默跳过，模型选出的账户（如 SAVINGS）原样流向 L2。
- **P2a** `_checking_accounts` 按出现次数计数：同一个 CHECKING 账户在两次
  `get_accounts` 读取中都出现，会被当成两个账户而误报 `ExhaustedClarify`。

### 修复

- `_user_named_source_account` 改为只看**当前轮 typed 原话**：新增
  `_current_turn_text`（取最后一个 `kind=typed` 的 `UtteranceRef`，跳过
  hint/action/context 与更早轮次），并改用**源账户锚定**正则判定：
  - `从(我的)?(储蓄|活期|支票|定期)(账户|卡)?`
  - `用(我的)?(储蓄|活期)(账户|卡)?[^，。；！？]*(转|付|汇)`
  - 命中且前面紧跟否定词（不/别/勿/莫，`_negated_before`）时不算命名。
- `_checking_accounts` 改为按账户 id 去重（`dict.setdefault`），同一 id 只保留
  第一次出现的 `(id, source_ref, pointer)`；空 id 的账户跳过（无法引用）。

**关闭词表（P2b，记录不修改）**：仅识别「从<类型>」「用<类型>…转/付/汇」两种
显式源账户说法；其它显式说法（如「从主账户转」「从理财账户转」）不在词表内，
不会被识别为「已命名源账户」，override 会替换为用户显式指定的账户。评审明确
接受此偏差（当前种子无此类账户类型，实际影响为零）。

### 测试（`tests/agent/test_default_account.py` 3 → 9 条）

- 4 条负例：目标账户 / 收款人名含关键词 / 否定 / 跨轮关键词 → 仍走默认 CHECKING；
- 1 条正例：「从储蓄转500给王五」→ 保留储蓄账户；
- 1 条 P2a：两次读取含同一 CHECKING → 不触发 `ExhaustedClarify`，覆盖生效。

红测先行：实现前 5 failed / 4 passed；实现后 9 passed。

### 门禁（tail -1）

| 命令 | 结果 |
|---|---|
| `make lint` | 427 files already formatted |
| `.venv/bin/pytest tests/agent -q` | 532 passed, 38 skipped |
| `make test` | 3342 passed, 901 skipped |

提交：
- `8bbcd0e` 红测
- `3113f4c` 修复实现
- `4fe3ed8` ruff format（plan.py + test_default_account.py）
