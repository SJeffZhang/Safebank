# W2 读代理、来源与意图范围

日期：2026-10-23。分支 `feat/l2-provenance`，原基线 main `8555bf0`。协调者另同步仅测试隔离修正 `614ec9b`。本文件为可评审的本地检查点；**W2 尚未达到完整验收**，下述 1 项 xfail 和真实依赖缺口没有算作通过。

## 已实现

- G2 从 `request.state.caller` 注入 customer_id，核对 session 和每个可验证 ID；只经 L3Client 发读请求。READ_ARGS / READ_RESULTS 冻结契约重验，不伪造银行数据。真实 L3 拒绝沿既有 Code 返回。
- 读取前后各取一次快照，比较 versions / clock_version；不稳定读取返回 `DEPENDENCY_UNAVAILABLE` 503，失败不生成来源、不写 ledger。来源为 src + ULID；具体 JSON Pointer 按 trust.yaml 标注，持久化 read_records。
- ① 重验 PlanDraft（包括 model_construct 等绕开初验后重新进入边界的情况），覆盖步数、DAG、来源重复、额外字段；AppFormPlanRequest 沿冻结模型校验。无请求的 W0 空流水线探针保留。
- ② 经 bank_state 核所有权；使用 bankcore.references.resolved_steps 解析确定性引用及同指纹复用；ctx.steps 元数据只经 registry 计算。逐步影子状态只复用 bankcore.apply_plan，不写业务算术；未读实体 `ENTITY_UNSEEN`，resolver=agent，evidence_refs 为建议读工具。no_op 由实际 bankcore 预演结果承载，不塞进 args。
- ③ utterance 区间及规范化值、ACTIVE/有效期、read 指针/版本/模拟观察时刻、前序实体依赖、服务端 hint/action 范围/有效期/一次使用均核验。internal `_utterance_snapshot` 可证明已见 ID，但不能当公共 read 参数来源。可信余额不等于金额许可；memo/alias 仅接受 utterance/action/null。来源声明删改不能洗掉复制的不可信值。
- 固定派生模板：cancelled_monthly_fees 对同计划前序、去重且 ACTIVE/MONTHLY 的取消指令重算，并约束本人每月定时互转；available_less_7d_charges 仅申购，消费协调接口 `reserve(state, *, days=7).accounts[id].max_outflow_cents` 的显式替身。默认生产 stage 未接预留依赖时 503，不用余额代替。
- 中文/英文注入启发式只升红；备注直接注入、账号复述、新增污染目标后转出的 A2 单测变体被拒。新污染目标转出 DENY 的 `evidence_refs=(target_fingerprint,)`，供 W3 G4 持久化 24h cooldown。W2 不写 Decision 或工作流表。
- ④ 用 W1 AuthorizationInputs 的 SCOPED/ACTIVE normalized_scope 匹配源账户、目标指纹、金额多重集合；默认唯一 CHECKING；资金元组不可复制或交换配对，申购等非转账动作也不可重复扩权，不能遗漏要求的保护动作。周期/首次时刻/截止精确绑定，派生金额 0 占位必须先有③已核验的同模板值。谓词通过当前可信数据重新求值，不能信历史候选 ID 集合。
- 既有 hard DENY 时③④仍写自身结果槽，但不让后续不可用依赖掩盖原拒绝；由 W3 继续聚合并审计。
- 随机改写 intents/rationale 30 轮结论不变；伪造 read/utterance/hint/action 的来源声明只失去原本有效的来源许可。

## 已协调的协议与裁定

- `NormalizedScope.fields` 用现有自由字典：非转账准确参数直存，`predicates:{idle_subscriptions:[ids],robust_products:[ids],suspicious_cards:[ids]}`，以及 `derived_template`；不改 schema。本人目标指纹为 PRD §7.2 的 `int:<account_id>`。
- 统一资源为 app.state.db / l3 / wall_now，不自行创建连接池。W0 main 尚未接资源的生产缺口由 S1 串行处理；本分支未改 main/config。
- PRD §7.6 优先于任务书“保护性动作只升黄”的泛化表述：额外未授权保护动作仍 `INTENT_SCOPE_EXCEEDED` REPLAN/user；已授权保护动作保留注册表基础等级，不新增放行码。
- ReadEnvelope 不加 clock_version。来源核对 owner versions 与 observed_at==sim_now；G2 夹读另查 clock_version。reset 必须撤销全部旧 session/read 上下文；真实 PG 测试已证明同一观察时间的旧 record 在 session 撤销后不可重放。W5 仍须验收 reset 本身兑现撤销。
- G2 没有 plan，读竞争用 503，不伪造 `STATE_CHANGED.details.decision`。

