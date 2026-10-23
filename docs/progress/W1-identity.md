# W1 身份、会话与原话

基线 main 8555bf0；仅 feat/l2-identity；2026-10-23。按 wave1-common 白名单实现，不改冻结区。

## 组件与验收

1. PIN 与 JWT：输入客户/PIN/来源 IP，输出 app JWT 或确定错误；PIN argon2 验证，5 次错误锁 15 分钟、IP 10/min；测试正常、错误、临界、并发、撤销。
2. 确定性解析：输入 typed 与 L3 snapshot，输出 NormalizedScope/parse_status/澄清候选；测试七幕、X1–4、引号/疑问、王五王武、复述。
3. 持久化与路由：输入 request.state.caller，输出契约响应；同一事务 request_idempotent，客户锁线性化，测试真实临时 PG、伪凭证与跨客户。

## 实际命令与结果

- 已完整读取 AGENTS.md、W1-identity.md、wave1-common.md、L2-plan、gateway README、指定 PRD 与 W0 评审章节。
- 静态核对 P1 load_bank_state：返回客户 transactions（memo/counterparty），可按 sim_now 本地筛近90天；instructions 仅契约预留，真实装载仍缺（首次检查误读已更正）。

## 待决 / 未联调

- 全局 auth/deps.py 同步 JWT 校验未查撤销；需协调者接入 tokens.check_revocation。W1 路由与 loader 会主动调用，其他组件需集成。
- state.transition 不支持 pending_clarifications；请求协调者串行补 OPEN→RESOLVED/CANCELLED。实现调用唯一入口，未合入前测试明确替身。
- W4 customer_lock 基线占位；取消/登出/写会话调用固定接口，测试替身提供同签名临时PG锁，待 W4 真正联调。
- scripts/keys.py PIN 文件未挂载 gateway / 未导出环境。W1支持 DEMO_PIN_HASHES JSON（argon2哈希）或 DEMO_PINS_FILE（读取预置PIN后仅内存hash），需部署配置。jwt-private.pem 0600 Linux容器可读性须协调者验证。
- G22 frozen main/matrix 永久挂路由：W1 非demo/eval 返回404；严格按模式不注册需协调者改 main/matrix。
- 不做 frontend/BFF/L1，不运行全栈命令，不创建迁移。

### 组件1 / 解析初验

- `.venv/bin/pytest tests/gateway/w1/test_pin.py -q`：1 passed（argon2正常/错PIN/未知客户/坏hash）。
- `.venv/bin/pytest tests/gateway/w1/test_parser.py -q`：23 passed（七幕、X1–4、引用/疑问、改口/取消、精确/同音、90天/跨会话账号复述）。后续将扩展边界。
- 协调者确认 NormalizedScope.fields 约定：非转账准确字段直接保存；`predicates:{idle_subscriptions:[instruction_ids], robust_products:[product_ids], suspicious_cards:[card_ids]}`；`derived_template` 为模板名。本人转账 target_fingerprint 使用 `int:<account_id>`（与W2核对PRD），外部使用 Payee.target_fingerprint。
- 统一资源为 `app.state.db` / `app.state.l3` / `app.state.wall_now`；W1不自行创建连接池。私钥可 `app.state.jwt_private_key` 注入，否则读取 `JWT_PRIVATE_KEY_PATH`；PIN同理注入 `pin_hashes` 或环境配置。

### 持久化初验与协调裁定

- `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-w1`：初次 27 passed（含3个真实PG用例），扩展后 1408 passed（1376个矩阵组合、24个纯逻辑用例、8个真实PG接口用例）；无DB跳过。W4客户锁与clarification迁移是明确替身，不等于共享实现已联调。
- `.venv/bin/pytest tests/gateway/w1/test_auth_matrix.py -q`：1376 passed。覆盖冻结矩阵43条路由×16凭证×2客户；矩阵只负责认证，W1实际资源归属另用真实PG路径测试，其他任务的资源归属仍待各自验收。
- 更正此前静态核对：P1仅真实装载transactions历史，BankState.instructions/subscriptions/products是契约默认字段，不能视为已装载。近90天memo/counterparty已可真实取得；定时target_display历史仍待P4装载；订阅/理财/可疑卡谓词需假L3契约验收并登记真实联调缺口。
- 协调者裁定新建typed定时无首次执行时刻时不得套种子06:00：保留period/anchor_day/continuous/模板作为partial_scope，返回INTENT_UNCLEAR + FORM_REQUIRED，经FORM补first_run_at再执行。模板金额0仅为未完成表示，禁止视为任意金额通配；已通知W2。
- G3取消已不依赖L3可用；普通写登记在HTTP外锁定客户，在SQL事务外加载快照，幂等重试不再次调用L3。
- 首次ruff检查在format之前出现长行，format后已消除；另一次未提权ruff因worktree缓存写权限失败（并非代码失败），后续验收在已授权worktree提权运行。

