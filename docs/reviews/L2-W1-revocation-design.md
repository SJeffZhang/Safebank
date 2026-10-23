# W1 撤销后的清理失败：最小裁定

2026-10-23，只读设计核对。基线 main `6815547`；W1 读取工作树当前文件（不是只看 HEAD）。未改仓库、未运行 Docker 或测试、未派 agent。本文是设计结论，未宣称实现或真实 PG 验收完成。

## 结论与两种方案

推荐 **方案 A：已有幂等 helper 缓存终态 409，事务提交后重建并抛 GatewayError**。协调者提出的安全语义成立，且这比路由直接返回错误 JSONResponse 更贴合当前公共约定。

| 方案 | 行为与边界 | 判断 |
|---|---|---|
| A（推荐，协调者已裁定） | producer 完成撤销与各项可安全完成的清理；稳定结构冲突先在同连接事务内写准确审计，再转为 `(409, GatewayError(...).body(trace_id))`；`request_idempotent` 保存状态码与错误体，外层事务正常提交。提交后按缓存的 code/message/details 重建 GatewayError 并抛出，由现有 middleware 生成本次 trace 的统一错误。 | 不改 idempotency.py、errors.py、matrix、共享 schema/state；公共成功响应不变；保留 common 的“错误只能 raise GatewayError”。仅本类 deferred409 分支允许用既有 audit.chain.append(conn,...) 原子审计。 |
| B | 同样缓存并提交，但最终直接 JSONResponse 缓存的错误体。 | 可做，但需要给 common 增加“仅本类已提交的撤销错误可直接响应”的例外，还要处理缓存旧 trace 与本次 X-Request-ID 不一致；比 A 多一项规则特例，无收益。 |

两者都不能把稳定错误吞成 200/201，也不能把 409 抛出包含撤销的事务。有限重读只解决 revision/status/evaluation 出现等竞态，不能修复空/重复 scope、缺少或多余 claim。禁止补行、补 revision、放宽 helper 或消费 claim。

## 依据与当前具体断点

- PRD §7.4（约 1008、1021–1023 行）要求原话撤销即时生效；未终结 job 的 claim 仍保留；已进入执行的提交不承诺撤回。
- PRD §7.10（1166 行）、ADR-008 与 S1e 任务书要求预期 CAS 用 savepoint 重读，不回滚本应生效的撤销；畸形历史行明确拒绝，真正 SQL/连接故障不吞。
- 冻结 `idempotency.py:request_idempotent` 的 producer 返回任意 `(status,dict)` 后会持久化；producer 异常则删除占位并重抛。它已经足够缓存本类 409，不能让 deferred GatewayError 穿过 producer。
- 冻结 `errors.py:GatewayError.body(trace_id)` 已有标准错误体。缓存后重建异常只需沿用 Code、status、message、details；不另拼一套错误 JSON。
- W1 `utterance/lifecycle.py:30–158` 当前先改 ACTIVE 原话，再调用 `_invalidate_plans`；任何一项异常会阻断后续 plan、approval、clarification、hint/action 清理。`supersede_session` 还重复进入 plan 清理。
- W1 `api/auth.py:84–100` 当前逐 sid 清理之后才标记 sessions/auth_sessions revoked；一个 sid 失败会妨碍全部客户撤销。
- W1 `api/sessions.py:136–164` 假定 register 一定返回新 utterance 并签 JWT，审计也无条件读取 `result['utterance']`；错误分支必须先挡住这些步骤。
- 共享 auth dependency 在路由之前做持久撤销检查。旧 JWT 在 logout 后得到 401 是正常鉴权结果，不能为重放 409 放宽鉴权。

## W1 内部最小事务流程

在真实客户锁下使用同一连接、短事务；没有 HTTP。W1 私有清理结果可携带 pending 项，不能作为新的公共成功模型。

