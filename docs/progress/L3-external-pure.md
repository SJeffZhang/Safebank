# L3 external 事件纯函数进度

任务：在 `packages/bankcore/src/safebank_bankcore/external.py` 实现三个模拟外部
（system/demo）事件的纯函数，供白名单合成任务（PRD §5.1 / §7.7 / §8.5 / §10.2
DEMO_EVENT）调用，不是写工具。

## 现状（已提交，全部门禁通过）

- `make lint`：通过（ruff check + format --check）。
- `make test`：**3388 passed / 903 skipped**。
- `make contracts-check`：PASS（contracts v1 冻结快照无漂移）。
- `tests/bankcore/test_external.py`：**18 passed**。

提交：
- `120d658` — 红测（实现缺失时 1 collection error，先红）。
- `9c5ea3a` — `external.py` 实现（294 行新增）。
- `37629d5` — ruff format 两个文件。

## 交付内容

- `apply_external_credit(state, ExternalCredit, now, ctx)`：模拟工资/退款入账。
  资金从 `acc_external_clearing` 清算账户转入客户账户；`counterparty` / `memo`
  作为不可信文本原样存储到交易。
- `apply_external_debit(state, ExternalDebit, now, ctx)`：模拟刷卡或外部扣款。
  检查余额（INSUFFICIENT_FUNDS）、卡状态（FROZEN/LOST → CARD_STATE_CONFLICT）、
  卡单笔（CARD_SINGLE_LIMIT）与当日（CARD_DAILY_LIMIT）限额；限额内过账并把
  消费计入 `counters.daily_used_cents`（不计入对外转出，PRD 刷卡/代扣约定）。
- `apply_split_payment(state, SplitPayment, now, ctx)`：AA 收款人之一付款。
  给收款账户入账、把对应 SplitItem 置为 PAID/记录 paid_at、received_cents 增加、
  outstanding_cents 减少；重复付款（已 PAID）与金额不匹配均 SPLIT_INVALID。

## 关键设计与 ADR-011 增量说明

- **新文件、纯增量**：`external.py` 是新增模块，未改动 `ledger.py`、`banking.py`、
  `split.py`、contracts 或迁移。
- **未修改 ledger.py 的原因**：`ledger.post_transfer` 把内部清算对手方硬编码为
  `"外部结算"`，无法保存不可信对手方文本（PRD §5.2 要求原样存）。故在
  `external.py` 内实现自包含 `_post_simple`，**复用共享算术 `account_deltas`** 与
  `replace_state`，交易/转账/幂等/守恒语义与 `post_transfer` 的 POSTED 路径一致。
- **无 UpdateSplit 效应**：contracts 的 `LedgerEffect` 判别联合中没有拆分项更新
  效应（`InsertSplit` 存在、`UpdateSplit` 不存在）。`apply_split_payment` 通过
  `replace_state(..., splits=...)` 直接返回更新后的 `state.splits`，效应只含
  PostTransfer + AppendTransaction + OperationEvent。
- **金额守恒**：所有资金都经 `acc_external_clearing` 内部清算账户过账，客户账户
  与内部账户余额合计不变（测试断言 `_total_balance` 守恒）。
- **新标签前缀**：转账/交易 ID 派生使用 `excr`（入账）、`exdb`（扣款）、`splp`
  （AA 付款）作为 `derive` 的 kind 段，避免与其它工具冲突。
- **不可信数据**：`counterparty` / `memo` 仅写入交易字段，不参与任何授权判定
  （本模块为纯函数，无授权逻辑）。

## 未覆盖 / 后续

- 本模块只交付纯函数；接入 L3 执行链（`banking.py` 的 step_result 分支、
  `state.py` persist、HTTP/真实 PG 验收）不在本任务范围，由后续组件完成。
- `ExternalCredit.kind` / `ExternalDebit.kind` 是自由字符串（默认 SALARY /
  CARD_SPEND），未做枚举白名单校验；消费分类仅按 `kind == "CARD_SPEND"` 简单
  二分（消费 / 其他）。
