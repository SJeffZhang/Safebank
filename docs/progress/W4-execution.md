# W4 审批与执行：独立组件检查点（部分交付）

2026-10-23；分支 `feat/l2-execution`；基线 `8555bf0` + 临时PG tmpfs修正 `0705452`。协调者要求先保存检查点并释放并发槽，等待执行状态协议的独立架构审查。**W4未完成，不满足完整退出条件，不可据此开完成PR。**

## 当前可独立验证的组件

- `approval/planview.py`：EvalContext → PlanView与内容哈希。哈希覆盖目标、金额、memo/alias、周期/日期、预演步骤/余额、风险/发现、不可逆标记和揭示版本/文本；评价ID、审批ID、动态状态和审批wall有效期由独立MAC绑定，不进入业务等价内容哈希。新收款人完整账号参与哈希，避免同尾号不同目标被视为等价。产品申购缺揭示时明确拒绝构建。标题/OTP/不可逆来自冻结注册表。
- `locks.py`：`configure(db: AsyncEngine,max_executions:int)`只绑定集中生命周期engine与Semaphore，不新建连接池；`customer_lock(customer_ids)`签名不变。独占连接、客户ID去重排序、会话级advisory锁、5秒lock_timeout、超时409、异常释放与RESET。
- `approval/service.py`：`create_for_evaluation`为黄/红创建审批，验证面板哈希/归属，锁session并替代旧PENDING/CONFIRMED；绿级返回None、approval_id仍null。HMAC绑定客户/会话/计划哈希/评价/金融版本/政策及profile/面板/审批有效期。
- OTP独立状态机：2分钟、30秒间隔、总发送≤3次、累计错5锁定；失败计数返回错误对象由上层事务提交，避免raise回滚安全计数；确认使用持久request_id幂等辅助。只存HMAC供验证，不在响应/审计返回明文。
- `approval/sms.py`：AESGCM封装模拟短信密文，随机12字节nonce；密钥为 `HMAC-SHA256(OTP_HMAC_KEY,b'safebank/otp-sms/aead/v1')`；AAD为canonical JSON的approval_id+view_hash，无裸拼接歧义。测试writer只接密文且要求同事务，不作HTTP。生产默认未配置writer/simulated_sms时503。
- `api/approvals.py`已搭建G6/G7/G9/G10/G17处理器；**G9与修改路由依赖尚缺的view_json，当前503，尚未通过HTTP完整联调**。G7成功审计名APPROVAL_REJECTED。风险揭示token按approval_id+view_hash+揭示版本/文本HMAC绑定。

## 组件验收范围

输入/输出与失败边界：面板为完整EvalContext→确定性视图/哈希；锁为客户ID集合→专用connection；审批为持久plan/evaluation/view→approval ID；OTP为锁定审批和挑战→发送/确认/明确错误。任何缺密钥、缺面板快照或缺短信通道都拒绝，不构造假成功。

独立PlanView最初6项通过；HMAC/AEAD独立10项通过；最终PlanView增至8项。真实PG测试不添加预计字段、不伪造迁移：复用原0001空库迁移与tmpfs夹具，证明锁/审批/OTP的现有表状态机；测试短信sink并不证明短信持久化。

## 实际命令（2026-10-23）