## 本地可续接检查点（2026-10-23）

已实现 G1/G3/G14/G15/G18/G19/G20/G21/G22/G22a 的W1业务，提供 `utterance.loader.load_for_plan(conn,sid,ids,caller)->AuthorizationInputs`。签发JWT均持久化jti；PIN失败先提交计数再返回错误；typed/action/hint互斥；候选只可显式补缺；取消保持执行中/UNKNOWN状态与claim；所有共享状态调用state.transition。

### 文件清单（W1白名单）

- `services/gateway/app/auth/tokens.py`：app/assistant签发、持久jti、撤销查询。
- `services/gateway/app/auth/pin.py`：argon2配置/校验、IP窗口、PIN锁定与并发保护。
- `services/gateway/app/api/auth.py`、`api/sessions.py`：W1接口、事务外L3读取、request_idempotent与客户锁调用。
- `services/gateway/app/utterance/{__init__,models,runtime,parser,loader,lifecycle,service}.py`：原话、候选合槽、来源账本与生命周期。
- `services/gateway/policies/intent_lexicon.yaml`：动作、取消/改口、周期、拼音近形、谓词词典。
- `tests/gateway/w1/{fixtures,test_pin,test_auth_matrix,test_parser,test_models_forms,test_predicates}.py` 与 `db/{conftest,support,test_identity,test_utterances}.py`。
- Makefile **仅末尾追加** `test-w1`；本进度文件。
- 协调者另行cherry-pick `614ec9b`（tmpfs临时PG fixture），不是W1擅改共享文件；未触碰其他冻结区。

### 最终退出命令（以上代码最终状态）

| 真实命令 | 结果 |
|---|---|
| `make lint` | exit 0；ruff check通过、163 files格式通过 |
| `make test-gateway` | exit 0；1510 passed / 11 skipped（这11项为W1 PG，下一条test-w1实际启用） |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db` | exit 0；17 passed，显式tmpfs临时真实PostgreSQL |
| `make test` | exit 0；1629 passed / 59 skipped（未启用PG的正常结果；不以skip计完成） |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-w1` | exit 0；1457 passed / 0 skipped；其中1376矩阵、70其他纯逻辑、11真实PG |
| `git diff --check` | exit 0 |

其他已运行组件命令：`pytest .../test_models_forms.py -q` 27 passed；`pytest .../test_predicates.py tests/gateway/test_routing.py -q` 8 passed；解析最终 `pytest .../test_parser.py -q` 40 passed。自审增加了否定/条件句、历史事实、入款、查询、负金额、万元与非CNY排除，以及NFKC/近形候选测试。初次解析23 passed、扩展28/36后最终40；最终全量覆盖这些变化。初次ruff在format前的长行失败已修；未提权ruff缓存权限失败后提权重跑通过。

### 自审与证据范围

- 1376矩阵组合是认证矩阵证明，不宣称其他W任务的每个资源已完成归属校验；W1真实接口逐路径验证跨客户404。
- 11个PG用例：PIN次数/IP窗口、并发PIN不丢计数、JWT/续签/登出撤销、会话归属与幂等、原话替代/loader、显式typed澄清及期限不延长、普通聊天串接攻击、FORM/CLARIFICATION action一次性、无L3取消、审批作废但执行中/UNKNOWN/claim保留、hint服务端标签与一次采用。
- 谓词通过FakeL3.transport+冻结读契约验证：闲置订阅、稳健/平衡产品适当性、上一自然月至今可疑卡集合、依赖失败503；不宣称P1已实现这些真实读功能。
- 原话解析为保守固定词典；未能确定的动作/复杂金额/缺首次执行时刻进入FORM_REQUIRED，未从普通聊天或L1文本推断许可。
- W1无新增表/列需求；需要的共享修复是状态合法边与运行资源/撤销接线。
- 未运行`make up/down/test-p1/demo`，未启动CLI/额外agent，未清理或重建任何卷，未push/PR/merge。

### 仍阻塞可合并 / 后续续接

