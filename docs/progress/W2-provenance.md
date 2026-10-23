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

1. **缺码**：PRD §7.5 规定 memo/alias 含账号或金额升黄；冻结 Code 缺 `MEMO_SENSITIVE_CONTENT`。精确测试 `test_memo_account_number_requires_precise_yellow_warning_code` 严格 xfail；生产对应处明确留空，没有滥用 UNKNOWN_SENSITIVE_ARG 或 INJECTION_SUSPECTED。需共享补缺加 Code/PRD 后接入并移除 xfail，当前不满足全部规则正反例退出要求。
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
