# S3a 实施前最小模型与加载提案

日期：2026-10-23。Owner：s3a_obligations_data。只读基线：`681554752215dcf82b6e768f4a3f788c19469834`，worktree `/Users/jet/.codex/worktrees/safebank-reserve/WeBank`，分支 `feat/l3-obligations-data`，读取前后工作树干净。

本报告是代码前的接口审查输入，不是实现或测试结果。唯一写入文件为本文件；未运行测试、lint、迁移、Docker、HTTP 探针或 Git 修改，未派 agent。报告编写期间协调者通知：S1d PR10 已四CI成功合并 main `44ded2f2431bdb0ce1d3f90f2c5d7d1b2ac67697`，共享前置门禁完成；本次仍只读旧指定基线，未变更HEAD。待字段审查及明确开工消息后，再协调同步新main实施。

已实际读取：完整 AGENTS；唯一需求 `docs/plans/briefs/S3a-obligations-data.md`；ADR-009；已 APPROVE 的 `docs/reviews/L2-S3a-obligations-design.md`；PRD §0.1/2.2/5.1/5.4/7.7/8.2–8.5/9.3，以及相关 §7.11；S2 任务/进度/验收/评审；现有 contracts state/views/tools/base、bankcore reserve/scope、L3 models/state/db/main/seed、两份银行迁移/历史 DDL、现有数据库/状态/HTTP测试、fixture、Makefile、CI、freeze 脚本。未重新讨论已批准的大框架。

## 1. 当前代码事实及受控改动

- `contracts/state.py:190` 的 BankState 当前只有默认空的 `instructions/subscriptions`；不能证明加载过义务。
- `views.py:89` 的 ScheduledView 有周期锚点，但缺 customer_id、kind/amount_type/recurrence、priority、grant_scope_hash、source_plan_id、created_at。last_run 是展示值，不是历史来源。
- `views.py:127` 的 SubscriptionView 没有 billing.active、anchor_day/weekday/time_of_day/created_at，不能从过期 next_due_at 还原月末周期；也没有独立 billing 与 mandate 生命周期。
- `app/state.py:85` 唯一 loader 当前不查询四个义务表；只把请求客户的完整 payees 放入 state（:116），这一隐私边界保留。
- `reserve.py:157` 的目标归属依赖 state.payees 和加载的目标账户，不能处理 scope 内第二客户对第三客户的既有定时义务。:176 的商户展开依赖不完整展示 View，月度 stale 指针会拒绝；:209 尚没有完整性证据校验。
- `app/main.py:113–133` 已在同一 REPEATABLE READ READ ONLY 事务内 resolve_scope → 唯一 loader → BankState 响应；无须新增 HTTP 接口。
- `app/db.py:21` 与 `tests/integration/test_database.py:19` 均写死 `0002_identity`。当前银行层未发现单独的最新 metadata drift 测试；`tests/gateway/test_ddl_drift.py` 属网关，不能顺手改它。

允许的代码面：contracts 新内部 obligations 模型及 BankState 字段；bankcore.reserve 必要同源兼容；L3 四表 metadata、唯一 loader、readiness head；银行新增 0003；专属测试/Make/CI及审查过的内部 schema freeze。外部 18读/14写的参数与结果、LLM tool、LedgerEffect 代数均不改；R7/R10/R11、scheduler、领域写和真实 baseline 安装不实现。

## 2. 内部 typed 模型

建议新增 `packages/contracts/src/safebank_contracts/obligations.py`，统一复用 `Contract` 的 frozen/extra=forbid、Id、Cents、Timestamp、FrozenMap 的深层只读序列化方式；不引入依赖。以下均为内部模型。

### 2.1 类型与容器

