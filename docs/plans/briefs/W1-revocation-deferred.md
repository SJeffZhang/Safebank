# W1：撤销已提交但旧执行清理受阻

2026-10-23，协调裁定。只补W1当前集成组件；不新增共享实现、不改冻结state/idempotency/errors/audit函数、表、Code或成功响应结构。依据PRD7.4/7.10及S1e：不能因预期409回滚用户撤销，也不能放宽畸形scope/claim。

## 固定语义

- 在真实客户锁及同连接短事务中，正常revision/status竞争用savepoint隔离并有界重读，按新快照选普通/孤儿CAS。只捕获预期GatewayError(STATE_CHANGED,409)；真正SQL/连接异常、程序ValueError不能吞成成功。
- 稳定畸形scope/claim或有界竞争未解决时，不补数据、不改job/claim来绕guard。继续撤销其他plan/审批/clarification/UI源及G14所有sid/auth session；原授权CANCELLED或SUPERSEDED、应有epoch/logout仍提交。失败任务保留原状态与字段，审计清楚记录待清理，不宣称阻止已在途提交。
- G3当前请求不得发布replacement写scope、assistant JWT或消费新的action/hint；随后任何准备发布写scope的请求都须重扫该sid现存live jobs，不以本次是否刚替代ACTIVE原话为条件。仍受阻则继续409；正常UNKNOWN经合法幂等清理通过不一律禁新授权。G18的utts=[]只读token保持原规则。
- 错误固定HTTP409、Code.STATE_CHANGED、details={authorization_revoked:true,cleanup_pending:true}，message明确原写授权已失效而旧执行清理待处理；没有Decision，未声称执行已失败或资金已回滚。外部不需暴露畸形行或其他客户数据。
- producer把该GatewayError.body(trace_id)作为(409,body)正常返回给既有request_idempotent，使撤销和错误缓存同事务提交。事务外从缓存code/message/details重建并raise GatewayError，保留统一错误格式及本次trace。成功分支保持原wire；不得改冻结helper。
- G3同request_id重放不再增epoch/撤销或生成token；同ID不同body仍冲突。G14旧JWT因全局撤销返回401；新有效JWT重放原request_id取得缓存409，不再次登出新会话，新logout须新request_id。鉴权不因回放放松。
- 精确审计例外：仅本分支用现有audit.chain.append(conn,...)，与撤销和dedup同短事务、无HTTP且不新开连接，避免提交后崩溃永久漏审计；不改append函数。G14沿用AUTH_LOGOUT，G3使用真实AUTHORIZATION_REVOKED（不能记未发生的UTTERANCE_REGISTERED），记录own sid/受影响授权/待清理plan及原因，重放无重复审计。正常成功路径不在本次顺便重构。

## 必须真实PG验证

1. 缺claim、scope空/重复等稳定冲突的G3取消/改口/普通替代及G14：409、撤销持久、job/claim提交字段不改、新scope/token/used_by不发布。
2. 同sid再次新request_id仍受阻；同request_id同body重放不重复epoch/副作用/审计，不同body冲突。只读续签不因此拿到写授权。
3. 多plan/多sid中畸形项不阻止其他合法项UNKNOWN、审批/UI撤销；G14所有原JWT/session失效。重新登录后旧logout request_id不能注销新会话。
4. evaluation出现/状态变化的savepoint重读仍成功；真实backend断连为503且不假成功。
5. 故障注入在撤销/审计/dedup任一提交前SQL失败时整体回滚，commit后响应丢失重放只一份审计/撤销；error trace使用本次请求。

只在W1白名单实施及既有专用CI一行；更新W1进度，原五退出全通过/专项零skip后冻结。协调者复跑和独立PRD审核仍必需。
