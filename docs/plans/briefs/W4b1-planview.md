# W4b1 PlanView and Approval Binding Implementation Plan

> 用户已指定本机Claude Code CLI / DeepSeek V4 Pro实现与独立评审、自主推进；协调者只任务/文档/亲验/发布，不编业务代码。不用自身subagents或再次等待用户确认。

**Goal:** 完成W3b需要的确定性PlanView/content_hash及短事务审批创建/HMAC验证库，真实临时PG证明归属/单活动审批/并发与不可变绑定。

**Architecture:** W4b拆为本组件纯面板+审批库与W4b2运行资源/确认/OTP/查询接口；两者都合入才开始W3b完整G4。当前组件只有显式配置的库API，缺配置fail-closed，不宣称生产G6/OTP/G4已接通。旧origin/feat/l2-execution3856a4c仅只读参考，不整体合并/覆盖main。

**Tech Stack:** 当前锁定Python3.12/Pydantic/SQLAlchemy/PostgreSQL与标准hmac/hashlib/secrets，不增依赖。

**Spec:** PRD7.10/9.5 PlanView与G6-G10/G17/9.1、ADR008/009、W4-execution.md、W3-W4-integration-order.md、wave1-common、已合W3a实际结果槽；后续执行仍W4c。

## Global Constraints / ownership

只写services/gateway/app/approval/**（本次不做OTP/confirm/endpoints）、tests/gateway/w4b1/**、docs/progress/W4b1-planview.md；Makefile末尾追加test-w4b1、CI p1-postgres追加该真实专项且不改job名（协调者明确批准）。api/**、execution/**、locks/state/tables/migrations/runtime/config/main、schema/共享contracts/bankcore/L3/依赖/PRD/AGENTS冻结。

金额严格整数分，显示只整数或Decimal，不float。数据取ctx.steps、snapshot、真实policy registry/Decision，不相信rationale、不调用LLM/L3。只有明确注册但未实施领域写可构造展示fixture；不得模拟实际领域执行成功。不读key文件/长期DB/全栈，不push/PR/merge/派agent/reset/TRUNCATE长期数据。新fixture只能独立tmpfs PG。

## Design decisions and review focus

- 保留build_view(ctx: EvalContext)->tuple[PlanView,str]与async create_for_evaluation(conn,*,plan,evaluation,view)->str|None签名。新增configure(approval_hmac_key:bytes,*,profile:str,wall_now:Callable[[],datetime])及binding/verify_binding内部API供W4b2显式接线；必须验证>=32byte key、合法服务端profile、aware wall，缺配置503；绝不以policy_version替profile、复用服务token、默认/临时随机key或从请求读取key/profile。进度准确列生产runtime尚待W4b2。
- 内容hash覆盖可见业务语义：plan/sid/origin、步骤/顺序/目标姓名+尾号/本行标识/金额/memo/频次/业务到期日期、逐步余额、风险/不可逆/揭示全文和版本、risk_ack/otp要求；字典规范JSON。审批/evaluation ID、展示status、审批expires_at_wall及hash本身属可变envelope不进内容hash，分别受HMAC绑定；否则等价复演必变hash。业务到期日仍在steps内必须进hash。不加入disclosure_token或任何新wire字段；如实码发现与PRD实质冲突，先写提案不另造协议。
- HMAC绑定customer/sid/plan_id/plan_hash/evaluation_id/versions/clock_version/policy_version/服务端profile/view_hash/expires_at_wall；constant-time比较。错任何字段拒，不跨计划/客户/sid移用。
- 同sid新审批使旧PENDING以及未执行CONFIRMED失效为SUPERSEDED；已有EXECUTING/UNKNOWN及CLAIMED不能被重批/释放，要按既有state/ADR008拒绝或保留，禁止改共享state逃过保护。单会话串行锁+现有唯一索引兜底；相同evaluation重复创建不制造第二审批/延长期限。
- 面板任何必要实体/标签/金额/余额/揭示缺失应明确fail-closed，不能仅显示裸ID、假余额或空解释；DENY/REPLAN或被禁预演的显示不应因无快照崩溃，要明确有效边界。
- view_json应持久化最终真实approval_id/evaluation_id/status/期限，与审批行/评估行一致，不能把apv_unassigned当真实审批交付。prepare/execute与授权消费均不做。

### Task 1: Deterministic view and semantic hash

Files: approval/planview.py及必要纯helper；tests/gateway/w4b1/test_planview.py。

- [ ] 红测：transfer已有收款人/本人互转的目标名称/尾号/标识、整数金额、memo、余额before/after；卡/定时周期起止/AA参与人/基金揭示字段形状；policy title/irreversible/otp来源；missing entity/disclosure拒；两次仅evaluation/approval/status/审批有效期变化hash相同，任一可见目标/金额/顺序/业务日期/余额/风险/揭示版本和全文变化hash不同；大整数不失真、input不变。
- [ ] 跑.venv/bin/pytest tests/gateway/w4b1/test_planview.py -q确认红。
- [ ] 实现build_view及content_hash，纯函数不写DB/HTTP。临时未分配approval标识仅内存构建，service必须替换为真实ID；不默认成功或暴露新字段。
- [ ] 定点绿并本地commit。

### Task 2: Explicit keys and transactional binding

Files: approval/service.py及必要HMAC配置helper；tests/gateway/w4b1/{test_binding.py,db/test_creation.py,conftest.py}。

- [ ] 红测：缺/短key与无效profile/naive wall拒；真实PG plan/evaluation/session/view归属一致创建PENDING并落view_json；所有绑定字段逐一篡改/伪MAC拒；ALLOW返回None且不造交互审批、DENY/REPLAN拒/零插入；5分钟上限且不超过原话/session有效期限；同sid不同计划串行新建使旧审批SUPERSEDED、不同sid不误伤、相同evaluation幂等不延时；撤销/过期/未授权客户拒；已有live job/claims不释放不重批。
- [ ] 独立tmpfs PG真实红测，禁止修改历史迁移/索引；读取已有state保护语义并调用必要只读/原语，不重写CAS。
- [ ] 最小实现，caller持有事务，库不commit/rollback、不跨HTTP；行锁/唯一索引应证明并发同sid不会两个活动审批，错误不留部分行；verify_binding重算而非信任传入MAC。stored view与approval/evaluation真实ID和内容hash一致。
- [ ] 真实PG绿并本地commit。

### Task 3: Acceptance

- [ ] make lint/test-gateway/test-gateway-db/test/test-w4b1/test-w3a/test-w2/test-obligations/contracts-check全部实际跑；W4b1真实PG专项零skip/xfail，actionlint由协调者亲跑。
- [ ] 进度写实际红绿/配置API/真实PG与内存fixture边界、未实现runtime/G6/G7/G9/G10/G17/OTP、后续W4b2→W3b→W4c；不得自称P2完成。
- [ ] 本地commit署名DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>，冻结停止，不发布/合并/开始W4b2；独立审与协调者亲验通过才发布。

自审：这是现有PRD审批先决库的最小可独审交付，所有业务、协议与凭据架构不变；冻结共享文件若实际缺口先提案，不能扩大范围。