| 名称 | 精确类型/值域 | 不变量与用途 |
|---|---|---|
| InstructionStatus | Literal[ACTIVE,DISABLED,DELETED] | 授权生命周期；BLOCKED/FAILED 属执行历史，不能冒充授权状态 |
| InstructionPriority | Literal[URGENT,HIGH,MEDIUM,LOW] | 保留领域优先级；不参与 S2 的 rank 排序 |
| BillingPeriod | Literal[WEEKLY,MONTHLY] | 对应 PRD §5.4 两种周期性商户扣费；ONCE 属定时转账，不猜测一次账单的年费折算 |
| LocalExecutionTime | 无 offset 的 `datetime.time`，JSON 为 ISO 本地时分秒 | 是 Asia/Shanghai 下的每日钟面时间，不是瞬间；拒绝带 tzinfo、24:00、非法时分秒。数据库 TIME WITHOUT TIME ZONE。所有表示瞬间的 *_at 仍用 Timestamp/timestamptz |
| AnchorDay / Weekday | 严格 int 1..31 / 0..6 | 拒绝 bool；weekday 周一0，周日6 |
| PositiveObligationCents | 严格整数 1..100000000 | 与 PRD 单笔合同上限一致，不把全部 Cents 放宽为有符号数 |
| ObligationSnapshot.covered_customer_ids | tuple[Id,...]，无默认值 | 非空、唯一、排序；与 state.versions 的客户集合严格相等，所有客户必须有 Customer 和完整自有账户集合 |
| .merchants | FrozenMap[Merchant]，必填 | 仅关联 scope 账单/授权所需商户；key=id |
| .billings | FrozenMap[MerchantBilling]，必填 | 所有 scope 客户全部账单，包括 inactive、无授权或已取消授权的账单；key=id |
| .standing_instructions | FrozenMap[ScheduledInstruction \| MerchantMandate]，必填 | kind 判别联合，加载全部三种 status，key=id |
| .runs | tuple[InstructionRun,...]，必填 | scope 客户真实终态历史，稳定按 due_at/id 排序；不从 next_*、transaction 或当前状态伪造 |
| .instruction_targets | FrozenMap[InstructionTarget]，必填 | 所有定时指令恰有一条最小归属映射；mandate 不需要收款人映射 |
| BankState.obligations | ObligationSnapshot \| None，默认 None | None=未加载。完整空必须显式提供 covered ids 和五个空集合；容器各集合不能各自默认空，避免漏一个查询后仍声称完整 |

五个领域集合含 merchants/billings/instructions/runs 四表数据和派生 targets。容器不是“非 null 即盲信”：整体校验表间引用、客户覆盖、源账户、OWN 目标、重复身份与组合约束；loader 先收齐再构造，失败不降为 None 或空。S2 再校验覆盖和所消费引用，防御 model_copy/手工构造的不完整输入。

### 2.2 Merchant / MerchantBilling

Merchant 对应下节 merchants 的全部五字段。MerchantBilling 对应 merchant_billing 的全部十二字段；除 anchor_day/weekday 按周期可空外，其余必填。

MONTHLY 必须 anchor_day 非空、weekday=null；WEEKLY 必须 weekday 非空、anchor_day=null。next_due_at 先按瞬间转上海，再验证本地钟面时间、星期或 `min(anchor_day,该月末日)`。next_due_at 可以早于 sim_now：有原始锚点即可纯计算未来出现时刻；不 UPDATE 指针，不把过期周期当已执行。若缺锚点、时刻冲突或 owner 不明，明确失败。

### 2.3 两种 StandingInstruction 判别项

共同必填：id:Id、customer_id:Id、kind:Literal、status:InstructionStatus、from_account_id:Id、priority:InstructionPriority、grant_scope_hash:64位十六进制字符串、source_plan_id:Id、created_at:Timestamp；end_at:Timestamp|null 表示授权截止瞬间，边界包含。

ScheduledInstruction：kind=SCHEDULED_TRANSFER、amount_type=FIXED、recurrence=PERIODIC；amount_cents:PositiveObligationCents；payee_id:Id|null / to_account_id:Id|null 恰一非空；period:Period（ONCE/WEEKLY/MONTHLY）；anchor_day、weekday、time_of_day、next_run_at（Timestamp|null）。ACTIVE 必须 next_run_at 非空，ONCE 两锚点均空，WEEKLY/月度锚点按上规则。merchant_id/billing_id/max_amount_cents 必须 null。

MerchantMandate：kind=MERCHANT_MANDATE、amount_type=DUES、recurrence=AS_PER_DUES；merchant_id:Id、billing_id:Id、max_amount_cents:PositiveObligationCents；amount_cents/payee_id/to_account_id 必须 null。period/anchor_day/weekday/time_of_day/next_run_at **全部 null**：账单是唯一周期事实源，不抄一套允许漂移的值。end_at 仍可空或有效截止，用于授权有效期，不承担 billing 周期来源。DELETED/DISABLED 留存原 grant hash/source plan，不物理删除，账单 active 与授权 status 互不代替。

金额与周期字段的 nullable 是受 kind 判别约束的 nullable，不是随意缺失。ACTIVE + billing.active + 截止窗口内的账单才进入预留。S2 继续使用实际 amount_cents，不以 max 替换；max 是授权扣费硬约束，不因本数据组件引入另一套金融策略。

### 2.4 InstructionTarget 最小字段