## 未完成与真实证明范围

1. **memo/alias 检测已接线**：共享 S1d 合入后，本分支已使用 `MEMO_SENSITIVE_CONTENT / RISK_YELLOW` 落实 PRD §7.5，移除原严格 xfail。2026-10-23 本次组件实际红绿、五退出与覆盖范围见文末；此前章节中的“缺码/xfail”描述属于历史检查点，不再是当前缺口。完整 W2 仍待以下生产依赖联调。
2. **S2/S3**：预留数学函数尚未落入本基线，完整义务投影亦未装载。测试回调只证明 W2 比对值和用途，不证明生产预留算法或真实资金安全。默认 reserve=None 显式阻塞；S3 完整数据门禁后再由 W2 接生产函数。
3. 真实 L3 P1 仅 5/18 读：get_accounts、list_payees、get_transactions、get_cards、get_profile。snapshot 的 instructions/subscriptions/products/holdings/orders 仍默认空。订阅/理财/异常谓词和派生场景的当前证明为合成状态或 FakeL3，未声称真实联调完成。不把缺失集合当客户无义务。
4. BankState v1 没有 split 集合，G2 对非空 get_split_status.request_id 暂无法证明所有权，保守 NOT_FOUND；等待共享实体投影/只读归属能力。其他未实现 L3 工具正常转发拒绝，未造成功。
5. W1 尚未合入；AuthorizationInputs 目前 fixture。需合入后真实 load_for_plan → ①–④ 和 app_form 内部 scope 联测。typed “每月1号”缺首次时刻由 W1 FORM_REQUIRED，不用种子06:00猜授权；SCOPED 模板测试是已补齐时刻的内部 fixture。
6. W3 须消费上述污染指纹 DENY 证据写 cooldown；W2 阶段不做跨步骤状态持久化。真实 L3 get_profile 的 risk_valid_until/date 缺陷已由协调者登记冻结区修复。
7. 未测试真实 L3 业务/完整演示、银行并发、浏览器或 BFF；未运行 make up/down/test-p1，未派生 agent，未 push/PR/merge。

## 组件与最终实际验证

- 契约/实体首轮 8 fail → 8 pass；G2 首轮缺实现导入失败 → 5 pass；来源首轮 6 fail → 6 pass；意图首轮 7 fail → 7 pass。后续测试暴露 owner 子集版本比较、提示范围读取、重复非转账金额、遗漏保护动作、hard DENY 被依赖错误掩盖和 ULID 格式，均加回归后修复。
- 初次 worktree 写入被沙箱拒绝；之后正常 require_escalated。首次 lint 有格式/导入错误，限定 W2 白名单格式化后修复。
- 较早 PG 验收使用既有 fixture（会产生匿名 Docker 卷）；收到协调者暂停指令时，预先启动 shell 中测试已进入执行，均按 fixture 正常结束，没有删除任何卷。随后协调者同步 `614ec9b` 的 tmpfs 修正，之后 PG 验收不再创建测试卷。
- **最终代码复验（含 hard DENY 回归）**：`make lint` 通过（161 files formatted）；`make test-gateway` 119 passed / 3 skipped / 1 xfailed；`make test-gateway-db` 17 passed；`make test` 238 passed / 51 skipped / 1 xfailed；`make test-w2` **58 passed / 1 xfailed**（3 个真实 PostgreSQL 测试实际执行，不是 skip）。全部命令 exit 0，但 xfail 是明确未完成项，不能称 W2 全验收。
- 单元命令中的 3 个 W2 DB skip 已被 test-w2 的真实 PG 运行覆盖；全量其余 skip 为既有需要独立银行环境的测试，未冒充通过。

## 本次文件清单

修改：Makefile（仅末尾 test-w2/.PHONY）、services/gateway/app/api/tools.py、pipeline/contract.py、pipeline/entity.py、pipeline/provenance.py、pipeline/intent.py。

新增：services/gateway/app/taint/{__init__,entities,records,text}.py；services/gateway/policies/{trust,injection_patterns}.yaml；tests/gateway/w2/{__init__,test_contract_entity,test_read,test_provenance,test_sources,test_derived,test_intent,test_predicates,test_llm_fields_ignored}.py；tests/gateway/w2/db/{conftest,test_read_ledger,test_saved_sources}.py；本进度文档。