1. **共享S1补缺与W4尚未联调**：`tests/gateway/w1/db/support.py` 明确提供固定签名W4客户锁替身与pending_clarifications状态替身；当前通过不证明冻结共享实现已补齐。S1合入main后需同步分支，去掉pending状态替身并验证全局认证撤销；W4合入后换真实customer_lock验证取消/执行线性化。
2. 全局auth依赖接入 `await tokens.check_revocation(conn,caller,now=...)`；必须与只验JWT的矩阵单测区分。
3. main需注入 `app.state.db/l3/wall_now`，可设置 `settings/jwt_private_key/pin_hashes`；PIN环境与私钥Linux容器读权限须部署接线验收。W1不自行创建池。
4. G22非DEMO/EVAL处理器404已实现；严格“不注册路由”仍需协调者改冻结main/matrix。
5. P1尚未装载指令target_display历史与订阅/产品等数据，真实L3谓词/指令复述需后续实现。当前近90天交易memo/counterparty真实可用。
6. hint范围收缩由W3生成端保证，W1仅从L2持久hint还原，不接受按钮文字；合入W3后验证完整生产链。

下一步按协调者要求停在本地可续接提交，等待亲自复跑和独立评审，不自行扩大范围。


## S1真实接线组件检查点（7250105之后）

协调者已亲自复跑7250105五退出且原review_w1定点APPROVE，七项问题全关闭。本阶段按新授权同步`origin/main`的S1 merge 691cf880，本地合并提交`d71784c`；只有Makefile尾部测试目标冲突，保留test-w1和test-shared-runtime两方配方。已完整读协调树wave1-common来源addendum：A路径scope_ids仍为ACTIVE utterance IDs，source.used_by仍为采纳utterance ID；本阶段不改来源协议。

### 改动与小组件验收

- `tests/gateway/w1/db/support.py`删除pending_clarifications状态替身，使用真实S1 `state.transition`；通过真实`GatewayRuntime`给build_public_app集中安装资源。仅保留W4 customer_lock的明确PG advisory-lock替身。删除后`PATH=... GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w1/db/test_utterances.py tests/gateway/w1/db/test_identity.py -q --tb=short`：11 passed in 5.99s，真实澄清RESOLVED/CANCELLED及原身份API通过。
- `services/gateway/app/utterance/runtime.py`不再请求期读取私钥文件或自建wall clock；缺已安装密钥/非callable或无时区时钟统一503。`api/auth.py`不再请求期从环境加载PIN或demo settings；集中资源缺失503。无新连接池，无共享runtime/config变更。
- 新`test_w1_runtime_resources.py`：私钥环境fallback、缺clock/空clock/naive clock、demo环境fallback等5例，修前5 failed，修后5 passed in 0.42s。
- 新`db/test_s1_runtime.py`：缺集中PIN/私钥即使环境中有有效配置也不能签JWT，真实PG修前2例均错误返回200，修后503且auth_sessions行数不增加；真实登出后F `/audit`和A `/plans/evaluate`在非W1 handler前401。仅此登出场景沿用已披露W4锁替身，auth.check_revocation与global caller_dependency均真实。
- `test_real_s1_runtime_loads_secrets_once_and_enforces_persistent_jti`完全不装锁/auth/state替身：真实gateway_runtime从0600文件加载Ed25519和PIN、建立真实PG资源、双app共享资源；活跃期实际调用契约FakeL3 snapshot；删除secret文件并改变请求期环境后仍用启动时材料登录；签名合法但未登记jti、DB已过期jti、已撤销sid均在shared dependency拒绝；退出后L3 client已关闭。该路径不调用需要W4的会话创建/登出，session与assistant持久种子由测试明确写入。
- `.venv/bin/pytest tests/gateway/w1/test_runtime.py -q --tb=short`为新文件重命名前的红绿命令。`PATH=... GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w1/db/test_s1_runtime.py -q --tb=short`最终4 passed in 3.59s；初次全局撤销测试错误假定共享stub为501，实际应为503+固定stub消息，按已读源码修正测试；追加snapshot断言曾错误假定state包装，按冻结BankState顶层accounts修正（1 failed/3 passed后4 passed）。不将这两次测试假设错误算生产缺陷。

### 最终五退出与失败记录

| 实际命令 | 输出（最终exit0） |
|---|---|
| `make lint` | All checks passed；178 files already formatted |
| `make test-gateway` | 1620 passed, 64 skipped in 4.51s |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db` | 17 passed in 3.33s |
| `make test` | 1739 passed, 112 skipped in 5.29s |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-w1` | 1569 passed in 22.06s；0 skipped（58真实PG） |