| 字段 | 类型 | 不变量/必要性 |
|---|---|---|
| instruction_id | Id | 与容器 key 及定时指令 id 相同 |
| source_customer_id | Id | 必须等于指令 customer_id 和源账户 owner |
| ownership | Literal[OWN,OTHER,EXTERNAL] | 必须有真实查询证据，不能把未知误标 EXTERNAL |
| target_account_id | Id|null | OWN/OTHER 必填；EXTERNAL=null |
| target_customer_id | Id|null | OWN=source owner，OTHER 与 source 不同，EXTERNAL=null |

OWN：只接受指令显式 to_account_id，目标账户必须存在于 scope、同客户、不同源。payee 指向本人仍拒绝，与 apply_transfer 一致。OTHER：payee 归源客户，target_account_id 对应真实非空且非内部客户 owner；只存 ID，不给它保证贷记，即使该客户碰巧在 scope 内。EXTERNAL：真实 payee 存在、归源客户且 target_account_id=null。

不放 name/alias/phone/account_no/bank_name/fingerprint，不把 B 的 Payee 塞入 A 的 state.payees。若 A 本次向 B 转账，B 既有定时向 C 转账，只加载 B 的源账户/义务与 C 的最小 owner/account IDs；不读取 C 的余额、版本、卡或联系人，不扩 scope、不递归锁 C。非请求客户的旧 ScheduledView.target_display 使用固定描述（本人账户/本行收款人/他行收款人），不携带联系人文字。

### 2.5 InstructionRun

字段见四表清单；内部新增 customer_id/account_id/kind 三个关系见证字段，用于数据库复合 FK 与加载时的 owner 校验，避免只凭两个独立 ID 把不同客户历史拼接。

kind 判别：SCHEDULED_TRANSFER 必须 instruction_id 非空、billing_id=null；MERCHANT_MANDATE 必须 billing_id 非空，instruction_id 可空（未找到授权也会产生被拦扣费历史）。status 四终态；InstructionRun 模型校验明确要求 **status=SUCCESS => instruction_id 非空**，同时 transfer_id 非空、code=null；其余 transfer_id=null、code 为非空稳定规则代码。保留 MERCHANT_MANDATE + instruction_id=null + BLOCKED。amount_cents 是该期实际请求额，不是扣费 cap。event_id 全局唯一，due_at 是真实周期瞬间。S3a 不插入“预计会执行”的未来 run。

成功记录的 instruction_id 必须通过现有 kind/owner/account/billing 关联校验；不要求所关联指令的**当前** status=ACTIVE。真实 SUCCESS 历史对应的授权后来变为 DELETED/DISABLED，模型与 loader 仍须保留并接受历史记录及其 last_run。这里仅证明非空来源关联和关系一致性，不能据四表追认或否定 due_at 当时的授权，也不自证后续 W5 的 grant 核验、L2 授权及事件原子执行。

last_run 仅从同 instruction_id 的真实 runs 按 due_at/id 取最大，返回 status/code/due_at；所有四终态都算 last_run。没有 run 就 null，不能从 next_run_at 减一个周期猜。billing-only run 不归到任意一条指令。未来 scheduler 的事件结果与 run 原子写入不在本组件实现。

## 3. 四张新表：完整字段、约束、父表增量

通用：ID 为 VARCHAR(256)，非空字符串；瞬间为 TIMESTAMP WITH TIME ZONE；正向金额为 BIGINT 且 0<值<=100000000；不设 now()/系统时钟默认值。下列 N=NOT NULL，?=nullable。CHECK 每个分支显式验证必要列 IS NOT NULL，避免 SQL 三值逻辑绕过。

### 3.1 merchants

| 字段 | PG / 内部类型 | 不变量与用途 |
|---|---|---|
| id | VARCHAR(256) PK / Id，N | 真实商户身份 |
| name | TEXT / str，N | 非空，不可信展示名 |
| mcc | VARCHAR / str，N | 非空类别代码，供后续闲置判定 |
| category | TEXT / str，N | 非空交易类别 |
| usage_expected | BOOLEAN / StrictBool，N | 保存真实使用预期，不由 LLM 决定 |

### 3.2 merchant_billing

| 字段 | PG / 内部类型 | 不变量与用途 |
|---|---|---|
| id | VARCHAR(256) PK / Id，N | 周期事件来源，不随授权删除 |
| customer_id | VARCHAR(256) FK customers / Id，N | 账单 owner |
| account_id | VARCHAR(256) / Id，N | 与 customer_id 复合 FK 到客户账户 |
| merchant_id | VARCHAR(256) FK merchants / Id，N | 独立商户关系 |
| amount_cents | BIGINT / PositiveObligationCents，N | 该合约实际应扣额；预留来源 |
| period | VARCHAR / BillingPeriod，N | WEEKLY 或 MONTHLY |
| anchor_day | INTEGER / AnchorDay，? | 仅 MONTHLY 非空；保留29/30/31原锚点 |
| weekday | INTEGER / Weekday，? | 仅 WEEKLY 非空；周一0 |
| time_of_day | TIME WITHOUT TIME ZONE / LocalExecutionTime，N | 上海本地执行钟面时间 |
| next_due_at | TIMESTAMPTZ / Timestamp，N | 可恢复周期的已知真实指针，与周期本地日历一致 |
| active | BOOLEAN / StrictBool，N | 商户账单独立生命周期 |
| created_at | TIMESTAMPTZ / Timestamp，N | 合约建立时间 |