冻结文件未由 W2 修改；测试 fixture 的 `614ec9b` 为协调者单独同步的前置提交。提交前自审：只写 ctx.steps / provenance / intent / findings，没有 Decision 赋值、业务数据库写入、额外依赖或密钥文件。

## 独立评审修复轮 1（1699091 之后）

已全文读取协调者的 `docs/reviews/L2-W2-provenance.md`：规格 FAIL、质量 REQUEST_CHANGES，开放 2 P1 + 1 P2。本轮只修改 W2 所有权内的三项，**没有混入 W1 来源链 / W3 FORM 适配或 S1 同步**。

### 修复内容与回归证据

1. **P1 明确参数不可删除**：`_fields_match` 区分原作用域未指定（可以 KEEP）与明确指定后被计划改为 null（拒绝）。卡渠道 `online=False,atm=False` 不可仅保留 online；卡双限额同理。明确 amount_cents 也不能被旁边的 derived_template 标记覆盖。两项卡回归首次 `2 failed`；金额回归首次 `1 failed / 22 passed`，均实际复现后修复。合法完整参数和未指定保持的原有用例仍通过。
2. **P1 非转账一对一授权**：每个 scope 按 ActionKind 形成单次可消费许可；`instruction_ids` 和当前闲置订阅集合展开为每实体一项。用完整二分匹配避免 scope 重用，也避免贪心匹配误拒“宽谓词 + 精确产品”这种存在完整配对的组合。产品 A/B、两卡、两条取消指令均覆盖“重复 A 遗漏 B”拒绝及合法反序正例；首次 `3 failed / 2 passed`。当前谓词重新求值一次后供匹配消费，不信历史候选列表，不给选购谓词增加购买次数。保留既有 X2 固定派生组合正例，并新增闲置集合 + 单独明确订阅的合法组合和缺项/重复反例。
3. **P2 读取实际客户范围**：读取快照必须包含 caller customer 的版本；与计划 expected 比较时，要求读取实际出现的每个客户都在 expected 中且版本相等，忽略只存在于计划、未参与该次读取的对方客户键。读取前后仍要求整个版本键集合和值严格相同，且 clock_version / sim_now 相同。稳定 `{cus_a:0}` 对 `{cus_a:0,cus_b:0}` 首次实际 `1 failed / 14 passed`，修复后通过。owner 版本变化、owner 缺失、expected 缺失实际读取客户、夹读缺键/增键/改值、expected 时钟变化和夹读时钟变化均继续拒绝 503。

新增 `tests/gateway/w2/test_review_round1.py` 共 23 项回归；使用现有冻结契约、已解析步骤 fixture 与 FakeL3 validating transport，不声称完成真实订阅/理财后端联调。生产仅改 `app/api/tools.py`、`app/pipeline/intent.py`；另更新本进度文档。没有修改 EvalContext、schema、契约、bankcore、L3、表/迁移、注册表、依赖或 Decision 逻辑。

### 本轮实际命令结果

- 红阶段：两项卡删除失败、三项重复授权失败、稳定不同客户范围读取失败，以及明确金额被模板覆盖失败，均见上述具体计数；先复现后修复。
- 分组件：`pytest test_review_round1.py test_intent.py` 12 passed；加入一对一匹配后关联 intent/predicate/derived 20 passed；读取范围修复后 review/read 21 passed；合法谓词与派生组合阶段关联测试 25 passed。
- 首次完整退出停在 lint：新增测试的循环闭包 B023 与本地 import 排序；限定新测试修复后重跑。未将该次失败算通过。
- **最后一轮（最终代码、同步 main 之前）**：`make lint` 通过，162 files already formatted；`make test-gateway` **142 passed / 3 skipped / 1 xfailed**；`make test-gateway-db` **17 passed**；`make test` **261 passed / 51 skipped / 1 xfailed**；`make test-w2` **81 passed / 1 xfailed**。五项命令连续执行 exit 0，3 项 W2 真 PG 测试实际执行。
- memo 缺 `MEMO_SENSITIVE_CONTENT` 的严格 xfail 仍然是**未完成要求**；S2/S3 与真实 W1/W3 联测等此前限制仍保留，没有宣称 W2 全验收。

### 跨 owner 最小语义方案（协调者已裁定，留待独立组件）

