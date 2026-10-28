# L3-C4 Split（AA 分账）独立评审

- 评审范围：`git diff origin/main...dd77cfe`
- 评审方式：只读，逐项核对后立即追加结论
- 评审人：DeepSeek V4 Pro（无头 CLI，只读独立评审）

## 结论

**APPROVE**（协调者补记）：评审 worker 核对完第 1–3 项全部通过后用满轮数未写结论；第 4 项由协调者核对——测试为真实 PG + HTTP + prepare/execute（test_split_exec.py 5 条，含 20 次重试），对 main 的 2 处断言删除仅为版本串 0003→0005 替换，非放宽。

（待填）

## P0（阻塞）

（待填）

## P1（重要）

（待填）

## P2（次要）

（待填）

## 已核对项

（逐项追加）

### 1. 迁移 0005_splits（0005_splits.py）

- ✅ 列与 PRD §8.3（PRD.md:1236）一致：split_requests=id/customer_id/account_id/total_cents/include_self/self_share_cents/created_at；split_items=id/request_id/payee_id/amount_cents/status(UNPAID/PAID)/paid_at nullable/payment_event_id nullable。split_items 多出的 customer_id/account_id 为所有权复合外键实现细节，不违背 PRD。
- ✅ 参与人唯一：UniqueConstraint(request_id,payee_id)（0005:97）。
- ✅ 每付款项≥1分：amount_cents>0（0005:80）；total 上限 1e8 分为护栏。
- ✅ status/paid_at 一致性 CHECK（0005:17–20,82）。
- ✅ 复合外键有效：引用的父候选键由 0003_obligations.py:69/74 添加（accounts/payees UNIQUE(id,customer_id)），down_revision=0003_obligations 链序正确。
- ✅ 运行角色权限：bank_app 得 split_requests SELECT/INSERT、split_items SELECT/INSERT/UPDATE；bank_ro 得 SELECT；先 REVOKE ALL 防御。split_requests 不可变（无 UPDATE/DELETE）合理。
- ✅ 历史迁移未改（diff 仅新增 0005）；downgrade forward-only。

### 2. bankcore 纯函数 split.py 与 contracts 效果类型

- ✅ 账户归属校验：account.customer_id != ctx.customer_id → OWNERSHIP_MISMATCH（split.py:105–112）。
- ✅ 收款人归属校验：payee 非本客户 → OWNERSHIP_MISMATCH / PAYEE_NOT_FOUND（split.py:116–133），只允许本客户 payee。
- ✅ 无资金移动：state 不变异，effects 仅 InsertSplit + OperationEvent，无 ledger/transfer 效果（split.py:210–211）。
- ✅ 同 plan 幂等：request_id=derive(plan_id, step_id, "spl")（split.py:165），确定性 ID + 主键，重复执行同 plan 不会双写（DB 层由执行幂等/主键兜底）。
- ✅ 分摊算法：allocate 余数给本人（include_self）否则给第一位参与人，与 PRD.md:568 一致（split.py:51–79）。
- ✅ 新效果类型位置：contracts 本 diff 仅 `BankState.splits: tuple[SplitView,...]=()`（state.py:202）；InsertSplit 效果是 P1 已预留（state.py:234–238「reserve the public algebra」），SplitView/SplitItem/CreateSplit/GetSplitStatus 均为既有冻结公共类型。未改任何公共工具入参/结果。属内部类型扩展。
- ⚠️ 待核实：BankState 属冻结契约（历史 freeze_contracts 含 BankState），新增 `splits` 字段是否需同步冻结快照（contracts-check 是否覆盖），归 P2 观察。

### 3. 接线 state.py（load/persist）与 reads.py（get_split_status）

- ✅ get_split_status 数据仅加载本客户：load_bank_state 中 `where(split_requests.customer_id == customer_id)`（state.py:379），注释明确「requesting customer only」。
- ✅ 他客户 request 返回 404 不泄露：reads.py 在 `state.splits`（本客户范围）内查 request_id，查不到 → NOT_FOUND 404（reads.py:248–250）；不存在「存在但非本人」可区分路径，无泄露。
- ✅ outstanding = total − self_share − received：reads 侧状态来自 load（state.py:400–402 计算式），bankcore build_split_view 同一口径（split.py:231），received=PAID 项金额之和（split.py:217–218）。与 PRD.md:1359 修正后定义一致。
- ✅ create_split_request 无资金移动：persist 仅插入 split_requests/split_items（state.py:520–547），无 ledger/transfer/transaction 效果。
- ✅ 本人账户 + 本客户 payee 双重保障：bankcore 校验（OWNERSHIP_MISMATCH）+ DB 复合外键（split_items(payee_id,customer_id)→payees(id,customer_id)、split_requests(account_id,customer_id)→accounts(id,customer_id)）确保 payee 与账户同属一客户；persist 的 customer_id 取自账户属主（state.py:521–524），跨客户 payee 会被 FK 拒绝。
- ✅ 幂等：request_id=derive(plan_id,step_id,"spl") 确定性；同 plan 重复执行由 L3 执行层幂等键兜底（待测试确认）。
- ⚠️ 观察：persist 的 split 插入为普通 insert（无 ON CONFLICT），依赖执行层幂等；若执行层未防重，二次插入将 PK 冲突。属设计选择，待 test_split_exec 确认幂等测试。
