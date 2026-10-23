# bankcore

无数据库、网络、时钟读取或随机数依赖的纯函数领域内核。L2 预演与 L3 执行复用相同代码；调用方传入不可变 BankState、时间、计划/步骤标识，返回新状态、类型化 LedgerEffect 与 Violation。

`ledger.post_transfer` 实现 posted/pending、全部/部分确认、释放与唯一终结；`banking.apply_plan` 实现 P1 转账、收款人及卡片规则，多步失败返回原状态和空效果；`references.resolved_steps` 与 `scope.scope_for` 统一引用身份和锁范围。确定性 ID 使用 UUID5，金额只用整数分。

根目录 `make setup` 后 `.venv/bin/pytest tests/bankcore -q` 独立验收；无需配置。改动写规则后，还必须运行 `make test-p1-concurrency` 和 `make test-p1-consistency`，前置 `make test-p1`。数据库加载/持久化只有 L3 的 app/state.py 一个实现。

账本原则参考 TigerBeetle 官方文档，未复制上游源码：

- https://docs.tigerbeetle.com/reference/account/
- https://docs.tigerbeetle.com/coding/two-phase-transfers/

P1 不实现调度、AA、订阅、基金、事件调度器及 L2 授权。