首次lint因新增注释超长失败，已修；首次gateway/all因新test_runtime.py与S1同名pytest模块冲突收集失败，改为W1唯一文件名test_w1_runtime_resources.py后两目标完整复跑通过。W1普通目标中的PG默认skip不作验收证据，test-w1明确启用并实际运行58条。未运行make up/down/test-p1，未清理/重建卷。

### 剩余替身、依赖与待决

1. **W4a真实客户锁**：当前production locks.customer_lock仍NotImplementedError且无configure。W1会话/原话/登出写路径所需真实锁未合入；现有58条W1 PG大部分仍使用唯一剩余的锁替身，不能称生产全链路通过。不cherry-pick未合入W4a，按协调者要求独立检查点冻结等W4a。上面明确无锁替身的runtime登录/读取/持久撤销路径已实际测。
2. **共享PIN配置来源差异**：S1 `_configured_pin_hashes()`调用零参W1 configured_hashes，未传Settings.demo_pins_file；serve(from_env())同源环境可用，但显式Settings路径与进程环境不一致会读错/拒启。合成探针：Settings指向存在的有效PIN文件，环境DEMO_PINS_FILE指向不存在路径，真实gateway_runtime启动503 DEPENDENCY_UNAVAILABLE（未分配DB）；首次探针漏PYTHONPATH报ModuleNotFoundError，补`PYTHONPATH=packages/contracts/src:packages/bankcore/src .venv/bin/python -`后复现成功，exit0仅表示复现脚本断言成功，不代表该配置已支持。已报协调者；协调者裁定保持configured_hashes()现有签名，后续新增configured_hashes_from_file(pins_file: str) -> dict[str, str]，由共享接线组件按Settings显式调用；本检查点不混入该新功能、不修改冻结区，完整W1验收前须关闭该错配。
3. S1b PAUSED/claims状态原语未到，不越界修改state/tables/迁移。L3业务snapshot/谓词真实装载缺口、W2/W3来源链和W4执行仍待联合真实集成。FakeL3仍为契约替身；本组件不证明真实资金写入。

文件：api/auth.py、utterance/runtime.py、W1 db/support.py、新两个W1测试文件、W1进度文档。`git diff --check`通过。只本地提交不推送，最终SHA见交付消息。


## S1c owner小组件：显式PIN文件loader（42c86f5之后）

已完整读取协调树`docs/plans/briefs/S1c-pin-settings.md`，按授权先同步origin/main a9d72cf49f518fbb2bd89d2e666f0d0e1196a4c9；本地merge 288569557c16b3ed4dae1584d39c05272a2daa61，无冲突，Makefile test-w1/test-shared-runtime/test-reserve全保留。此时S1b/W4a尚未合入。

仅修改`services/gateway/app/auth/pin.py`，新增`configured_hashes_from_file(pins_file: str) -> dict[str, str]`；显式入口只读取传入文件，不查询DEMO_PINS_FILE或DEMO_PIN_HASHES，不使用legacy全局缓存，也不缓存文件路径/文件内容。每次启动调用得到独立内存hash快照，同一路径文件被替换后下次显式调用读到新值，既有快照不受文件删除影响。

既有`configured_hashes()`固定零参签名与其兼容缓存保留。两入口共用`_validated_hashes`：客户白名单、值严格为字符串、明文PIN长度按现有Login契约1–128、Argon2库哈希与真实编码检查。不复制哈希算法；legacy DEMO_PIN_HASHES不再仅靠“$argon2”前缀判断合法性。文件缺失、目录、乱码、JSON/类型/客户/值非法均503 DEPENDENCY_UNAVAILABLE，错误不输出指定路径或PIN，也不会从有效环境secret兜底。

新测试`tests/gateway/w1/test_pin_files.py`覆盖相反环境/有效或损坏环境hash、两个不同文件、legacy缓存已填充、同一路径后续启动替换、旧快照在文件删除后仍可校验、缺失/非法文件failclosed、零参兼容。红绿证据：

- `.venv/bin/ruff format tests/gateway/w1/test_pin_files.py`：1 file reformatted。
- `.venv/bin/pytest tests/gateway/w1/test_pin_files.py -q --tb=short`：实现前17 failed / 1 passed in 0.92s；16例显式helper尚不存在，另1例复现旧encoding前缀校验接受非法hash。
- `.venv/bin/ruff format services/gateway/app/auth/pin.py`：1 file left unchanged。
- `.venv/bin/pytest tests/gateway/w1/test_pin_files.py tests/gateway/w1/test_pin.py -q --tb=short`：实现后19 passed in 1.77s（新增18+原PIN1）。

最终五退出均exit0：