1. **先完成授权撤销阶段。** G3 cancel 递增该 sid epoch，并把旧 ACTIVE 原话置 CANCELLED；correction/ordinary replacement 将应替代的旧 ACTIVE 写原话置 SUPERSEDED，保留既定 clarification parent 例外。G14 先锁定/选定该客户本次待撤销 sessions，完成所有这些 sid 的 epoch/原话撤销，并把客户 sessions 与 auth_sessions 置 revoked，再进入各 sid 的辅助清理。全部操作仍在一个外层事务，后续真正 SQL 故障可以使它整体失败，不能假称撤销成功。
2. **每个 plan 独立清理单元。** 读取当前身份后在 `begin_nested()` 中调用普通或 orphan 受控 helper。仅捕获这次受控清理中的 `GatewayError(Code.STATE_CHANGED,409)`；savepoint 回滚后重读该 plan/job/latest evaluation，若身份有变化，有限重试并选普通/孤儿路径。不得无限重试。结构稳定仍不满足前置条件时，记录 pending，保留其 plan/job/claim/提交证据原状，继续下一个 plan。不能将 ValueError、SQLAlchemy/连接错误、LockOwnershipLost 等混入“预期 CAS”。
3. **继续其余独立清理。** 一个坏 plan 不妨碍其余合法 live jobs 持久 UNKNOWN，无 job 普通计划 SUPERSEDED，所有相关 sid 的 PENDING/CONFIRMED approvals SUPERSEDED、OPEN clarifications CANCELLED、应失效的未采纳 hints/actions 到期。按现有 state.transition 合法边写状态；发生预期状态竞争时 savepoint+重读，已经终态视作无需再改。真实 SQL 故障仍外抛，不声称“其他清理也都成功”。
4. **有 pending 时结束本次替代。** 不插入替代原话/新 clarification root，不签 assistant JWT，不给 hint/action 写 used_by，不发布新的写作用域。已用来源不可恢复/转移给新授权；claim 一律不消费。返回清理结果到 producer，由 producer 生成并缓存标准 409。可保留只读快照记录，但不得把它当成授权。
5. **无 pending 时走原成功路径。** 成功返回格式完全不变。G3 只有完成授权清理后才写替代相关的 RESOLVED、utterance、clarification、used_by 和 auth_session/JWT，避免失败时留下“已解决但未产生新 root”的澄清链。若必须提前暂存这些替代写入，单独 savepoint 回滚替代部分；不要把应保留的旧授权撤销放进该 savepoint。

不能简单 catch 整个 register 或整个 logout 的所有 GatewayError 后提交。前置请求验证、来源所有权/期限、普通 parsing/clarification 合槽错误仍用原异常路径，不得无授权地撤销、缓存或掩盖程序错误。稳定清理失败只在“本次已确定发生合法撤销”的内部边界收敛成 deferred 结果。

### 新 request_id 也不能跳过遗留检查

当前 `supersede()` 仅在本次找到 ACTIVE 写原话、`replaced=True` 时调用 `_invalidate_plans`。若失败分支已经把旧原话 SUPERSEDED，下一条不同 request_id 的普通写请求就可能因 `replaced=False` 跳过坏 job，直接取得新 scope/token。

因此每次准备发布新的写作用域（typed、hint、action，含实际产生写 scope 的合槽）都须重查该 sid 的遗留 plans/live jobs；不能依赖“本次是否刚替代旧原话”。遗留 live job 加上已失效授权就是现有持久待处理事实，无需加表。仍有结构冲突则再次拒绝该次替代；清理恢复后只能用新 request_id 发起，旧 request_id 始终重放原终态 409。有效 UNKNOWN 能通过受控清理时不应被一概禁止；禁止的是仍未通过严格清理前置条件的结构冲突。

G18 的 `utts=[]` 读历史令牌不产生写许可，可保持原行为；本裁定不增加永久会话封禁或绕开现有 claim 的恢复入口。G14 则必须覆盖选定客户的全部 sid，不能第一个 sid 坏了就停止其余 sid 的撤销。

## 错误、幂等与审计的闭环

### 错误体与 trace

使用已有 `Code.STATE_CHANGED`、HTTP 409 和 `GatewayError.body(trace_id)`。建议 details 只包含已证实的事实，例如：

`revocation_applied: true`、`cleanup_pending: true`、`scope: session/customer`；G14 可补 `auth_revoked: true`。有必要时列本客户相关的 pending plan IDs/数量，不返回 claim 内容、授权证据、JWT、SQL、其他客户 ID。措辞为“授权已撤销，部分执行记录清理待处理”；不能说“在途提交已阻止/资金未变/一定会自动清理完成”。

当前 PRD §9.1 把 STATE_CHANGED 的 details 固定为 `{decision:Decision}`，本类 G3/G14 没有合法 Decision。协调者须精确限定该旧规定适用于计划执行/复演相应接口，并登记 G3/G14 的撤销结果 details，不能伪造 Decision。

