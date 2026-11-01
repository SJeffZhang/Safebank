# 六场景补查：业务日期与未来定时目标

C对集成3512a24逐29需求只读审计确认三处生产可达问题：R13从真实UTC assessment.valid_until取.date导致显示比R18业务有效期早一天；ProductDef从合法上海午夜base_date的UTC存储取.date，非MMF报价多算一天；外行定时InstructionTarget无内部账户/客户ID时未来扣费target_display为空。原43固定产品seed非午夜，本轮未声称该边界已经造成原43资金错误；适当性硬判断仍用正确customer风险摘要，R13问题是契约/显示错误。

修复：两种日期先还原Asia/Shanghai再取date；upcoming定时目标优先当前客户登记收款人名称、本人目标账户名称，否则显示确定的行内/行外描述；不暴露内部ID、不查外人名录。该target_display继续受既有trust.yaml不可信字段包装，不成为授权来源。金额/预留/执行逻辑不变。

原作者和root同一生产loader/read纯probe旧3红/2正控；修复后501相关pure通过。额外真实PG覆盖上海00:30及12:30，R13/R18均到2027-10-15，合法午夜基准NAV当日1.0000；修复前b998精确归档同用例2失败，当前全日期PG3通过（另含基金trade_date）。日志/private/tmp/sbg-nov1-business-dates-{red,green}.log、sbg-nov1-business-dates-pg-red2.log与sbg-nov1-all-business-dates-pg-green2.log。

首轮新PG fixture对products UPDATE被append-only保护正确拒绝，已改合法追加独立产品；不放宽权限/保护，该轮不计产品红。C先前直接给纯R1/scorer UTC时钟的两个红，经真实loader已转上海正控证伪，明确撤回，未改这两个函数。独立复审与最终完整head CI另核；所有旧库/演示结果保持。
