# S2 共享预留纯函数进度

## 范围与边界

- 分支 `feat/l3-reserve`，起点 `8555bf0c82860a11765d73f2a59f8e72d6a5de7a`。
- 仅实现 `bankcore.reserve`、包导出、独立测试、Make 目标与本报告；不修改 contracts、L3、gateway、迁移或 PRD。
- 输入必须是完整的客户账户与义务投影；当前生产快照尚未加载这些义务，生产接入仍由 S3 阻塞。空元组单元样本不证明客户没有义务。
- `projected_shortfall(state, *, days=7)` 仅支持严格整数 7；其他值显式拒绝。SubscriptionView 缺少月度原始锚点，不能据此支持任意天数。
- `scheduled_occurrences(instructions, start, end)` 使用 ScheduledView 的原始锚点，可供后续 30 天承诺计算复用。
- weekday 经协调者确认采用 `datetime.weekday()`：周一 0 至周日 6；范围外或与 next_run_at 不一致时报错。MONTHLY 保留原 anchor_day，短月截到月末，下一月恢复原锚点。
- MONTHLY 商户 next_due_at 早于 sim_now 时显式报错，因为现有 SubscriptionView 没有原始锚点；等于 sim_now 按左开窗口排除。WEEKLY 可精确按 7 天推进，保留原执行时刻。
- 未运行 Docker、真实 PostgreSQL、HTTP、LLM 或外部服务；不得把纯函数结果描述为真实集成验收。

## 组件门禁

### 1a：定时义务展开（完成，33 项）

- 输入：完整 ScheduledView 序列与带时区的 `(start,end]`。
- 输出：按 `(due_at,3,id)` 排序的不可变定时借记义务。
- 依赖：仅现有 contracts 与 Python 标准库。
- 失败条件：月末锚点漂移、窗口边界错计、忽略 end_at、无效 ACTIVE 周期被静默丢弃。
- 验收：`.venv/bin/pytest tests/bankcore/test_reserve.py -q`（先观察缺失实现失败，再实现）。
- 测试覆盖 ONCE/WEEKLY/MONTHLY、30 天展开、月末/闰年、end_at、相同时间稳定 ID 排序、非法周期元数据和重复 ID 拒绝。

### 1b：账户预留报告（完成，36 项）

- 输入：完整 BankState，输出账户前缀峰值预留、缺口、可流出上限与有序义务。
- 验收覆盖真实账单金额、ACTIVE 过滤、商户执行时刻、本人互转与未知所有权拒绝、输入不变、未来入账不担保。
- `AccountProjection` / `ShortfallReport` / `Obligation` 为冻结 dataclass；accounts 返回 MappingProxyType。账户按 ID 排序，义务按 `(due_at,rank,id)` 排序；本人互转两腿使用同一来源 ID，稳定保留借记在前、贷记在后。
- 本人互转仅显式 to_account_id 且源/目标同属一个客户时贷记目标。payee 解析到本人账户显式报错，与既有 apply_transfer 的约束一致；跨客户收款不贷记本投影。
- 预留是负前缀和的峰值；缺口与总缺口保留已有数值，max_outflow 不裁成零。不读取工资、未来基金到账或 pending credit 来作担保。

### 2：PRD 场景与共享消费（完成，4 项）

- 验证 600000 − 300000 − 4500 = 295500 分；计划前后缺口比较与保护动作不扩大缺口；输入顺序不改变结果。
- 只输出数学报告，不返回 L2 的 SCHEDULED_SHORTFALL 策略违规。
- 完整样本含四笔真实订阅账单与窗口外的妈妈定时转账。调用既有 apply_transfer 向王五转出 300000 后，共用报告得 295500 上限；再转 295500 缺口为 0，多转 1 分缺口为 1。
- 既有缺口 3500 分下调用既有 apply_card 冻结成功，前后缺口仍为 3500；投影已取消视频授权后的缺口降为 500（取消的实际持久化尚待 S3）。
- W2 模板与 W3 hint 可读取同一报告的 accounts[account_id].max_outflow_cents，本测试不实现第二套预留公式；未声称 gateway 已完成接入。

## 实际命令与结果

日期：2026-10-23。以下是同步 S1 前、本分支原起点上的实际结果。

1. 首轮 `.venv/bin/pytest tests/bankcore/test_reserve.py -q`：25 failed，原因均为 scheduled_occurrences 尚未实现；另有 2 条 worktree 内 pytest 缓存目录写权限警告。
2. 设置 `PYTEST_ADDOPTS='-o cache_dir=/private/tmp/sbg-reserve-pytest-cache'` 后重跑：25 passed，无缓存警告。后续 pytest 门禁均使用此设置。
3. 添加周周期测试：4 failed / 29 passed（尚未支持 WEEKLY），实现后 33 passed。
4. 添加账户报告测试：30 failed / 33 passed；再加入本人/跨客户转账测试后 36 failed / 33 passed（报告接口尚未实现）；实现后 69 passed。
5. 自查既有 apply_transfer 的本人 payee 约束，调整一项预期并先观察 1 failed / 68 deselected，修正 reserve 后 `make test-reserve`：69 passed。
6. 加入四项 PRD/消费测试，`make test-reserve`：73 passed。
7. 仅对 S2 的三个 Python 文件执行 `ruff format`：3 files reformatted，退出 0。
8. `RUFF_CACHE_DIR=/private/tmp/sbg-reserve-ruff-cache make lint`：All checks passed；147 files already formatted，退出 0。
9. `PYTEST_ADDOPTS='-o cache_dir=/private/tmp/sbg-reserve-pytest-cache' make test`：256 passed / 48 skipped，退出 0。跳过项依赖真实数据库或专用开关，不能算通过。
10. `make contracts-check`：PASS: contracts v1 match frozen schemas，退出 0。

本地检查点提交、同步 origin/main 与同步后完整退出结果待下节记录。

## 待协调者同步

- 根 AGENTS.md §3/§4 由协调者统一记录，避免超出本任务允许修改文件。
- R11 现有 days 范围为 1–30，而本接口只支持 7；在 S3 补充精确商户周期投影前不得将其他窗口静默降为 7 或猜月度锚点。
- 现有公开 AccountReserve.max_outflow_cents 使用非负 Cents；本报告保留负整数。上层必须先处理非正上限，不能直接把负数塞进该公开模型，也不能将其变成可执行正金额。
- 当前无法从 BankState 默认空 instructions/subscriptions 判断是否加载完整；S3 必须保证快照完整后才解除生产接入阻塞。
- 本任务只新增纯函数，没有触及 L3 写路径；未运行并发、持久化一致性或真实数据库/HTTP 集成测试。