CHECK 周期组合与 next_due_at 的上海本地钟面/星期/月末一致；不加 amount<=mandate.max 的跨表 CHECK，真实超 cap 账单必须能存储并由领域拒绝。索引(customer_id,next_due_at,id)。候选 UNIQUE(id,customer_id,account_id,merchant_id) 供授权绑定，UNIQUE(id,customer_id,account_id) 供历史绑定。

### 3.3 standing_instructions

| 字段 | PG / 内部类型 | 不变量与用途 |
|---|---|---|
| id | VARCHAR(256) PK / Id，N | 授权身份 |
| customer_id | VARCHAR(256) FK customers / Id，N | 源客户 |
| kind | VARCHAR / 两项 Literal，N | SCHEDULED_TRANSFER/MERCHANT_MANDATE |
| amount_type | VARCHAR / Literal[FIXED,DUES]，N | 由 kind 决定 |
| status | VARCHAR / InstructionStatus，N | ACTIVE/DISABLED/DELETED |
| from_account_id | VARCHAR(256) / Id，N | 同客户源账户复合 FK |
| payee_id | VARCHAR(256) / Id，? | 定时 payee 目标，同 owner 复合 FK |
| to_account_id | VARCHAR(256) / Id，? | 定时 OWN 目标，同 owner 复合 FK，不能等于源 |
| merchant_id | VARCHAR(256) FK merchants / Id，? | 代扣商户，定时必须 null |
| billing_id | VARCHAR(256) / Id，? | 代扣账单，与 owner/源账户/商户完整复合 FK |
| amount_cents | BIGINT / PositiveObligationCents，? | 仅 FIXED |
| max_amount_cents | BIGINT / PositiveObligationCents，? | 仅 DUES |
| recurrence | VARCHAR / Literal[PERIODIC,AS_PER_DUES]，N | 分别绑定 kind |
| period | VARCHAR / Period，? | 仅 FIXED；DUES null |
| anchor_day | INTEGER / AnchorDay，? | 仅 FIXED MONTHLY |
| weekday | INTEGER / Weekday，? | 仅 FIXED WEEKLY |
| time_of_day | TIME WITHOUT TIME ZONE / LocalExecutionTime，? | 仅 FIXED 非空 |
| next_run_at | TIMESTAMPTZ / Timestamp，? | FIXED ACTIVE 必填；DUES null |
| end_at | TIMESTAMPTZ / Timestamp，? | 包含式授权截止；不改 billing 生命周期 |
| priority | VARCHAR / InstructionPriority，N | 保留四值，不默认隐式排序 |
| grant_scope_hash | VARCHAR(64) / str，N | 原授权作用域哈希，禁止空串，保留审计证据 |
| source_plan_id | VARCHAR(256) / Id，N | 原计划逻辑身份，供未来 grant 恢复；种子用独立 fixture 清单中的真实种子计划标识 |
| created_at | TIMESTAMPTZ / Timestamp，N | 业务建立时间 |

CHECK 精确落实 §2.3 两分支及周期组合。索引(customer_id,status,next_run_at,id)。代扣 billing_id 的 ACTIVE 部分唯一，保证同账单最多一条 ACTIVE 授权，同时允许 DELETED 历史留存；重授权不在本期实现。

候选 UNIQUE(id,customer_id,from_account_id,kind) 供 run 的来源类型/owner/account 一起绑定；UNIQUE(id,customer_id,from_account_id,billing_id) 供带授权的商户 run 绑定到同一账单。

source_plan_id 是跨 L2/L3 的逻辑关联，当前 bank 无 plans 表，现有 transfers.plan_id 也是无本地 FK 的同类引用。不得伪建父表或要求种子计划已存在于 plan_executions；将其作为非空 typed ID 保存，未来事件路径核验已提交结果/outbox。此处不假称已经实现 grant 恢复。

### 3.4 instruction_runs

