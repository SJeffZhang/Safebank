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