- **A 路径**：scope_ids 保持本次已加载 ACTIVE utterance IDs，授权单元身份与 hint/action 的源记录 ID 分开；W2 后续从这些 DB utterances 的 kind/source_id/parent_ids 核源链。必须 `source.used_by == 本次 adopted utterance.id`，同 sid/customer/epoch、有效期和 ACTIVE 条件成立。未采纳源不能使用；绝不能接受任意 used_by，也不能仅把 scope_ids 当 hint ID 查。
- **F direct FORM**：只有 `origin=app_form` 的受信适配可使用已核验 act IDs 作为内部 ref/utterance_ids，statuses 对应 ACTIVE，scope_ids 为这些表单动作；source.used_by 仅允许空或同 plan。W3 使用 W1 的 scope_from_form 统一生成完整转账/周期作用域，不只填写 fields。A 令牌不能借该适配绕过 utts。
- 上述来源链与 FORM 修复按协调者要求**另做独立后续集成组件**，本轮代码保持原状，未用宽松放行掩盖跨分支错配。
- main 已有 S1 PR #4，但协调者最新要求本轮提交后冻结工作树/git，待其亲自复跑和原评审读取后再通知同步；当前没有 merge/cherry-pick 新 main。

## 来源 / claim 语义集成组件（修复轮 1 批准之后）

### 基线与固定需求

按协调者授权，将 `origin/main a9d72cf49f518fbb2bd89d2e666f0d0e1196a4c9`（含 S1 PR #4 与 S2 PR #5）合入本分支，merge 为 `d580c956929c9c6639347ccfd060d25c8fa13ac1`。唯一冲突是 Makefile 尾部追加目标，保留 test-w2、test-shared-runtime、test-reserve 三者，没有覆盖共享配方。本节覆盖前文“尚未同步 main / S2 尚未落入基线”的旧状态；S2 数学函数已存在，但完整生产数据门禁与 W2 接线仍等待 S3，默认 reserve=None 的明确阻塞保留。

已完整读取协调根工作树 `docs/plans/briefs/wave1-common.md` 的 A/F 及澄清补充、PRD §7.2/7.4/7.5 对应修订；采用协调者明确裁定：

- A 的 scope_ids、utterance_ids 始终是本次已加载的 ACTIVE root utterance IDs，受同 sid/customer 的 assistant JWT utts 限制。hint/action 来源 ID 只是证据，不是 claim 身份。
- 从持久 utterances.kind/source_id/parent_ids 追溯。root ref 与持久行的类型、原文、scope、parent_ids、parse_status、echo 标记及创建/过期时间必须一致；session 未撤销且 epoch 匹配。每个 parent 必须同 session（因此同 owner）、同 epoch、ACTIVE、未过期，创建不得晚于子节点，子节点不得延长父节点期限。父节点可处于原始缺槽的 parse_status，但不能形成新的执行作用域。
- 防环；最大 64 个节点、最大 32 层，遇缺失/重复 parent、跨链或畸形链一律整条证明失败。source 必须同 sid、已生效且未过期，created 不晚于采纳节点，节点期限不超过 source；source.used_by 精确等于链上实际采纳该 source 的 utterance.id，不接受未采纳、任意非空 used_by 或 plan ID。
- parent 链只供 typed 原文 span 或已采纳 hint/action 取证，绝不把 parent.normalized_scope 加进 ctx.authorization；最终计划仍只匹配 root 完整 scope。无效链即使配合其他合法 read 参数，也在④以 INTENT_SCOPE_EXCEEDED / REPLAN,user 拒绝。
- CLARIFICATION 的 `[{field,entity_id}]` 仅还原所存选定槽值，要求 parent_utterance_id 在采纳节点 parent_ids 内、槽值与该节点完整 scope.fields 一致，拒绝重复字段（包括重复同值）和冲突；不能作为 amount 等完整 FORM 授权。
- F 直接 FORM 独立要求 credential-derived Caller.F / origin=app_form / app scope，并以已核验 act IDs 构造内部 ref、scope_ids、ACTIVE statuses。source.kind 必须 FORM，used_by 为空或同 plan，精确匹配 fields、ActionKind、创建/过期时间；不写假 utterances 行。A 即使 JWT utts 包含 act ID 也不能借用适配。

### 实现、边界与文件

新增 `services/gateway/app/taint/authorization.py`：只读、有界的来源证明加载器；修改 `pipeline/provenance.py` 只消费此持久证明，移除无 caller 时直接信 loaded typed span 的旧路径；修改 `pipeline/intent.py` 对无效证明作完整授权门禁。只写本阶段结果与 findings，不写 claims、Decision、session 或来源记录。

