# S3a 字段提案 F1 定点复审

日期：2026-10-24。对象：主仓库 `docs/plans/briefs/S3a-model-proposal.md` 的 F1 修订（对照 `docs/reviews/L2-S3a-fields.md` 的 F1 阻塞项、ADR-009、PRD §5.4/§7.7/§8.3/§9.5a，及现有银行迁移 `schema_v1.json`、`0001_core.py`）。只读；唯一写入本报告（写入 W2 worktree，未改主仓库任何文件）。未运行 pytest/迁移/HTTP。

## F1 原问题

`MERCHANT_MANDATE + instruction_id=null + SUCCESS` 可借 SQL MATCH SIMPLE 空值跳过含 `instruction_id` 的复合 FK，把无授权关联的商户扣费记为真实成功。

## 逐点核对修订

1. **SUCCESS 的 instruction_id 非空由 DB CHECK 强制（不只应用层）** ✓
   - 应用层：§2.5（提案第 79 行）「InstructionRun 模型校验明确要求 status=SUCCESS => instruction_id 非空，同时 transfer_id 非空、code=null」。
   - 数据库：§3.4（第 169 行）明确新增 `CHECK (status <> 'SUCCESS' OR instruction_id IS NOT NULL)`。status='SUCCESS' 时要求非空、'BLOCKED' 时放行，无三值逻辑绕过（§3 第 87 行已要求各分支显式 IS NOT NULL）。
   - 配合既有复合 FK（§3.5 第 5、6 条：`run(instruction_id,customer_id,account_id,kind)→SI` 与四列 `run(…,billing_id)→SI(…,billing_id)`），SUCCESS 必须指向真实 MERCHANT_MANDATE 授权且 kind/owner/account/billing 一致。洞已闭合。
2. **无授权 BLOCKED 可存 NULL** ✓：§2.5 第 79 行「保留 MERCHANT_MANDATE + instruction_id=null + BLOCKED」；§3.4 第 160 行「商户未找到授权的 BLOCKED 可空」。该行仍受 `run(billing_id,customer_id,account_id)→billing` 与 `run(account_id,customer_id)→accounts` 复合 FK 约束，不伪归任何指令的 last_run。
3. **授权现为 DELETED/DISABLED 的历史 SUCCESS 仍能加载** ✓：§2.5 第 81 行、§3.4 第 169 行「历史关联只要求 kind/owner/account/billing 一致，不以指令当前 ACTIVE 为约束，不因后来 DELETED/DISABLED 拒绝已有 SUCCESS 或从快照删除它」。SI 行不物理删除、run 只追加，FK 目标仍在。
4. **新增三行矩阵正确** ✓：§6 第 247–252 行三行分别覆盖——无指令商户 SUCCESS 模型+PG 双拒；同账单无指令 BLOCKED 可存并经 snapshot 保留；SUCCESS 关联指令后改 DELETED/DISABLED 两场景均保留历史与真实 last_run。与 F1 评审「最小修正」三点一一对应。
5. **修订未引入新矛盾** ✓
   - 外键目标：accounts/payees/transfers 均有 id 主键（`schema_v1.json` 第 6/13/14 行），候选 `UNIQUE(id,customer_id)`/`UNIQUE(id,debit_account_id)` 可加且必然满足；INTERNAL 账户 customer_id 为 NULL，不会被非空复合 FK 误引为义务源账户。
   - 只追加历史：run 复用既有 `forbid_mutation()`（`0001_core.py` 第 27 行已定义）新建 BEFORE UPDATE OR DELETE 触发器；新增 CHECK 与触发器无冲突。SI 保留 SELECT/INSERT/UPDATE 以改 status。
   - loader 的 None 与空区分：§2.1 `obligations=None`（未加载）vs 完整空容器（真实无义务）语义未受影响。

## 非阻塞备注

- PRD §8.3 第 1235 行 `instruction_runs` 尚未列出本提案新增的 `customer_id`/`account_id`/`kind` 三个关系见证列，也未登记 `SUCCESS=>instruction_id IS NOT NULL` CHECK；S3a 实施时需按 AGENTS §8.1 同步 PRD（提案 §7 已声明提案阶段不改 PRD，实施阶段应落）。不属 F1 矛盾。

结论：**APPROVE**。P0/P1：无。F1 已在模型与 DB CHECK 双层闭合，三行矩阵正确，未引入外键目标/只追加/None-空区分方面的新矛盾。
