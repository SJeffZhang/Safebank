# bankcore · 金融领域内核

L2 预演与 L3 执行共享的纯函数库。不读取数据库、网络、系统时钟或随机数；调用方传入不可变 `BankState`、时间与计划身份，获得新状态、类型化效果和违规结果。

## 领域规则

| 模块 | 职责 |
|---|---|
| `ledger.py` | 已过账 / 在途记账、确认、释放与唯一终结 |
| `banking.py` | 多步骤计划、转账、收款人、卡片与公共结果适配 |
| `standing.py` / `grants.py` | 定时指令、取消订阅、授权条款与周期 |
| `reserve.py` | 未来七天义务与资金预留 |
| `funds.py` / `nav.py` / `products.py` | 申购、赎回、净值与产品规则 |
| `risk.py` | 问卷计分、风险等级与业务日期有效性 |
| `analytics.py` | 消费分类、异常与周期订阅识别 |
| `split.py` | AA 收款分配 |
| `external.py` / `events.py` | 外部事件与到期业务效果 |
| `references.py` / `scope.py` / `ids.py` | 计划引用、锁范围与确定性 ID |

金额只用整数分，份额使用 Decimal。计划内硬约束失败返回原状态与空效果；数据库持久化只在 L3 发生。授权、审批和 OTP 属于 L2，不由本库代替。

## 验证

```bash
# 仓库根目录，make setup 后执行
.venv/bin/pytest tests/bankcore -q
```

改动写规则时，还需执行 `make test-p1`，再运行 `make test-p1-concurrency` 和 `make test-p1-consistency`，核对真实 PostgreSQL 下的并发与预演/执行一致性。

账本语义参考 [TigerBeetle 账户文档](https://docs.tigerbeetle.com/reference/account/)与[两阶段转账文档](https://docs.tigerbeetle.com/coding/two-phase-transfers/)，未复制上游源码。设计取舍与依赖见 [PRD §4](../../PRD.md) 和[第三方记录](../../docs/third-party.md)。