新增 `tests/gateway/w2/db/test_source_lineage.py`（46 项真实 PG）：合法采纳 hint/FORM、root/parent 原文回溯、parent 上已采纳 hint、澄清选择、无假原话 F FORM、同 plan 重放，以及未采纳/错误 used_by、跨 sid/customer/epoch、撤销、过期/失效 parent、JWT 不含 root、source ID 冒充 claim、root ref 篡改、无关原话、环/深度/节点上限、重复/冲突澄清槽、A 冒充 F、畸形时间、parent 未来创建、parent scope 不可扩成第二笔授权、无 caller 和坏链④门禁的拒绝。

修改 `tests/gateway/w2/db/test_saved_sources.py`：移除旧的“未采纳 hint 可由 scope_ids 直接授权”错误夹具，等价安全性质由新的 A 已采纳链与 F FORM 测试覆盖；保留 reset 撤销后同 sim_now read 重放拒绝测试。修改 `tests/gateway/w2/db/test_read_ledger.py`：因 main 中 S1 已 fail-closed 依赖尚未合入的 W1 tokens.check_revocation，明确以签名兼容 test-only substitute 隔离 G2 HTTP / SQL 持久化；此测试不证明真实 W1 撤销检查。新增 `tests/gateway/w2/conftest.py`：只有非 db 的孤立阶段测试注入 loaded-evidence 替身，db/ 用例全走真实证明加载器；生产不含测试回退。另修改本进度文档。共 8 个组件文件，未改冻结文件、W1/W3 实现、迁移、契约、L3 或依赖。

W3 适配输入需求已报协调者：复用 W1 `utterance/service.py:scope_from_form(kind,args,bank)`，kind 取 registry、args 为 WRITE_ARGS 校验后的 JSON、bank 为同次受信 BankState（提供 payee 指纹），不能仅填 scope.fields；内部 UtteranceRef.kind=action / text=None / parent_ids=() / parse_status=SCOPED / act ID / 数据库创建及过期时间，AuthorizationInputs 同步 act IDs 与 ACTIVE statuses / 当前 session epoch；ctx.identity 必须保持真实 Caller.F。A 则保留真实 loader 的 root refs 和 JWT-bound utterance IDs。

### 实际红绿与退出命令

- 先补真实 PG 回归：合法 adopted hint/action 两项实际 `2 failed / 4 passed`，修复持久 source.used_by / root ID 查证后 `6 passed`；扩展 parent 等用例到 `30 passed`。
- 时间边界实际 `2 failed / 41 passed`：直接 FORM 的畸形时间抛 ValueError、活 parent 创建晚于 child 仍获信任；修复后来源/读/reset 共 `45 passed`。
- 无 caller 的 loaded typed span 回退实际 `1 failed / 43 deselected`；移除生产回退并将孤立阶段替身限定到 tests 后通过。
- 无效 parent / 无 caller 在④仅看 root.scope 的路径实际 `2 failed / 44 deselected`；加入相同持久证明门禁后通过。期间一次关联测试因测试自己的连接已关闭，实际 `1 failed / 125 passed / 1 xfailed`；修正测试连接生命周期后完整复验，没有把该次失败计为通过。
- **最终代码五退出均 exit 0**：`make lint` 通过（178 files already formatted）；`make test-gateway` **187 passed / 54 skipped / 1 xfailed**；`PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db` **17 passed**；`make test` **416 passed / 102 skipped / 1 xfailed**；同 PATH 下 `make test-w2` **126 passed / 1 xfailed**。test-w2 中 **48 项真实 PostgreSQL 测试实际执行**（新来源链 46 + G2 1 + reset 1），不是 skip；其他命令的 DB skips 不计为通过。
- 最终 diff 自审及 `git diff --check` 通过；所有临时 PostgreSQL 使用既有随机端口 / 唯一容器 / tmpfs fixture，没有删除或重建数据卷；未跑 make up/down/test-p1，未派生 agent、未 push/PR。

### 明确未完成

本组件只证明 W2 对真实持久来源数据的校验；直接 PG 造数没有调用真实 W1 G3，跨 W1 load_for_plan → W2 → W3 G4 / replay 的端到端采纳、claim 与重放仍待 W1/W3 合入后的真实联测。S1 生产缺失 W1 revocation hook 仍 fail-closed；G2 fixture substitute 不能算生产身份验收。memo/alias 缺 `MEMO_SENSITIVE_CONTENT` 的严格 xfail 仍为**未满足要求**，等待共享枚举补缺后落地；真实派生及谓词生产数据 / reserve 数据完整性待 S3。未声称 W2 全验收或全部六场景真实后端齐备。


## S1d 后 memo / alias 黄级检测组件（2026-10-23）

### 基线、范围与行为

