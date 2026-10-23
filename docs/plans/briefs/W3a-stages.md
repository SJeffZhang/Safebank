# W3a：快照、预演、工作流和决策阶段（后续组件准备）

须W2b通过亲验/独审/精确head四CI合入main后才启动。由协调者从最新main建独立worktree/分支；旧origin/feat/l2-decision 3bcbe8e只作现有代码参考，不整枝覆盖main或未经核实cherry-pick。用户已授权全自动P2闭合后继续P3，不因此跳门禁；本组件可实现相关安全逻辑，但不自称P2/P3整体完成。

先读完整AGENTS、PRD §5.0/7.7/7.8/7.9/9.5/10，wave1-common.md、W3-decision.md、W3-W4-integration-order.md、S3a与W2b验收、ADR008/009、实际main EvalContext/Stage结果槽。原W3任务中完整G4/落库/复演此次不做，不为缺失W4 build_view/create_for_evaluation引入默认成功替身后称真实接线。

## 所有权和交付

只允许services/gateway/app/pipeline/{snapshot,preflight,workflow,decision}.py、services/gateway/app/workflow/**、policies/thresholds.yaml和workflow.yaml、tests/gateway/w3a/**、docs/progress/W3a-stages.md；Makefile末尾追加test-w3a，CI p1-postgres追加该真实专项（由协调者明确允许，保持job名）。冻结pipeline/__init__.py、context/schemas/auth/state/tables/迁移/config/main、W1/W2、审批/执行/evaluation/api/plans、contracts/bankcore/L3/依赖/PRD/AGENTS。若实际结果槽无法承载批准规则，先写提案由协调者另派受控共享组件，不能复制另一套或存空结果蒙混。

- ⑤使用ctx.bank_state唯一快照，保留versions/clock_version，app_form resolved steps也可实际scope；不重复拉取，不写L3。
- ⑥用唯一bankcore.apply_plan累积预演，B1按PRD从初始快照独立逐步且仅允许结构性payee引用，不能新写金融算术。L3强制错误和L2策略分表；S3完整义务+唯一projected_shortfall七天预留，负max_outflow有符号，缺依赖fail-closed，已有DENY不能被后续503遮盖。
- ⑦按PRD工作流全规则，窗口sim/wall准确，成功历史以L3已提交ops为事实、gateway COMMITTED去重补充；BLOCKED只给拒后重试，不当成功链。RISKY_SEQUENCE顺序不限、无卡储蓄变体、近期窗口/冷静期、重复/拒后重试均明确。shadow B1只内存比较，不写业务/审计/审批。
- ⑧DENY>REPLAN>REQUIRE_APPROVAL>ALLOW，风险只升不降，evidence_refs/resolver实际指向正确工具/用户；hint仅现PRD白名单收缩，输出赵六最多295500分，不扩大金额/目标/动作。profile开关及shadow不得被请求body伪造。

## 红绿与验收

分小任务逐个先红→最小实现→绿，端到端不得只检查finding字符串：
1 同快照多步骤累计、实体引用、不变性、snapshot一次；使用真实L3 snapshot HTTP+独立PG至少证明余额/版本/义务输入，明确这不是实际G4或资金执行。
2 A1拆分升红、A3顺序互换/无卡储蓄均DENY、REJECTED_RETRY和DUPLICATE_PLAN墙钟窗口/模拟时间分离；L3 ops和gateway history去重/未来/跨客户/无效状态边界。
3 第2幕真实数据REPLAN与hint295500；SCHEDULED_SHORTFALL、负max_outflow、缺coverage/无数据/前置DENY交叉。
4 B0/B1/B2及各ablation开关按PRD真实差异，不能用过弱B1稻草人；SHADOW_B1完全无写。
5 保留同源预演/实际L3执行一致性证据，支持工具仅P1子集，尚无领域写路径不得假实现成功。

退出make lint/test-gateway/test-gateway-db/test/test-w3a（真实PG/HTTP零skipxfail）/test-w2/test-obligations/contracts-check；actionlint由协调者亲验。测试复用既有独立tmpfs PG和S3数据，不装长期业务数据，不docker卷操作。进度记所有实际命令/fake/真实边界/未覆盖，不宣称完整W3/G4通过。只本地commit、实际署名DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>；禁止push/PR/merge/派agent/up/down/test-p1*/test-integration，单命令不用 ;/&&。冻结停止交独审/亲验。W4b必须单独任务/独审/PR合入后才做W3b。