producer 使用 `GatewayError(...).body(request.state.trace_id)` 存入既有缓存。外层提交后，对于缓存 status >= 400，按缓存 Code/message/details 重建并抛 GatewayError，使用 middleware 当前请求 trace。因而业务状态码/code/message/details 持久重放；trace_id 是每次 HTTP 请求元数据，允许按当前 X-Request-ID 重建，不要求逐字节复用首次 trace。应把这一点写进幂等约定，避免误写“全 body 每次完全相等”。成功缓存保持原样。G3 `_already_registered` 的早返回也必须在其读取事务结束后重建并抛缓存错误，不能继续直接 JSONResponse 原缓存而留下旧 trace。

### G14 重试

- 原 JWT 在 logout 提交后已撤销，之后请求由共享鉴权拒绝 401；不豁免、不改 matrix/check_revocation，不为幂等绕鉴权。
- 请求在首次 logout 前已通过 dependency、之后等待客户锁，锁内 check_revocation 仍应拒绝旧 JWT；不能再次撤销。
- 同客户重新登录取得有效新 JWT 后，同 route/request_id/body 进入既有 helper，直接重放首次结果，producer 不执行，因此不撤销新登录和新建 sid，不再增 epoch；同 ID 不同 body 仍 409 IDEMPOTENCY_CONFLICT。不同客户的 request_id 缓存隔离不变。
- 原请求不丢失时返回原 200 成功或本类已提交 409。HTTP 响应丢失不改变已经持久化的结果。清理后来恢复，也不能让旧 request_id 重新生效。

### 审计顺序：协调者已裁定仅 deferred409 分支原子追加

原先考虑的“commit → append_tx → 抛 deferred error”存在永久漏审计窗口：撤销 commit 后、审计 commit 前崩溃，回放跳过 append_tx。因此协调者已明确采用同事务审计，精确顺序为：

**同一个 producer/连接/无HTTP短事务：撤销与逐项清理 → 确认稳定 pending → audit.chain.append(conn,...) → 返回409标准体 → helper持久化dedup → 外层commit → 重建并抛GatewayError。**

这只针对“需要保留撤销并返回409”的分支。G3 用准确的 `AUTHORIZATION_REVOKED` 事件，记录 cancel/supersede 原因与 cleanup_pending；不得写未发生的 `UTTERANCE_REGISTERED`，也不得索引不存在的新 utterance。G14 用 `AUTH_LOGOUT` 并带 cleanup_pending。trace、sid/customer 范围及本客户必要 plan 信息可入审计，不记录 secret。

producer 一次性执行，回放只读取 dedup，既不重复审计也不再撤销。audit SQL/连接故障不转成409、不吞成成功，外层事务连同撤销、审计、dedup 一起回滚；commit 回包不明时仍不对外猜测，之后由已提交的 dedup 决定重放。不会出现已提交409而本次撤销审计永久缺失的 postcommit 窗口。

这是对 common“审计只能用 append_tx”的**精确调用规则例外**：只允许上述已确定的 deferred409 分支使用现成 `append(conn,...)`；不新开连接，不改冻结 audit/idempotency/errors 函数，不增加表。常规成功路径继续当前 append_tx/postcommit 行为，本次不顺便重构其既存崩溃边界。deferred409 在事务内已审计，提交后不能再 append_tx 一次。

## 最小文件范围与冻结边界

W1 可改范围内：

1. `utterance/lifecycle.py`：分离授权撤销与辅助清理；每 plan savepoint/有限重读/收集稳定冲突；不中止其余 plans/approvals/UI；避免 correction 双次清理；新写 scope 发布前每次复查遗留 job。
2. `utterance/service.py`：承接 W1 私有结果；失败不产生 replacement root/used_by/clarification RESOLVED；保持正确的 ordinary/correction/cancel 与 parent 例外。不得给当前 helper 拼假 revision。
3. `api/sessions.py`：producer 只将已撤销清理失败转终态 tuple，并在同事务调用现成 append 写 AUTHORIZATION_REVOKED；提交后重抛 GatewayError，不再postcommit重复审计；禁止签发 replacement JWT；兼容 cached 409 的前置 replay。
4. `api/auth.py`：全客户授权撤销先于辅助清理；聚合全部 sid 的 pending；保留 status（当前 `_ ,result` 会丢掉 409）；deferred409同事务AUTH_LOGOUT审计，提交后GatewayError；成功 `{revoked:true}` 及其原审计路径不变。
5. `tests/gateway/w1/**`、W1 进度文档；协调者更新 PRD §7.4/§7.10/§9.1、ADR-008、S1e/W1 任务书与公共约定中的 trace/审计裁定、总 AGENTS。