协调者已将 S1d 与最新 main 同步到本分支；本轮起点为 `50c1bdb732872ba43fecdca86d8401c77e7c0872`，工作树起始干净。只补本任务的可独立推进部分，不将本节称为完整 W2 生产验收。

- `ProvenanceStage` 对 memo（transfer 与 AA）和 alias 的 ≥8 位数字或金额格式追加 `Code.MEMO_SENSITIVE_CONTENT / FindingEffect.RISK_YELLOW`，绑定具体 step_id。数字继续复用现有 `numbers()`；金额匹配复用 `normalize()` 做 NFKC/空白归一化，覆盖数字+元/人民币/CNY/RMB、人民币/CNY/RMB+数字、¥/￥及大小写形式。仅识别敏感文本，不解析/计算金额，不改阈值、共享 Code 或 registry。
- 来源核验顺序和结果保持：合法 utterance/action 可证明 user；read（包括 trusted read）仍不是 memo/alias 许可，保留 `TAINTED_SENSITIVE_ARG / DENY`；缺失或无效 utterance 来源仍保留 `UNKNOWN_SENSITIVE_ARG / REPLAN,user`。warning 只追加，不替换或降级这些 finding。
- 原严格 xfail 变为普通测试；新增 `test_memo_sensitive.py` 75 项，覆盖 transfer/AA memo/alias，普通中文/7位与8位边界/全角和空白/各金额格式、null/空串、read taint/unknown 来源和已保存 action 来源。action 测试只替代证明加载结果，测试生产阶段消费，不作为真实 W1 采纳链验收。

### 实际红绿与完整退出

- 先只修改测试：`.venv/bin/pytest tests/gateway/w2/test_memo_sensitive.py tests/gateway/w2/test_provenance.py -q` 实际 **58 failed / 21 passed**（0.40s）。失败均缺精确黄级 finding；既有来源 DENY/REPLAN 与普通文本正反例断言先行成立。
- 实现后同命令 **79 passed**（0.24s）；再补 3 项保存 action 的兼容覆盖并限定格式化，最终同命令 **82 passed**（0.26s）。
- 本轮最终代码五退出均 exit 0：
  - `make lint`：ruff check 通过，**196 files already formatted**。
  - `make test-gateway`：**317 passed / 302 skipped**（5.99s）。
  - `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db`：**17 passed**（2.16s）。
  - `make test`：**552 passed / 350 skipped**（6.60s）。
  - `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-w2`：**202 passed**（5.80s），**零 skip / xfail**；包含原有 **48 项真实 PostgreSQL** 来源链/读账本/reset 用例（`pytest tests/gateway/w2/db --collect-only -q` 核实48项），本轮并未跳过它们。
- 默认 gateway / 全量入口的既有 DB/环境 skip 不算通过；W2 的48项 PG skip 已由上述专项实际执行覆盖。`git diff --check` 通过。

### 本轮文件、限制与待决

本轮仅修改 `services/gateway/app/pipeline/provenance.py`、`tests/gateway/w2/test_provenance.py`、新增 `tests/gateway/w2/test_memo_sensitive.py`、更新本进度文档。未修改 AGENTS（由协调者维护）、冻结契约、共享 Code、registry、W1/W3/W4文件、CI、Makefile、L3、迁移或依赖。

保留真实缺口：本基线尚无真实 W1 G3 / load_for_plan / revocation hook 联调，G2 的既有 fixture substitute 仍不能证明生产鉴权；原48项真实PG造数只证明持久来源校验。S2纯函数已存在，但 S3 完整义务数据与谓词/派生场景生产投影仍未交付，默认 `reserve=None` 继续显式阻塞，未用 FakeL3 代替生产能力。W3整决策保留YELLOW和W4非空原文展示/view_hash绑定仍需各owner真实联测。本轮未运行真实L3完整业务、完整演示、浏览器/BFF、银行并发或 replay。

未运行 make up/down/test-p1，未删除或重建任何Docker数据卷，未派生agent，未push/PR/merge；仅本地提交冻结，交协调者亲自复跑与独立审后处理检查点及完整W2交付。


## W2a：真实 W1 身份/原话接入与 memo P2（2026-10-24）

基线 `24a6fdb`（已合并 W1 PR #13 / main `79dc943`，保留 memo 与 S3a F1 报告）。只做 W2a，未动 W2b / default reserve（S3a 仍在修复未合入）。修改仅限 W2 所有权文件与 `tests/gateway/w2`，未改 W1/冻结区/迁移/契约/L3/依赖/AGENTS/PRD。

### memo P2（先红后绿）