| 字段 | PG / 内部类型 | 不变量与用途 |
|---|---|---|
| id | VARCHAR(256) PK / Id，N | 不可变终态历史身份 |
| customer_id | VARCHAR(256) FK customers / Id，N | 新增关系见证，scope 查询与 owner FK |
| account_id | VARCHAR(256) / Id，N | 新增关系见证，真实扣款源账户 |
| kind | VARCHAR / 两项 Literal，N | 新增关系见证，区分定时/商户事件来源 |
| instruction_id | VARCHAR(256) / Id，? | 定时必填；所有 SUCCESS 必填；商户未找到授权的 BLOCKED 可空 |
| billing_id | VARCHAR(256) / Id，? | 商户必填；定时必须 null |
| due_at | TIMESTAMPTZ / Timestamp，N | 唯一周期瞬间及 last_run 时间来源 |
| event_id | VARCHAR(256) UNIQUE / Id，N | 同一事件只有一终态；当前无 event_jobs 父表，保存逻辑事件身份，不建假 FK/假 job |
| status | VARCHAR / 四终态 Literal，N | SUCCESS/FAILED_INSUFFICIENT/BLOCKED/SKIPPED_REAPPROVAL |
| code | VARCHAR / Code|null，? | 成功 null；失败/拦截/跳过记录真实原因 |
| amount_cents | BIGINT / PositiveObligationCents，N | 实际请求额 |
| transfer_id | VARCHAR(256) / Id，? | 成功必填、失败必须 null；关联真实 transfer |

周期唯一：定时部分 UNIQUE(instruction_id,due_at)；商户部分 UNIQUE(billing_id,due_at)，不能更换 mandate 后对同一期再扣一次；非空 transfer_id 唯一，防同账本转账复用成多次成功历史。两类来源 CHECK、状态/transfer/code 组合 CHECK 均显式拒绝缺失值；另明确增加 `CHECK (status <> 'SUCCESS' OR instruction_id IS NOT NULL)`。因此商户 SUCCESS 不能借 instruction_id=null 跳过授权来源复合 FK；无 instruction 的 MERCHANT_MANDATE BLOCKED 仍可保存，并继续校验其 customer/account/billing 关系。历史关联只要求 kind/owner/account/billing 一致，不以指令当前 ACTIVE 为约束，不因后来 DELETED/DISABLED 拒绝已有 SUCCESS 或从快照删除它；这些关系 CHECK/FK 不代表 W5 当时授权已被本组件证明。

只追加：复用 0001 已存在的 forbid_mutation()，为 instruction_runs 新建 BEFORE UPDATE OR DELETE 触发器；不给 bank_app UPDATE/DELETE/TRUNCATE。错误历史不修改、不删除，不建立可变“最新 run”缓存。due_at 超 sim_now 的终态历史在 loader 一致性校验中拒绝；本组件不预写未来结果。

### 3.5 必要父候选唯一与复合 FK 精确清单

只 ALTER 父表添加候选键，不更新任何父行：

1. accounts UNIQUE(id,customer_id)。billing(account_id,customer_id)、SI(from_account_id,customer_id)、SI(to_account_id,customer_id)、run(account_id,customer_id) 分别引用它；非空 customer_id 不能指向内部账户。
2. payees UNIQUE(id,customer_id)。SI(payee_id,customer_id) 引用它，拒绝别人的 payee；target_account_id 的真实 owner 用现有 accounts 查询核验。
3. transfers UNIQUE(id,debit_account_id)。run(transfer_id,account_id) 引用它，成功历史不能指向另一源账户/另一客户的 transfer。
4. SI(billing_id,customer_id,from_account_id,merchant_id) → billing(id,customer_id,account_id,merchant_id)。四列在代扣分支均非空，不允许只靠独立 FK 混配。
5. run(instruction_id,customer_id,account_id,kind) → SI(id,customer_id,from_account_id,kind)；run(billing_id,customer_id,account_id) → billing(id,customer_id,account_id)。
6. 商户 run 同时有 instruction_id 与 billing_id 时，run(instruction_id,customer_id,account_id,billing_id) → SI(id,customer_id,from_account_id,billing_id)，拒绝同客户另一个 billing 的授权错配。无授权时 instruction_id=null，保留真实被拒请求。

FK 默认 NO ACTION、无 ON DELETE CASCADE。nullable 关联只在明确允许的分支为空；不能利用 MATCH SIMPLE 的空值跳过必需关联。目标余额不递归进 scope。

## 4. 唯一 loader、S2 同源适配及展示投影

loader 在传入 conn 同一事务顺序读取 clock/账户/客户版本和四表；不另建 engine/连接，不读墙钟。now 与 clock.sim_now 应为同一业务瞬间；全部日历校验先转上海。读取完成前后不写 clock/data_version，纯 schema 升级不改 dataset_revision。

