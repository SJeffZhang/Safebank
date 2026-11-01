# 实际代扣的授权到期边界

协调者在G9有效节省核查期间继续检查实际扣款：ACTIVE状态不代表end_at仍有效。真实pure old2例1fail/1正控pass；真实tmpfs PG old1fail，end_at早于due一秒却返回SUCCESS，证明不只是显示问题。

仅在apply_merchant_charge现有mandate ACTIVE判断中加入due > end_at（同一Asia/Shanghai转换）；等于截止时刻仍有效，过期返回既有MANDATE_NOT_ACTIVE/BLOCKED，不动余额/账本，账单游标照常推进，幂等仍沿用实际run/event identity。S1预演与L3执行使用同一纯函数，没有另写授权路径。

纯事件相关15pass；实际PG新回归+整个events_api 11pass/4.50s。PG验证全表账本ID集合前后相同、原活期600000、游标前移、source版本+1、旧版本终态重放仍BLOCKED。首次绿验的fixture误将种子账本当空，再混比全表与客户scope集合，已改为同scope前后比较；这些夹具失败不计产品红测。旧定时测试仍查SCHEDULED_TRANSFER与新canonical producer不一致，改成按实际transfer_id查询并明确断言P2P_EXTERNAL；不足资金负控检查新旧三类均不得新增，不降低资金检查。

独立审查与最终完整head CI另核。不调用模型/浏览器，不触旧演示数据库、卷或分支。