- 红：先改 `tests/gateway/w2/test_memo_sensitive.py`，`make` 下单跑该文件实际 **30 failed / 69 passed**（裸符号 6×3、DENY 2×3、UNKNOWN 2×3 均如预期多出 `MEMO_SENSITIVE_CONTENT`）。
- 修复 `services/gateway/app/pipeline/provenance.py`：`_MONEY_PATTERN` 第三分支由 `[¥￥]` 改为 `[¥￥](?=[0-9])|[0-9][¥￥]`（裸符号不升黄，符号邻近数字仍升黄）；memo/alias 黄级仅在 `trust == "user"` 时追加，DENY/UNKNOWN 来源不再被黄 finding 覆盖。
- 绿：`test_memo_sensitive.py + test_provenance.py` 100 passed；补 `报销1¥`/`报销￥1` 后 99 passed。

### 真实 W1 联测

- 删除 `tests/gateway/w2/db/test_read_ledger.py` 的 `fixture_revocation` monkeypatch，改用真实 W1 runtime（`tests/gateway.w1.db.support.harness`：真实 PIN 登录、app+assistant JWT、真实 `check_revocation`、数据库会话）。断言：G2 真实 HTTP 读 200 + read_records 持久化；助手令牌跨 sid 404；跨客户 args 422；logout 后同请求 401 `UNAUTHENTICATED`（真实 check_revocation，不再抹平）。
- 新增 `tests/gateway/w2/db/test_real_w1_integration.py`（8 项真实 PG，走真实 G15/G3/G19/G20 → `load_for_plan` → ②③④，L3 用契约 FakeL3 并显式登记）：typed 正常金额/目标 trust=user 且 intent matched；同名澄清 G19 action + G3 合槽（payee/amount 双来源 user、root 唯一 claim、action id 与 parent 仅证据）；hint 实际采纳 used_by 可证明、未采纳/错误 used_by 拒绝；typed parent span 追溯；跨 sid/customer 404；改口后旧 utts `AUTHORIZATION_CONSUMED` 且状态 SUPERSEDED；logout 后 `load_for_plan` 401；失效 parent 使整个来源证明失败并阻断 intent。

### 实际退出命令（连续、exit 0）

- `make lint`：ruff check 通过，**224 files already formatted**。
- `make test-gateway`：**1880 passed / 469 skipped**（8.19s）。
- `make test-gateway-db`：**17 passed**（2.38s，真实临时 PG）。
- `make test`：**2115 passed / 517 skipped**（8.83s）。
- `make test-w2`：**234 passed**（8.82s），**零 skip / xfail**；含原 48 项真实 PG + 本组 8 项真实 W1 联测。

`git diff --check` 通过；未运行 make up/down/test-p1/test-integration，未删除或重建任何 Docker 数据卷，未 push/PR/merge。actionlint 由协调者另跑。

### 待决 / 未完成

- W2b（default reserve 真实生产接线）是后续组件，须 S3a 合并后同步 main 再启动；当前 `reserve=None` 显式阻塞不变。
- 完整 W2 尚未全交付：W3 聚合 Decision、W4 原文展示/view_hash、真实 L3 业务/演示/replay 仍未联调；不得自称 W2 全验收。


## W2a 评审修复轮 1（2026-10-24，REQUEST_CHANGES）

协调裁定保留 PRD 已审「黄 finding 与 DENY/REPLAN 并存」语义，不批准额外 trust gate。只修：

- **撤销 `trust == "user"` 门禁**（`pipeline/provenance.py`）：memo/alias 黄级恢复为只要 `numbers()` 或 `_MONEY_PATTERN` 命中就追加，与来源 DENY/UNKNOWN 平铺并存，不覆盖、不降级；**裸 ¥/￥ 邻近无数字的正则修复保持**。
- **恢复两处并存断言**（`test_memo_sensitive.py`）：`test_sensitive_read_text_keeps_source_deny_alongside_yellow` 与 `test_sensitive_text_without_valid_user_source_keeps_replan` 重新断言 DENY/REPLAN 与 `MEMO_SENSITIVE_CONTENT/RISK_YELLOW` 并存。
- 先恢复断言实际红测：`test_memo_sensitive.py` **12 failed / 87 passed**（暴露当前 gate）；删 gate 后 `test_memo_sensitive.py + test_provenance.py` **106 passed**。
- P2：`test_real_w1_integration.py` docstring 更正为 P1 实现子集 5/18 读、7/14 写，并明确本轮来源联测 L3 仍为契约 fake、不宣称完整 L3 联调。