1. 验证 scope 非空/无重复、请求客户在 scope；逐客户存在 state_version、Customer 和自有账户，不能只像当前代码那样验证 caller。覆盖证据取实际成功查询的完整 owner 集合，与 scope.customer_ids、versions/customer IDs 一致。
2. 查询所有 `customer_id IN scope` 的 billing、SI、runs，不以 ACTIVE、窗口、caller 过滤掉历史/无授权账单。merchants 仅加载这些账单/授权实际引用的集合，不扫描无关客户。
3. 定时 payee 的查询只投影 id/customer_id/target_account_id 及目标账户 customer_id；这可在相同 conn 的 JOIN 内完成。生成 targets，不修改旧 state.payees。已有 caller Payee 数据沿用；非 caller 不读取联系人列。
4. 整体校验引用/归属/类型/金额/周期。SQL join 不用“匹配不到就丢该义务”的内连接过滤方式；需要显式确认每个引用成功。缺商户、账单、源账户、owner 或可恢复锚点就失败，禁止返回部分空快照。
5. 构造完整 ObligationSnapshot，再派生旧 instructions/subscriptions。ScheduledView.last_run 来自真实 history；SubscriptionView 的 period/amount/next_due 只取 billing，max/status 取 mandate，年费沿用 monthly*12/weekly*52；billing.active 不洗成另一种 mandate status。展示字段不成为预留事实源。

S2：`projected_shortfall(state, *, days=7)` 签名及严格仅7天保持；先要求 obligations 非 null、coverage 全部匹配，随后只消费 raw snapshot。OWN 信用腿来自 targets，OTHER/EXTERNAL 永远不保证入账。旧 state.instructions/subscriptions 即使内容与 raw 冲突，也不能覆盖 raw。保留整数 rank=3（定时）/4（商户）、同瞬间稳定顺序及原前缀峰值算法，max_outflow 继续可为负。

周期只保留一个私有展开核心：从当前 scheduled_occurrences 的校验/月末逻辑提取私有日历迭代器，scheduled_occurrences 维持现有 View 输入签名作为兼容包装，原始 billing 使用同一迭代器。不伪造 Payee 或带假 target 的 ScheduledView 来凑 merchant 调用。对 stale MONTHLY/WEEKLY 只按真实原始锚点计算未来 occurrence，不持久推进、不插 run，不宣称 scheduler 已执行历史期。

S2 现有 report 测试 fixture 需显式构造原始快照；其默认空 tuple 不能继续作为完整数据证据。scheduled_occurrences 的独立 View 单测保持；旧公开 View 的 status=str 不因此被改 schema。新 raw status 不接受 BLOCKED/COMPLETED/FAILED，分别由新模型拒绝测试覆盖，不把它们当合法授权状态。

失败行为：typed/关系异常明确抛错，缺失完整性时 S2 ValueError；生产 snapshot 不能把异常转成成功空集合。当前 HTTP 错误处理未专门将坏内部领域数据包装为统一503，这不妨碍拒绝返回快照；本组件不越界改 main.py 的公共错误处理或新增 code。真实缺失/非法样本首先用 PG 约束拒绝，raw 数据缺失则用 typed/纯函数输入测试证明拒绝。

## 5. 0003、最新 head、历史数据保护

0003 revision=`0003_obligations`、down_revision=`0002_identity`；独立明确 DDL，不调用当前 metadata.create_all，不重新执行 0001/0002。仅四新表/索引/触发器、上述父候选唯一及新表 DML/SELECT 授权。银行 app/ro 不拥有表、不获得 DDL/DELETE/TRUNCATE/禁触发器权限；新 merchants 仅 SELECT，billing/SI SELECT INSERT UPDATE，runs SELECT INSERT；bank_ro 仅 SELECT。旧账户与账本权限不放宽。

历史三文件 SHA256（本轮只读计算，后续必须相同）：

- 0001_core.py：`427a39cc00ffba19984d2e43d1c6ce45d0198da9e247eeff4980e5ffb9ef559c`
- 0002_identity.py：`12678a5f7078969e8b5a06c18a9bfde2b32933ebeba20db5c44223e2d256546d`
- schema_v1.json：`e49b26f72d583ba7579f2b4045311291e973f45f76f3cd5da36be378a352898f`

精确适配：app/db.py readiness 只接受 clock存在 AND revision==0003_obligations；test_database.py:19 当前 head 只定点更新为该精确值，保留表 owner、CREATE/TRUNCATE 拒绝、旧余额/只追加/失败 readiness 全部原断言。新增 readiness 不接受旧0002或未知 revision 的用例。

