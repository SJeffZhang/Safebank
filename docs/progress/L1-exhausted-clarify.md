# L1 exhausted → clarify

**分支**: `feat/l1-exhausted-clarify`（基于 main 5e11bb5）
**日期**: 2026-10-27
**协调者**: Claude (deepseek-v4-pro)

## 问题

当 `PlanAdapter` 有界重试耗尽时（max_retries=1，即最多 2 次尝试），所有问题（ref/val/sem/ch）合并为 `ReferenceError` 抛出，图节点把异常传回 LangGraph。

对「缺用户信息」类问题（必填参数为空/`__NULL__`，且该参数的值只能来自用户输入），正确行为应是向用户追问缺失信息（如「请问收款人的开户银行是？」），而非报错。

**live 实证**：「把李阿姨加为收款人，卡号6222020000004444」（未说银行名）。L1 PlanAdapter 本地校验报 `steps[0].bank_name: 必填参数不能为 __NULL__ 或空字符串…`，有界重试耗尽后整条 ERROR。

## 方案

### 判定逻辑

当 `PlanAdapter.__call__` 在最后一次尝试后仍有问题，且**全部**问题均为「缺用户信息类」，则抛出新异常 `ExhaustedClarify`（含字段名与中文标签），**不再**抛出 `ReferenceError`。

缺用户信息类的判定：
- 来自 `_required_value_problems` 且字段值在 `("", "__NULL__")`
- **且**字段属于 `_USER_ONLY_PARAMS`（值只能来自用户输入，不能从 read 数据引用）

`_USER_ONLY_PARAMS = {"name", "account_no", "bank_name", "amount_cents"}`

对应中文标签：
| 字段 | 标签 |
|---|---|
| name | 收款人姓名 |
| account_no | 收款账号 |
| bank_name | 开户银行 |
| amount_cents | 转账金额 |

**排除**的必填 id 字段（如 `from_account_id`, `card_id`, `payee_id` 等）只能从 gather 读取数据中引用，不在用户追问范围。ID 前缀错误、来源引用错误、语义错误等问题重试耗尽后保持原有失败路径。

### 图路由变更

`plan` 节点从静态边 `plan → evaluate` 改为条件边：
- `plan_exhausted_clarify == True` → `respond`（直接带 reply_draft）
- 否则 → `evaluate`（原路径）

plan 节点捕获 `ExhaustedClarify` 后设置：
- `reply_draft`: 确定性模板 `请补充以下信息：{中文标签列表}。`
- `clarification`: `{"required_fields": [...], "source": "l1_exhausted"}`
- `plan_exhausted_clarify`: `True`

### 改动文件

1. `services/agent/app/llm/plan.py`：新增 `ExhaustedClarify` 异常、`_USER_ONLY_PARAMS`、`_PARAM_LABELS`、`_exhausted_clarify_fields()` 函数；`PlanAdapter.__call__` 最后一次尝试时调用判定
2. `services/agent/app/graph/graph.py`：plan 节点捕获 `ExhaustedClarify`；静态边 `plan→evaluate` 改为条件边

### 不改变

- 请求预算与重试次数
- `max_retries` 默认值
- 其他异常类型（`LlmToolError`, `ValidationError`, `ValueError`）的重试耗尽行为
- `packages/contracts/` 接口
- `services/gateway/`

## 测试

### 红测（先写，预期失败）
1. PlanAdapter 直接调用：add_payee bank_name=__NULL__ ×2 → ExhaustedClarify，fields 含 bank_name
2. PlanAdapter 直接调用：ID 前缀错误 ×2 → 仍抛 ReferenceError（原有路径）
3. PlanAdapter 直接调用：amount_cents=__NULL__ ×2 → ExhaustedClarify，labels 含「转账金额」
4. 编译图端到端：fake LLM 两次返回 bank_name=__NULL__ → respond，reply_draft 含「开户银行」且无 G4 请求

### 绿测（实现后全部通过）
- `make lint` exit 0
- `pytest tests/agent -q` 全过
- `make test` exit 0
- `make test-agent-db` exit 0