未改 DENY 优先级、共享 Decision、PRD 阈值或独立评审报告；未提交协调者 WIP 任务书；未做 W2b / default reserve。


## W2b：真实完整义务快照与默认预留接线（2026-10-24）

基线 main `eea02a6`（S3a #15 / W2a #16 已合）+ 纯协调文档 `ff244bb`，分支 `codex/w2b-reserve-integration`。只做本组件，未实施 W3/W4/义务业务写；前端/BFF 暂停。

### 实现（仅 W2 白名单 + 协调批准的两处 CI/Makefile）

- `services/gateway/app/pipeline/provenance.py`：
  - `__init__` 默认 `reserve = projected_shortfall`（唯一 bankcore 纯函数），`None` 表示「无测试覆盖」而非依赖不可用；显式测试注入保留。
  - 新增 `_reserve_max_outflow`：`projected_shortfall` 的 `ValueError`（obligations None / coverage 不完整 / 无目标账户）转 `DEPENDENCY_UNAVAILABLE 503` fail-closed；目标账户缺失用 `.get()` 判 None 转 503，不再裸 KeyError；`max_outflow_cents` 原样返回、不 clamp 负值；未用宽 except。
  - `cancelled_monthly_fees` 增补：去重（`len(cancellations) != len(selected)`）与「目标本人储蓄」校验（`_is_own_savings`，type=SAVINGS 且同 customer）；既有账户/ACTIVE/MONTHLY/跨账户/缺失来源校验保持。
- `Makefile`：`test-w2` 仅追加 `OBLIGATIONS_DB_TESTS=1`。
- `.github/workflows/checks.yml`：`p1-postgres` 在 `test-obligations` 后追加 `make test-w2`，job 名未改。

### 测试（先红后绿）

- 新增 `tests/gateway/w2/test_reserve_default.py`（4 项）：默认实例走真实 bankcore（300000−4500=295500 匹配 → derived、295501 → unknown）；obligations 未加载 / coverage 不完整 / 未知目标账户 → 503。红：`test_default_instance_uses_real_bankcore_projection` 实际 **1 failed / 3 passed**（旧 `reserve=None` 503 暴露）；绿后 4 passed。
- 新增 `tests/gateway/w2/test_derived_cancelled.py`（4 项）：重复 cancel 步骤、目标非本人储蓄、WEEKLY、跨账户订阅均 INTENT_SCOPE_EXCEEDED + unknown。
- 新增 `tests/gateway/w2/db/test_reserve_integration.py`（1 项真实 HTTP+PG）：真实 bank tmpfs PG（install_core+install_obligations）→ 真实 L3 uvicorn HTTP → 真实 prepare/execute 300000 转账 → snapshot（available=300000）→ gateway PG read_records + 默认 `ProvenanceStage` → 295500 derived、295501 unknown；第二次转账 bump 版本后旧 read ledger 拒绝（unknown）；`UPDATE clock` 前移模拟时刻后旧 read ledger 拒绝（unknown）。L3 走真实 bankcore，未用 fake reserve。
- `tests/gateway/w2/db/conftest.py` 引入 `obligations_db` 会话级 fixture（复用 S3a 临时容器，未复制生产模块、未往长期 cus_a/b 种子装义务）。

### 实际退出命令（连续 exit 0）

- `make lint`：**246 files already formatted**。
- `make test-gateway`：**1888 passed / 470 skipped**（8.02s）。
- `make test-gateway-db`：**17 passed**（2.08s，真实临时 PG）。
- `make test`：**2139 passed / 530 skipped**（8.56s）。
- `make test-w2`：**243 passed，零 skip/xfail**（11.75s；含真实 W1 联测、48+ 项真实 gateway PG、本组件真实 bank PG+HTTP）。
- `make test-obligations`：**30 passed**（4.76s，1 条既有 FK 环 SAWarning）。
- `make contracts-check`：**PASS**（契约 v1 无漂移）。

`git diff --check` 通过；未运行 make up/down/test-p1*/test-integration/docker volume/prune，未删除/重建任何长期数据卷，未读取/打印任何密钥；actionlint 由协调者另跑。

### 边界与待决

- 本组件证明阶段③派生模板对唯一 `projected_shortfall` 的真实接线；**不宣称** W3 完整 Decision、W4 执行、demo/replay 或全六场景联调（make demo 仍实现前不可用）。
- `cancelled_monthly_fees` 仍依赖真实持久化订阅视图（`state.subscriptions` 为 S3 原始 obligations 的派生视图）；未实现取消领域写，只验证来源与范围，不把未实现取消写说成执行通过。