| 实际命令 | 实际输出 |
|---|---|
| `make lint` | All checks passed；181 files already formatted |
| `make test-gateway` | 1638 passed, 64 skipped in 14.75s |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db` | 17 passed in 13.26s |
| `make test` | 1867 passed, 112 skipped in 16.13s |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-w1` | 1587 passed in 38.37s；0 skipped（沿用58真实PG） |

本轮只证明显式文件helper及兼容校验；尚未修改共享runtime，它仍调用旧零参loader，所以**未证明Settings.demo_pins_file整体接线已完成**。下一共享组件必须调用新函数，之后W1再用两套Settings+相反环境+真实runtime/真实PG登录做完整集成。现有58 PG并非新函数的Settings接线证明，W4锁替身仍按旧进度明确保留。没有修改runtime/config/state/tables/迁移、依赖或其他任务代码，不推送/开PR，不跑up/down/test-p1、不删卷、不派生agent。

`git diff --check`通过；本轮文件为auth/pin.py、新test_pin_files.py、W1两份进度。自审后仅本地提交并冻结，最终SHA见交付消息，待协调者复跑及独立评审。


## W4a/S1b真实锁与生命周期接线（进行中，存在共享阻塞）

以协调者留存5cf394c为干净基线，merge origin/main d5ac9ede为041546d；仅AGENTS追加日志冲突，保留双方原记录，Makefile/CI保留双方目标。已读ADR-008、S1b/W4a任务书、真实locks.configure/customer_lock/assert_lock_owner与state专用CAS。接口简报已获协调者确认：取消/登出增epoch，改口/普通新写替代只SUPERSEDED；LIVE job必须持久UNKNOWN并保留claim/evidence/attempt/cap，真实HTTP reconcile/终态archive归W4后续组件，不由本轮伪造。

删除W1/db/support全部锁替身，改为同一GatewayRuntime.db调用真实configure(max_executions)，18槽pool匹配当前runtime默认容量；仅L3仍用契约FakeL3。`PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w1/db/test_identity.py tests/gateway/w1/db/test_utterances.py -q --tb=short`：11 passed in 6.39s，原登录→session→登记/取消/登出真实PG锁路径通过。

新增db/test_live_job_revocation.py：初次完整真实PG红测19 failed / 2 passed in 9.15s，复现EXECUTING/FENCING未转UNKNOWN、PAUSED计划普通SUPERSEDED被共享guard拒绝导致原话撤销回滚、普通新写替代未作废旧审批；含无evaluation孤儿job最小场景。新增生产衔接仅在W1 lifecycle中使用真实ExecutionIdentity和mark_execution_unknown，不直接写job状态、不消费claims。普通写替代只清理旧plan/审批，不能把正在采纳的新hint/action的期限清空；明确取消/改口另做UI/clarification清理。

阶段测试：

- `PATH=... GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w1/db/test_live_job_revocation.py -q -k 'not orphaned' --tb=short`：先20 passed/1 deselected（9.80s）；补keep/hint/锁等待后23 passed/1 deselected（10.54s）；补真实登记/登出两排队顺序与legacy空证据后26 passed/1 deselected（12.41s）。这些是明确的非孤儿组件测试，**不是完整目标验收**，孤儿用例仍真实保留、没有skip/xfail。
- `... -k concurrent_registration --tb=short`：2 passed /24 deselected in 3.52s；真实pg_locks证明请求已在同客户键排队，退出无旧ACTIVE授权、epoch/撤销时间正确、job UNKNOWN/claim CLAIMED。
- `... -k orphaned --tb=short`：当前1 failed /26 deselected in 3.09s，409导致旧原话仍ACTIVE而非CANCELLED；这正是不能交付的共享原语阻塞。不能伪造evaluation/revision、直接DML或把宽松fallback计作完成；working tree不会在该风险存在时标为验收完成。
- 当前lint首跑仅新测试SQL字符串超长E501，已拆行；等待下面复验结果。

共享最小阻塞已报协调者并获确认：schema允许job存在而evaluations零行，既有ExecutionIdentity要求revision>=1且mark_execution_unknown要求latest evaluation；需要独立共享“expected latest absent”UNKNOWN CAS（及终态清理配对），让W1在同事务撤销仍可持久待对账。协调者安排S1c后原S1b owner串行补缺；W1不改state/迁移、不假造revision，待冻结新接口后消除该红测再跑原五退出。当前无新完成提交，无完整五退出成功声明。S1c runtime显式PIN文件接线也待随后同步后做真实两路径相反环境+PG登录验证。
