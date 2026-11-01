# 11-01 历史分类与日期核验

原报告B2指出，生产新交易已经按merchant_category映射八类，但历史summary只白名单tx.category，将“外卖”等归其他，空category还遗漏分类行。旧六项真实形状纯测5失败/1正控。统一只读投影：已关联的权威商户MCC/类别优先，缺商户则按已保存tx.category映射；不读counterparty/memo猜类别。消费流水返回和category筛选、汇总当前/上月、异常同类基线/展示共用，金额及原交易记录不变。修复后bankcore+六新测500通过，日志/private/tmp/sbg-nov1-categories-{red3,green}.log。首轮夹具误用缺usage_expected或非法category=None已纠正，不计产品红测；None不是内部Transaction冻结契约的合法值。

日期额外验证通过生产persist→真实PostgreSQL timestamptz(UTC)→load_bank_state→get_holdings：上海10/15写入，PG显示UTC10/14 16:00，公开trade_date仍10/15且确认/到账时刻不变。精确修复前6c137db归档跑同一PG测试旧1失败，当前1通过；日志/private/tmp/sbg-nov1-trade-date-pg-{red,green3}.log。新fixture最初导入dynamic effect错误、runtime试图seed只读products被权限正确拒绝，改为FUTURE_EFFECTS和admin仅fixture建档，未放宽产品权限；上述两次不计产品红测。

独审及最终全CI另核。不修改原演示CSV或已存在数据库；读投影修正不等于重跑原场景或改账。
