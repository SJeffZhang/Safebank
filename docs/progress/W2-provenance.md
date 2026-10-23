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