新增银行最新 PG metadata drift，不能改网关漂移测试、不能重生成历史 schema_v1。比较时需精确补足一项已知历史差异：0001 在 frozen DDL 后单独创建 accounts_current_card_fk（DEFERRABLE INITIALLY DEFERRED），当前 models 中 current_card_id 尚只是 String；测试局部 metadata clone 加上这条完全相同 FK，再与最新 PG compare_metadata，排除 alembic_version 后必须零差异。禁止泛化忽略所有 FK/CHECK/类型变化。新四表、候选唯一、复合 FK、约束和索引都在当前 metadata 与迁移中一致声明；触发器/权限另用 catalog 与实际拒绝断言验证。

旧0002升级测试不能从0001带非空intent再穿越0002（该历史迁移会拒绝非空执行表）。正确顺序：全新独立库先 upgrade 0002_identity → 使用旧兼容真实核心 seed/执行路径生成已有 posted/pending、transactions、PREPARED/COMMITTED/CANCELLED intents、evidence_history、plan_execution、operation/outbox及客户/clock版本 → 对全部旧表行值、行数、序列当前值和关键权限采集基线 → upgrade head两次 → 逐字段相等。还要验证原 prepare/query/reconcile/replay 可继续工作、旧余额与pending/审计/outbox未丢。迁移不执行任何资金/义务 seed，不碰 sim_now、版本或dataset标记。

空库同样 upgrade head两次；四表空应正常、缺 clock readiness 仍 false；装入纯测试核心后现有 snapshot 显式完整空。测试限定随机新建 tmpfs PG，不触碰长期 P1 库、无 TRUNCATE/reset/卷操作。旧 test-p1 自有 bank_p1_test 清理流程由协调者独占执行，本专项不借用该 helper。

freeze 只更新经审阅的 models.BankState `$defs` 和 obligations 属性及 v1.sha256；结构化逐项比较 read_tools/write_tools/llm_tool/其余顶级模型完全相同（BankState 的新递归定义除外），再运行 contracts-check。不能把 freeze 生成器整份输出当自动接受所有漂移的许可。

## 6. 独立 fresh PG → 真实 HTTP → snapshot → S2 验收设计

建议目录 `tests/obligations_data/`、独立环境开关 `OBLIGATIONS_DB_TESTS=1`，独立目标 `make test-obligations-data`。fixture 自行 `docker run` 锁定现有 PG16镜像、随机容器名/本地端口，显式 PGDATA 和 `/var/lib/postgresql/data` tmpfs；只清理本次容器，不操作 docker volume、固定 safebank compose 项目或现有库。未开启时默认大套件可显示专属跳过；该目标开启后必须零 skip，服务缺失应失败。

fresh PRD 义务 fixture（仅测试目录，不进 app.seed/bootstrap）：以真实 ledger/persist 开户记账，客户用 PRD 的 cus_zs/cus_ls/cus_sb/cus_ww，账户/余额严格 §2.2，sim_now=2026-10-15T09:00+08。张三四个真实 billing=3000/1500/2100/19900 分、日18/20/1/25，max=3600/1800/2520/23880；next pointers=10-18/10-20/11-01/10-25，全部06:00。妈妈每月5日100000分，next=11-05 06:00。李四两笔 billing=10-20 3000、10-22 1500，均06:00。scope/prd IDs、原始哈希/来源计划均写入真实新表；运行 history 样本也真实落 PG。fixture 名义为 PRD obligations acceptance subset，不冒充完整 baseline_v1 的150笔历史/全部基金。

真实 HTTP：在独立本地随机端口启动当前 create_app + 实际 Uvicorn listener，连接专属 PG、测试服务令牌；httpx 经真实 TCP POST `/internal/snapshot`，而非 FakeL3/替换 loader。ASGITransport 可做补充测试，不能代替这个贯穿证据。启动/停止仅独立 app，绝不 restart 长期银行服务。

核心链：先 snapshot 张三计划向王五转300000 → 确认 covered含cus_zs/cus_ww、raw四表及targets可 JSON 往返 → 真实 prepare/execute API提交该转账 → 再 HTTP snapshot并 BankState.model_validate_json → 调唯一 projected_shortfall；断言张三活期 available=300000、reserve=4500、max_outflow=295500，无其他未来入账担保。再用纯 apply_transfer 在该真实 snapshot 上分别尝试295500/295501，报告新增缺口0/1；金额模板/hint将来直接消费同一报告，本组件不实现W2/W3调用。

补充矩阵（均是拟运行，不是结果）：