| 命令 | 结果 |
|---|---|
| `.venv/bin/pytest tests/gateway/w4/test_planview.py -q`（初版） | 6 passed |
| `.venv/bin/pytest tests/gateway/w4/test_binding.py -q` | 10 passed |
| `make lint`（最终） | 通过；All checks passed；151 files already formatted |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-w4` | **26 passed**（18单测+8真实PG），7.12s；此前24 passed后新增2项目标/缺揭示回归 |
| `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db` | **17 passed**，2.24s |
| `make test-gateway`（最终） | **1 failed,81 passed,8 skipped**；唯一失败旧占位断言 `tests/gateway/test_probes.py::test_create_for_evaluation_is_not_implemented`，仍要求函数抛NotImplementedError。现代码对缺输入/配置返回503 GatewayError。不能为通过该断言保留假占位实现 |
| `make test`（最终） | **1 failed,200 passed,56 skipped**；同一旧占位断言，非完整通过 |
| `git diff --check` | 通过 |

权限记录：最初worktree写入受workspace沙箱拒绝，按明确任务授权升级后成功；PG命令首次在沙箱内因随机端口bind权限失败（15 setup errors），授权升级后复跑17 passed。无自动审核拒绝，无删除/重建任何数据卷；只使用共享显式tmpfs临时容器，未执行make up/down/test-p1。

## 未完成与精确待决（由共享任务处理，W4未越界）

1. **执行状态协议阻塞**：现state.py规定EXECUTING永不回决策态，但PRD7.10.4/5要求L3明确STATE_CHANGED后可同plan重新审批。需要独立审查后统一裁定plan/job状态和attempt边界，不能擅加回边。按协调者指示，**G8、G5和UNKNOWN工作器尚未实施**；双花、篡改后的执行拒绝、STATE_CHANGED、新attempt、迟到execute/reconcile、commit_then_drop等执行验收尚未运行。
2. **0002最小字段清单**：`evaluations.view_json JSONB`保存所有结论完整面板（W3 append_evaluation分配revision后build_view并存hash+view）；`otp_challenges.code_ciphertext Text nullable`保存D专用模拟短信密文；`execution_jobs.authorization_evidence JSONB`与`deadline_cap_wall timestamptz`保存UNKNOWN原attempt证据和不可延长的授权cap。不能把私有_view塞进公开Decision、用其他JSON字段偷存。
3. **状态唯一入口**：state.transition固定按c.id寻址，execution_jobs真实PK为plan_id，需通用单列PK寻址；authorization_claims需登记CLAIMED→CONSUMED（scope_id PK）。W4不写workflow_events（W5 outbox归属）。非法迁移仍ValueError。
4. **集中runtime接线**：调用locks.configure；绑定APPROVAL_HMAC_KEY/OTP_HMAC_KEY；仅DEMO/EVAL启用simulated_sms并注入同事务加密短信writer（0002后存code_ciphertext）。W5 D5只读取仍有效且未锁定的挑战并调用受控解密，普通A/F不可读；无通道503。
5. **冻结schema**：任务书要求disclosure_token，但冻结PlanView无该字段；当前G9字典响应额外带token，需共享schema/接口审查确定正式wire字段后再宣称G9完成。
6. **过时probe**：由共享任务将test_create_for_evaluation_is_not_implemented替换为实际缺配置failclosed测试，W4不修改白名单外测试。
7. **跨模块联调**：W3检查点已只读核对，resolved_steps有来源及action_kind/base_risk/outflow/target_fingerprint等metadata；未来L3步骤需严格提取step_id/tool/args/depends_on/arg_sources再用冻结PlanBody验证，禁止直接向L3传metadata。未复制或修改W3 replay；W1 app/assistant均写auth_sessions，future deadline cap须按caller.jti读取持久JWT期限。取消后的CANCELLED原话在执行终态不得转CONSUMED。
8. **面板后续覆盖**：AA分摊细项必须来自bankcore预演结果，不能在W4另写金额算术；当前L3/bankcore该领域未交付，不能把参与人ID列表视作完整AA分摊验收。

## 文件清单

`services/gateway/app/approval/{planview,service,sms}.py`、`services/gateway/app/locks.py`、`services/gateway/app/api/approvals.py`、`tests/gateway/w4/{test_planview,test_binding}.py`、`tests/gateway/w4/db/{conftest,test_lock,test_approval}.py`、Makefile末尾test-w4、本文。

AGENTS.md、PRD、replay、EvalContext、schemas、matrix、state、tables、迁移、main/config、L3均未修改。无push/PR/merge，无新增子agent，无虚假署名。实现SHA将由后续日志提交记录。
