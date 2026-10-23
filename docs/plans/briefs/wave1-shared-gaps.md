# 波次 1 共享依赖补缺提案

日期：2026-10-23。状态：用户已明确授权按本提案受控补缺（2026-10-23）；逐组件串行实施，尚未实现。用户要求稳健优先；子任务遇到缺口继续可独立验证的部分，不以替身或 503 宣称完整完成。

## 已证实的阻塞

| 范围 | 当前代码与影响 | 最小处理 |
|---|---|---|
| 全局撤销 | `auth/deps.py:caller_dependency` 只验 JWT，不调用 W1 数据库撤销检查 | 异步依赖接 `tokens.check_revocation`，覆盖整个授权矩阵；无数据库时拒绝 |
| 澄清状态 | `state.py:TRANSITIONS` 未登记 pending_clarifications，W1 不能通过唯一入口完成澄清 | 仅补 OPEN→RESOLVED/CANCELLED，终态无出边；非法边仍 ValueError |
| 运行资源 | main 不注入 DB/L3/时钟，处理器只能靠测试 app.state | 统一应用生命周期创建/关闭 AsyncEngine 和 L3Client；注入 db/l3/wall_now；W1私钥/PIN由配置注入 |
| Linux 密钥 | 宿主私钥0600无法由容器非root用户读取；demo_pins未挂入gateway | 保留宿主私钥0600，用私有tmpfs所有者适配或等价受控注入；不可公开私钥；测试真实Linux权限 |
| 预留算法 | bankcore 无 reserve/projected_shortfall；PRD §7.7要求预演/hint/derived共用 | 新建 bankcore.reserve 纯函数，统一 obligations、shortfall、max_outflow；不在gateway重写 |
| 快照数据 | BankState只有ScheduledView/SubscriptionView展示对象，无完整merchant_billing义务、周期与所有权字段 | 最小增量内部快照契约，L3唯一load_bank_state提供原始信息；冻结的外部写工具入参不变，更新契约检查与文档 |
| 生产读与数据 | reads.SUPPORTED_READS仅get_accounts/list_payees/get_transactions/get_cards/get_profile（5/18）；state loader不填instructions/subscriptions/products/holdings/orders，模型默认空不等于真实数据 | 对应领域组件须同时补真实读与快照加载；FakeL3完整样本只能证明网关协议，不能证明生产支持。get_profile有效risk_valid_until分支引用未导入date，也需修复回归 |
| 领域写路径 | banking.SUPPORTED_WRITES仅7个，另外7个已登记工具仍返回TOOL_NOT_ALLOWED | 分组件补schedule_transfer/cancel_scheduled/cancel_subscription、create_split_request、submit_risk_assessment/subscribe_product/redeem_product；预演与真实执行共用apply/persist，逐组件审核 |
| 全历史风险（已消除） | 协调者提示后W3复核 L3 state.py:136–145，transactions按客户加载全历史，无时间过滤 | 直接使用成功POSTED P2P OUT全历史与本计划，不新增快照字段 |
| 拒绝时间（既有数据可用） | plans.created_at_wall不是实际拒绝时刻 | W3使用evaluations.created_at_wall的DENY及APPROVAL_REJECTED审计事件时间；W4拒绝审计遵循该事件名 |

## 受控实施范围与门禁

用户已批准对冻结规则作以下范围受控的例外：由协调者另派单个实现agent串行补共享依赖，W1–W4仍不得改冻结文件。协调者不写业务代码。每个组件单独本地提交、独立只读评审、亲自复跑，再开base main PR；当前head的unit/p1-postgres全绿才merge commit。

1. 先网关接线：auth/deps、state、main/config及部署密钥配置；保留授权矩阵和交叉接口签名。部署后真实API验证登录→会话→撤销拒绝，以及Linux0600权限。
2. 再核心预留：bankcore.reserve、必要的内部contracts/BankState和L3 state loader；明确时间窗口、06:00、账单实际金额、本人互转、忽略未来入账；同时验证W2派生与W3预演使用同一函数。验收必须复现赵六295500分上限和SCHEDULED_SHORTFALL。
3. 缺失领域写工具按职责拆成后续依赖组件，不混入W1–W4所有权；每项修改bankcore/L3写路径必须做真实PostgreSQL、并发与预演/执行一致性验收。不能支持的步骤维持明确拒绝且阻塞对应完整交付。
4. W1–W4的网关表需求仍汇总后串行追加0002_wave1，不改历史迁移；任何额外L3表结构需求先列清单，不擅自清库或重建卷。

不能在维持当前全部冻结的同时交付原任务书所有退出条件。若用户选择保持冻结，则完成可独立验证部分与待决清单，保留本地分支；有未满足退出条件的组件不以完成名义开PR/合并。
