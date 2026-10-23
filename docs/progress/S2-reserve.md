# S2 共享预留纯函数进度

## 范围与边界

- 分支 `feat/l3-reserve`，起点 `8555bf0c82860a11765d73f2a59f8e72d6a5de7a`。
- 仅实现 `bankcore.reserve`、包导出、独立测试、Make 目标与本报告；不修改 contracts、L3、gateway、迁移或 PRD。
- 输入必须是完整的客户账户与义务投影；当前生产快照尚未加载这些义务，生产接入仍由 S3 阻塞。空元组单元样本不证明客户没有义务。
- `projected_shortfall(state, *, days=7)` 仅支持严格整数 7；其他值显式拒绝。SubscriptionView 缺少月度原始锚点，不能据此支持任意天数。
- `scheduled_occurrences(instructions, start, end)` 使用 ScheduledView 的原始锚点，可供后续 30 天承诺计算复用。
- weekday 经协调者确认采用 `datetime.weekday()`：周一 0 至周日 6；范围外或与 next_run_at 不一致时报错。MONTHLY 保留原 anchor_day，短月截到月末，下一月恢复原锚点。
- 所有带时区输入先归一化为 PRD §0.1 的 Asia/Shanghai，再校验本地时间/星期/月度锚点、展开周期和计算窗口；同瞬间的其他 offset 不拒绝。报告 due_at 统一为上海业务时间。
- MONTHLY 商户 next_due_at 早于 sim_now 时显式报错，因为现有 SubscriptionView 没有原始锚点；等于 sim_now 按左开窗口排除。WEEKLY 可精确按 7 天推进，保留原执行时刻。
- 未运行 Docker、真实 PostgreSQL、L3 业务 HTTP、LLM 或外部服务；完整单元回归包含 S1 的本地回环 HTTP 监听测试，不构成银行业务集成验收。

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

### S1 同步与最终退出

- S2 检查点：`e45849467afb38038ab8de6a105a139ec36172cd`。
- 同步的 main：`691cf880866076a0db9a5a77b752a785364560f6`。
- 本地 merge：`46c9d216f5b152e793e5442fd4b23d6d4774df4e`。唯一冲突为 Makefile 末尾追加，保留 `test-shared-runtime` 与 `test-reserve`；没有修改 S1 实现。
- 首次普通沙箱 `git add` / `git commit` 因 worktree index.lock 写权限失败，随后使用获准的提升权限完成本地暂存与提交。没有 push、创建 PR 或合并远端 main。

同步后对同一代码树实际运行：

| 命令 | 结果 | 退出码 |
|---|---|---|
| `RUFF_CACHE_DIR=/private/tmp/sbg-reserve-ruff-cache make lint` | All checks passed；158 files already formatted | 0 |
| `PYTEST_ADDOPTS='-o cache_dir=/private/tmp/sbg-reserve-pytest-cache' make test`（普通沙箱） | 6 failed / 295 passed / 54 skipped；六项均为 S1 本地监听被 `127.0.0.1` bind EPERM 拒绝 | 2 |
| 同一 `make test` 命令（获准本地回环端口的提升环境） | **301 passed / 54 skipped**，5.39s；未修改测试或业务代码 | 0 |
| `PYTEST_ADDOPTS='-o cache_dir=/private/tmp/sbg-reserve-pytest-cache' make test-reserve` | **73 passed** | 0 |
| `make contracts-check` | PASS: contracts v1 match frozen schemas | 0 |
| `git diff --check` 与 `git diff --cached --check` | 无空白错误 | 0 |

六项普通沙箱失败均来自 `tests/gateway/shared_runtime/test_servers.py::test_real_listeners_and_requests_stop_before_runtime_disposal`，参数为 `clean`、`runtime_failure`、`cancel`、`cancel_twice`、`timeout`、`cancel_during_shutdown`；提升环境的全量复跑已覆盖并通过这六项。

## 最终文件清单（相对 main）

- `packages/bankcore/src/safebank_bankcore/reserve.py`：唯一预留算法及三个不可变返回类型。
- `packages/bankcore/src/safebank_bankcore/__init__.py`：必要公开导出。
- `tests/bankcore/test_reserve.py`：110 项独立纯函数/场景验收（最初 73 项，加 R1 的 37 项时区回归）。
- `Makefile`：末尾 `test-reserve` 目标，保留 S1 目标。
- `docs/progress/S2-reserve.md`：本报告与限制。