不需要修改冻结 idempotency.py、errors.py、audit、matrix、state.py、tables/migrations、schemas、contracts，不需要新 Code。推荐 A 不需要对“只 raise GatewayError”作例外；已裁定只对既有 append(conn) 作上述精确调用例外。保持真实错误与非法迁移 ValueError 原样上抛。

## 必须由实现者实际执行的真实 PG 矩阵（本文未运行）

| 分组 | 真实数据库安排 | 必须断言 |
|---|---|---|
| 原有合法路径 | cancel/correction/ordinary replacement/logout × EXECUTING/FENCING/PAUSED/UNKNOWN；普通及 orphan | 原成功 wire 不变；撤销持久；合法 job UNKNOWN、提交字段逐项不变、claim CLAIMED；不是 L3 fence/资金执行验收。 |
| 稳定结构冲突 | orphan scope `[]`、重复、畸形 JSON；缺 claim/额外 claim/非 CLAIMED；四种入口 | 409 标准体与明确已撤销 details；G3 原话 CANCELLED/SUPERSEDED、cancel epoch 恰增一次；G14 sessions/auth_sessions 全撤销；坏 plan/job 与 claim/evidence 不被补写或消费。 |
| 跨项继续 | 同 sid 首/中间坏 plan+后续好 live plan+无job待批计划；G14 首个 sid 坏、后续 sid 好；准备 approvals、OPEN clarification、unused UI | 后续合法项全部完成；全部应撤销授权均生效；审批/UI 不能因坏 plan 漏掉；输出只有本客户信息。 |
| 阻止替代 | typed correction/普通、hint、FORM action、实际写合槽失败 | 无新授权 root/clarification root/JWT auth_sessions，无 used_by 采纳；提前 RESOLVED 不残留；parent 例外不扩大授权。 |
| 新 ID 防跳过 | 第一次409后旧root已失效；同sid用新request_id再发写typed/hint/action | 仍检查遗留坏 job并409；不因replaced=False放行；修复只由明确授权的外部措施完成后，新ID才可成功，原ID仍409。 |
| 幂等与 trace | G3原body原ID重复/并发；更换X-Request-ID；同ID异body；跨客户同ID | producer一次，epoch不重复、无新token/source采纳；状态码及业务错误信息稳定，trace对应当前请求；异body冲突，缓存客户隔离。 |
| logout重试 | 旧JWT重试；先通过dependency再等待客户锁；提交后新登录+新sid，再用新JWT重放原ID | 旧JWT401且无副作用；新JWT原ID重放原200/409，不撤销新auth/sid、无epoch累增；不同新request_id才是新logout。 |
| 真正竞争 | 外连接锁plan，W1读absence后并发插evaluation/更新revision或job状态；客户锁按真实实现 | savepoint重读选正确路径；可解决竞态无需错误409；不能用最新revision消费旧HTTP响应。稳定冲突不无限重试。 |
| 真正故障 | pg_terminate_backend/连接断开、有效SQL故障、外层commit失败；不得用mock成功代替 | 503/真实失败，无成功/终态409假记录；未提交事务全部回滚，claim不释放；未知提交结果不承诺撤回。 |
| 审计 | 首次成功、首次deferred409、原ID回放；真实audit失败/producer后与commit前故障 | deferred409事件准确且原子，无新utt索引崩溃；replay无重复；故障后审计/撤销/dedup共同存在或共同回滚；成功路径不被本次顺便重构。 |

继续执行原五退出、真实 W1 PG 专项目标与冻结契约门禁；不得把本文矩阵写成已通过。不能把网关局部 revoke/claim 测试冒称真实 L3 在途资金取消或完整 W4 恢复验收。

## 需协调落文的裁定（无需再次向用户求授权）

- 已生效撤销与稳定清理失败分离：可提交已撤销事实并以持久幂等 409 报告，禁止产生新许可；有限 CAS 重读后仍不放宽 helper。
- PRD9.1 的 Decision-only STATE_CHANGED 只适用对应计划路径；G3/G14 使用本类已撤销 details；trace为每请求元数据，可在回放时重建。
- 每次发布写 scope 都检查本 sid 未通过清理的历史 live jobs，不以 ACTIVE旧root是否存在为条件；G14必须继续全部sid。
- 已裁定仅 deferred409 的撤销分支用既有 append(conn) 与撤销/dedup同事务；common须落文该精确例外，audit函数和常规成功路径不改。

以上属于项目已经授权的必要 PRD/规则闭合；由协调者更新规则、原 W1 owner 实施，独立复核和真实验收后才算完成。
