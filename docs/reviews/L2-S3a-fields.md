# S3a 具体字段模型独立只读审核

日期：2026-10-23。审核对象：`/private/tmp/sbg-s3a-model-proposal.md`（251 行）；约束：root 的 S3a 任务书、ADR-009、PRD §5.4/§7.7/§8.2–8.3/§9.3，以及已通过的框架审核。对照当前 root 的 contracts、S2、L3 metadata/唯一 loader/snapshot HTTP、银行历史迁移与验收入口；本报告不是实现代码审核。

**规范：FAIL（下述一个字段组合缺口）。字段可实施性：REQUEST_CHANGES。**

四表边界和实现路径可以闭合；仅需补充 F1 的最小组合约束后定点复核，无须扩大表、改变外部契约或重开架构。未发现其他必然阻塞。

## F1 — 阻塞：无授权来源的商户 run 仍可被记为 SUCCESS

位置：提案 §2.5 第 79 行、§3.4 第 158–167 行及 §3.5 第 179–180 行。

提案允许 `MERCHANT_MANDATE` 的 `instruction_id=null`，意图是保留未找到授权时的真实被拦扣费；但列出的成功组合只要求 `transfer_id` 非空且 `code=null`。因此一条 `kind=MERCHANT_MANDATE, billing_id=<有效账单>, instruction_id=null, status=SUCCESS, transfer_id=<同源账户真实transfer>, code=null` 的记录能满足全部已列 CHECK 和 FK：两个含 instruction_id 的复合 FK 都因 null 而跳过，账单/账户 FK 则正常通过。

这与 PRD 第 618–619 行的“有效代扣经授权后扣款、无效代扣拒绝”及第 1202 行的授权/账单独立生命周期不一致。模型将“无授权被拦历史”的 nullable 入口同时开放给成功历史，不能实现提案自身要求的真实终态与来源关联。它不直接新增资金执行入口，但会把缺少授权关联的扣费记为真实成功。

最小修正：

- 在 `InstructionRun` 校验和 `instruction_runs` CHECK 中明确 `status='SUCCESS' => instruction_id IS NOT NULL`。定时分支原已要求该字段，商户成功分支因此也必须关联实际授权，并继续使用现有 kind/owner/account/billing 复合 FK。
- 保留 `MERCHANT_MANDATE + instruction_id=null + BLOCKED`，以保存确实找不到授权的扣费拒绝。无需为这点新增父表、event job 或 scheduler。
- 专属模型/PG 组合矩阵增加两项：无 instruction 的商户 SUCCESS 必须拒绝；同一账单的无 instruction BLOCKED 可以保存。已有成功历史关联的授权后来变为 DELETED/DISABLED，快照仍须接受并保留真实 last_run。

这里要求的是历史记录的非空来源关联及所有权一致性；**不能用授权的当前 status 来追认或否定历史成功**。S3a 的四表也不能单独证明 due_at 当时已经完成 L2 授权、grant 核验和事件原子执行；那些仍属于后续事件组件。修正不应把时点授权事实伪称为本组件已证明。

## 其余具体字段结论

- 类型与生命周期：严格整数分、限定金额、aware 瞬间与上海本地钟面时间的区分可实施；ACTIVE/DISABLED/DELETED 与四种 run 终态分开，符合 PRD。DUES 的周期字段全 null、只从 billing 取周期，避免双事实源；priority 不参与 rank=3/4 排序。
- 四表与复合 FK：新增 run 的 customer/account/kind 是必要关系见证。accounts/payees/transfers 候选键及提案的复合 FK 可以约束同客户源账户、本人目标、payee owner、账单/商户/授权配对；nullable 分支除 F1 外已有明确组合约束。只追加 runs、唯一 event、来源+due_at、唯一 transfer 与 NO ACTION 可保持历史，不需要第五表。
- 逻辑 ID：`source_plan_id` 与 `event_id` 当前没有相应本地父表，提案明确按非空 typed 逻辑 ID 保存，未冒充 FK 或已实现事件。这与批准的四表边界相符，不要求构造 plans/event_jobs 或将种子伪塞进 plan_executions。
- 覆盖与隐私：obligations=None 与显式完整空分开，covered 与 scope/versions/customers 对齐；唯一 loader 覆盖全 scope 的三种授权状态、全部账单及历史，缺引用显式失败。OTHER/EXTERNAL 只通过最小 target 映射判定，非 caller 联系资料不进入旧 payees，第三客户不递归进账户/版本/锁范围；只有 OWN 提供保证贷记。
- S2 同源：raw snapshot 为唯一事实源，旧 View 仅派生投影；原签名、七天范围、前缀峰值、负 max_outflow、稳定 rank 和私有日历核心均保留。月末/闰年/stale 指针可以使用原锚点计算，无须写周期指针或伪造执行历史。
- 历史升级：0003 仅四表及必要父候选键、权限/触发器；旧 0002 先装真实执行数据再升级两次的方案绕开历史 0002 的空执行表门禁，能够验证账本、intent/evidence/outbox、序列和版本保留。readiness/head 定点更新、最新 drift 精确补历史 current_card FK，不需要修改历史文件或全量忽略差异。
- 真实验收：专属 tmpfs PG → 当前真实 Uvicorn/TCP snapshot → BankState JSON 往返 → 唯一 S2，以及真实 prepare/execute 后的 295500 分上限，是可执行的验收链；隐私哨兵、真实空/未知、取消但账单保留、负上限、历史与一致快照矩阵覆盖本组件边界。都只是拟运行证据，不能据此解除 W2/W3 阻塞。
- 冻结边界：只更新 BankState 内部新递归定义及哈希，逐项比较 18 读/14 写、LLM tool、其余顶级模型与 LedgerEffect，符合受控增量；未把 R7/R10/R11、1–30 天、signed 公共契约、scheduler、领域写或完整 baseline 搭进本组件。

未另列非阻塞问题。F1 修正后，按本报告可以直接进入既定实施/独立验收门禁。

## 证据边界

仅文件读取与只读检索；未修改仓库、未运行测试/lint/迁移/HTTP/Docker，未做 Git 变更，未派 agent。唯一写入为本临时报告。实现 owner 的旧基线尚需按协调者指令同步 main；本结论不声称任何义务存储或生产加载已经实现。