## 评审修复轮 1：R1 业务时区（完成，待原评审定点复核）

- 修复起点：`8a7d6ddd4b71dacadf1df20b1f8e29846cbbb794`。
- 独立评审指出：同一瞬间 `2026-10-18T06:00:00+08:00` 与 `2026-10-17T22:00:00Z` 在两个接口中的结果不一致。按 PRD §0.1，应按 Asia/Shanghai 校验业务日历。
- 根因已核对：旧 `_schedule_time` 直接读取输入 offset 下的 `.time()`、`.weekday()`、年月日；后续月度递推又沿用该 offset。因此合法 UTC 表示被拒绝，错误的上海本地时刻反而可能按 UTC 时分被接受。
- 单项修复：用 `_business_time` 在读取日历字段之前统一转 Asia/Shanghai；同时规范化 start/end/end_at、sim_now、商户 next_due_at 和返回 due_at。金额、排序、预留算式、所有权、API 签名和 7 天限制均未改动；没有修改 S1、gateway、L3 或 contracts。
- 新增 37 项回归：两接口覆盖 ONCE/WEEKLY/月末闰年/年末递推/跨年一日锚点，每组接受 UTC、-07:00、+14:00 等价瞬间；显式断言上海时间结果和同源输入结果相同。混合 offset 验证左开右闭窗口、end_at 正好到期或提前 1 微秒；负例继续拒绝错误上海时刻、星期和月度锚点。输入深度不变也有断言。

实际红绿与退出证据（2026-10-23）：

1. 先只加测试，`.venv/bin/pytest tests/bankcore/test_reserve.py -q --tb=short`（同前 PYTEST_ADDOPTS）：**34 failed / 76 passed**，退出 1。新增 34 项复现误拒绝/误接受；另 3 项拒绝负例原本即通过。
2. 实现归一化后同命令：**110 passed**，退出 0。
3. `ruff format` 限定两个修复文件：1 file reformatted / 1 unchanged，退出 0。首轮 `make lint` 因测试中 8 处 `timezone.utc` 违反 UP017 退出 2，定向改为 datetime.UTC；下一轮因新增 UTC 导入排序违反 I001 退出 2，排序修正后通过。未放宽 lint 规则。
4. 样式修正期间两次完整 `make test` 实际均为 **338 passed / 54 skipped**，退出 0；最后代码冻结前重新完整验收如下。

| 最终退出命令 | 实际结果 | 退出码 |
|---|---|---|
| `RUFF_CACHE_DIR=/private/tmp/sbg-reserve-ruff-cache make lint` | All checks passed；158 files already formatted | 0 |
| `PYTEST_ADDOPTS='-o cache_dir=/private/tmp/sbg-reserve-pytest-cache' make test`（提升环境，允许 S1 本地回环监听） | **338 passed / 54 skipped**，4.20s | 0 |
| `PYTEST_ADDOPTS='-o cache_dir=/private/tmp/sbg-reserve-pytest-cache' make test-reserve` | **110 passed** | 0 |
| `make contracts-check` | PASS: contracts v1 match frozen schemas | 0 |

本轮仅修改 reserve.py、test_reserve.py 与本报告；本地提交后冻结，交由协调者和原评审者复核，不 push。生产接入及真实 L3 集成限制仍与下节相同。

## 待协调者同步

- 根 AGENTS.md §3/§4 由协调者统一记录，避免超出本任务允许修改文件。
- R11 现有 days 范围为 1–30，而本接口只支持 7；在 S3 补充精确商户周期投影前不得将其他窗口静默降为 7 或猜月度锚点。
- 现有公开 AccountReserve.max_outflow_cents 使用非负 Cents；本报告保留负整数。上层必须先处理非正上限，不能直接把负数塞进该公开模型，也不能将其变成可执行正金额。
- 当前无法从 BankState 默认空 instructions/subscriptions 判断是否加载完整；S3 必须保证快照完整后才解除生产接入阻塞。
- 本任务只新增纯函数，没有触及 L3 写路径；未运行金融并发、持久化一致性或真实 L3 数据库/HTTP 集成测试。上述 54 个跳过项不能计为通过。
