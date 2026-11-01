# 11-01 取消后的授权同步

原报告S4/X2的L3订阅已DELETED且实际拒扣正确，但网关scheduled_grants仍ACTIVE/revoked_at为空。属于持久化授权状态不一致，不把它描述为已发生未授权资金流出。

普通执行与UNKNOWN/orphan恢复只在W4已核验L3真实COMMITTED、当前客户/计划/attempt、完整EXECUTED结果后，把cancel_subscription/cancel_scheduled对应grant在同一归档事务撤销。已有grant保留最初scope/hash/source/risk与首次revoked_at；未知lazy grant只插入显式REVOKED墓碑，scope_json.revocation标记取消计划/step/tool，source_plan_id为取消计划，RED仅数据库拒绝默认值，不是假造创建授权。该墓碑不可授权执行或重建创建同意。稳定instruction排序，跨客户冲突STATE_CHANGED并整体回滚。

旧12红/1失败计划不撤销正控；修复后21真实网关PG通过，含普通/UNKNOWN/orphan、已有/缺失grant、归档写后异常整体回滚并可重试恢复、错客户/目标/tool/result/plan均零撤销、no_op取消仍保留首次撤销及原授权。日志/private/tmp/sbg-nov1-grants-{red,green2}.log。S1懒回填并发冲突及已取消商户的拒扣码另单元开发，独审与最终完整head CI另核；不操作原演示数据。

归档20a56d独审23独立负控APPROVE，全状态真实PG252通过。S1 294fc9(集成3512a24)经C独审72纯测/36PG skip＋27独立边界通过，root额外真实PG/HTTP旧3失败/2正控→5通过：普通及墓碑REVOKED+DELETED返回MANDATE_NOT_ACTIVE，仍ACTIVE返回BYPASS，两次重投仅一次event_block/一条CHARGE_BLOCKED/零execute，实际INSERT冲突返回已有REVOKED行而非本地ACTIVE。日志sbg-nov1-s1-pg-{red,green}.log；全S1 PG和最终完整CI继续核。新增单元代码另见nov1-s1-revocation-boundary.md。
