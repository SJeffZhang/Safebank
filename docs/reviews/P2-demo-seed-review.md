# P2 demo-customer seed 独立评审

- 评审范围：`git diff origin/main...c574a65`
- 评审对象：demo 客户种子安装器（seed_demo.py）、seedlib 抽取、Makefile seed-demo 目标、test_seed_demo.py
- 评审方式：只读，逐项核对后追加结论

## 结论

**APPROVE**（无 P0 / P1；2 条 P2 加固建议，不阻塞合并）

## P0

无。

## P1

无。

## P2

1. **seedlib 未强制「客户账户 debit == 该账户 OUT 流水之和」这一守恒前提**。`seed_demo.py:80-117` 的开户资金逻辑假设：每个新客户账户的最终 `debits_posted_cents` 全部来自该账户的 OUT 交易（`funding = credits_posted − ΣIN`，并按 `Σcredits = ΣIN + funding`、`Σdebits = out_delta` 推出全局 `sum(debits)==sum(credits)`）。但 `seedlib.py` 只校验 `credits−debits==ledger_balance`（`seedlib.py:269`）与「最新一笔 balance_after == ledger_balance」（`seedlib.py:578-584`），**没有**校验 `ΣOUT(per account) == debits_posted` 或 `ΣIN(per account) <= credits_posted`。若未来某个 demo 种子存在「不来自 OUT 流水的 debit」或「credits < ΣIN」的账户，当前全部校验都会通过，但 `install_demo` 会静默破坏全局守恒。当前 `l1_seed_v1.json` 的数据是自洽的（由 `test_seed_demo.py:146-159` 的全局守恒断言兜底），因此仅是加固建议：在 `seedlib.prepare_seed` 中补这两条账户级一致性校验，把不变量从「数据属性」变为「代码强制」。

2. **测试覆盖可再收窄到第二客户与流水内容**。`test_seed_demo.py` 只断言 `cus_a/acc_a` 未被改动（`:107-126`），未断言 `cus_b` 账户余额不变；只断言重复运行后行数相等（`:135-144`），未抽查 demo 客户自己的 `transactions.balance_after_cents` 与账户 `ledger_balance` 一致。属覆盖范围优化，不阻塞。

## 已核对项

1. **增量且幂等 — 已有客户整体跳过**：`seed_demo.py:56-64` 以 `new_customer_ids = prepared − existing` 决定插入，`skipped_customer_ids` 为交集；重跑时 `new_customer_ids` 为空，后续所有写入（accounts/cards/payees/transactions/transfers/billings/standing_instructions）都按 `in new_customer_ids` 过滤而全部跳过，行数不变。通过。

2. **不删除、不修改 cus_a/cus_b**：账户只插入 `a["customer_id"] in new_customer_ids` 的新账户（`seed_demo.py:66-73`）；对既有账户无任何 `UPDATE`。唯一触及既有账户的写入是内部清算账户与种子权益账户（见下）。通过。

3. **不修改 clock.sim_now / dataset_revision**：`seed_demo.py` 全文无 `m.clock` 写入；`prepare_seed` 解析出的 `sim_now` 仅用作 funding 转账与账单的 `occurred_at/created_at`（`seed_demo.py:108`、`seedlib.py:396/489`）。测试 `:128-133` 断言 `sim_now` 与 `dataset_revision` 逐值不变。通过。

4. **商户去重**：`seed_demo.py:128-133` 用 `pg_insert(m.merchants).on_conflict_do_nothing()`。`merchants` 表只有 `id` 主键、`name` 无唯一约束（`models.py:281-289`），因此 `ON CONFLICT DO NOTHING` 只可能命中主键冲突，不会因同名商户被误跳过，也不会因其他唯一约束静默吞行。通过。

5. **账本平衡与客户归属**：funding 转账借记内部权益账户 `acc_seed_equity`、贷记新客户账户（`seed_demo.py:94-111`），清算账户只做 IN/OUT 增量折叠（`:146-156`）；两类写入都落在内部账户或新客户自有账户上，无跨客户写入。守恒的成立依赖 P2-1 所述的数据前提，当前种子由测试全局守恒断言兜底（`test_seed_demo.py:146-159`）。通过（附 P2-1）。

6. **seedlib 抽取与 E0b 行为一致、校验未放松**：`evaluation/runner/seed.py` 的 `_prepare` 逻辑逐行移入 `seedlib.prepare_seed`，非 obligation 路径的校验（卡限额链/账户限额/账户 balance 一致性/收款人指纹去重/交易引用与负余额/最新 balance_after==ledger）与原实现逐条一致；`include_obligations=False`（默认）仍在 `seedlib.py:140-147` 对 billings/standing_instructions 大声拒绝，`load_seed` 仍写 clock（`seed.py:65-72`）。`_prepare = prepare_seed` 别名无任何外部调用者（grep 仅命中定义处）。通过。

7. **种子文件只有一份**：`l1_seed_v1.json` 仅存在于 `services/bank-backend/fixtures/`（`evaluation/runner/fixtures/` 已不存在）；`tests/evaluation/corpus/spec.py` 与 `generate.py` 已指向新路径，无残留旧路径引用（grep `runner/fixtures` 无命中）。通过。

8. **Makefile seed-demo 目标**：`seed-demo: docker compose run --rm bank-maintenance python -m app.seed_demo`（与 `seed-p1` 同容器），仅新增到 `.PHONY` 与配方；`up` 目标未改（仍为 `p1-provision migrate seed-p1 gateway-provision migrate-gateway`，不包含 seed-demo）。通过。

9. **容器内可运行性（静态核对）**：`Dockerfile` `WORKDIR /app`、`COPY services ./services`、`PYTHONPATH` 含 `/app/services/bank-backend`（`Dockerfile:3,9,10`），故 `python -m app.seed_demo` 解析到 `/app/services/bank-backend/app/seed_demo.py`，`SEED_PATH = Path(__file__).parent.parent/"fixtures"/"l1_seed_v1.json"` 解析到 `/app/services/bank-backend/fixtures/l1_seed_v1.json`，与镜像布局一致。未实际执行 `make seed-demo`（本评审 shell 的 grep 异常、且未获运行授权），`BANK_DATABASE_URL`/`FINGERPRINT_KEY` 的 compose 注入依赖 bank-maintenance 服务既有约定（`seed-p1` 同容器同模式），风险低。

10. **测试断言具体性**：`test_seed_demo.py` 断言了 cus_zs 余额（`acc_zs_chk==600000`、`acc_zs_sav==2000000`，经真实 L3 `get_accounts` 返回，`:180-182`）、cus_a 账户四字段逐值不变与流水数不变（`:107-126`）、`sim_now`/`dataset_revision` 不变（`:128-133`）、重复运行后 9 张表行数相等（`:135-144`）、全局账本守恒与 pending 为 0（`:146-159`）。覆盖了评审要求的五类断言。通过（附 P2-2）。

11. **测试隔离安全**：`obligations_db` fixture（`tests/obligations_data/conftest.py:46-107,132-143`）以 `--tmpfs /var/lib/postgresql/data` 起独立容器、随机端口、`OBLIGATIONS_DB_TESTS=1` 才启用、teardown `docker rm -f`；`test_seed_demo.py:53-67` 的 `TRUNCATE` 作用在 `bank_obligations_test` 临时库上，不触碰长期 bank 卷。通过。