1. 已有缺口：独立数据变体可用1000、视频+音乐4500 → shortfall3500/max_outflow=-3500；真实freeze执行前后同源报告保持3500，证明不裁负值、不用已有缺口阻止保护动作。
2. 取消语义：测试准备事务仅把视频 mandate状态设DELETED（在隔离库按clock→客户锁并增客户版本，不声称实现取消工具），billing仍active且行内容/历史保留；重取snapshot后预留1500，缺口500。原授权hash/source仍在。billing active=false、mandate ACTIVE 的独立组合也不预留，不洗改授权状态。
3. 本人互转：真实定时源/目标两本人账户，前缀借贷两腿；多个同刻义务rank3/4及稳定ID；只 OWN 贷记。
4. all-scope与隐私：张三本次向王五形成scope；给王五增加独立外部及第三客户目标的真实定时义务，确认王五出账不丢、第三客户不进versions/customers/accounts，张三state.payees仍只有张三资料；把第三客户联系电话/别名设置为哨兵，整个snapshot响应不得包含哨兵。单客户snapshot不出现王五义务。
5. 完整空/未知：真实scope四表确空→非null容器、covered精确、reserve0；将同JSON obligations删除/null、遗漏一个covered customer或目标映射→S2明确拒绝；空默认旧View不能补证。
6. 周期：真实PG存monthly31 anchor从1月/闰2月/非闰2月分别取7天窗口；用同一纯展开核心验证短月后恢复31。WEEKLY0/6、左开右闭、end_at等时刻/提前1微秒、stale但有原锚点、多offset等价瞬间；坏anchor/时分/所有权/缺billing首先PG拒绝，不允许误算零。无恢复锚点raw输入明确失败。
7. 真实last_run：成功及各种失败/拦截/跳过终态、乱序插入按due_at取最新；billing-only拦截不伪归某指令；UPDATE/DELETE/重复event/重复周期/跨客户source/错billing/错transfer均拒绝。
8. 快照一致性：测试连接持有REPEATABLE READ READ ONLY，在它首次读取后另一受控事务更新billing并增版本；同conn唯一loader应只见旧版本+旧账单，下一真实HTTP snapshot见新版本+新账单；不接受混合代际。读取前后DB全量不变、原有只读事务写入拒绝。

F1 专属组合矩阵（模型与真实 PG 均新增，以下仍是拟运行）：

| 组合 | 模型校验 | PG/快照预期 |
|---|---|---|
| MERCHANT_MANDATE、有效同客户账单/账户、instruction_id=null、SUCCESS、同源真实transfer_id非空、code=null | 必须拒绝，不能仅靠有效 transfer 放行 | INSERT 必须被 SUCCESS 来源非空 CHECK 拒绝，不能写成真实成功历史 |
| 同一有效账单的 MERCHANT_MANDATE、instruction_id=null、BLOCKED、transfer_id=null、code=MANDATE_NOT_ACTIVE | 接受真实无授权拦截记录 | 独立有效样本可 INSERT 并经 HTTP snapshot 保留；仍校验 customer/account/billing，不伪归任何 instruction 的 last_run |
| SUCCESS 有真实非空instruction_id且kind/owner/account/billing/transfer关联全部一致；其后该授权分别变为DELETED、DISABLED | 两种当前状态下都接受并保留 SUCCESS | 两个独立场景中状态变更后重取 HTTP snapshot，历史原行与关联指令仍在、真实 last_run 保留；不以当前ACTIVE追认或否定历史，也不宣称证明当时W5授权 |

独立目标建议拆成 `test-obligations-models`（contracts/纯函数及原S2回归）与 `test-obligations-data`（真实tmpfs迁移/权限/升级保留/loader/真实HTTP，零skip）。正式CI在现有p1-postgres顺序任务中加专项一行；不重排原CI，也不同时启动compose控制。协调者后续仍亲自串行make up/test-p1/test-p1-concurrency/test-p1-consistency/test-integration，组件owner只跑获准专项、lint/test/contracts-check。

## 7. 审查输入与待协调项

没有发现必须扩大需求、改外部契约、修改历史迁移或重开架构设计才能解决的真实阻塞。报告中的内部类名、run新增三个关系见证字段及候选键是本轮提交协调者确认的具体最小模型；正式接口审查确认后才代码。

两个现有逻辑引用的边界已明示而非假完成：source_plan_id 当前无本地plans父表，event_id当前无event_jobs父表；采用现有L3同类逻辑ID约定，不伪建第五表，真正grant恢复/事件核验留后续组件。若协调者要求本组件把这两列变成真实外键，将与“四新表、无scheduler/领域写”的已批边界冲突，须另行裁定；当前提案不需要该扩大。

本轮未修改AGENTS/PRD/任务书，遵守仅临时报告输出限制；协调日志请协调者统一记录。等待字段审查、明确实施授权与最新main同步指令。
