# 演示报告缺陷与六场景开发修复

基线main `22a87799c2b422a6c2186490a5972e11bc05dce4`。输入为另一已结束会话的四份真实报告，原文与摘要位于[来源目录](../reviews/demo-report-2026-10-31/)。本轮三个Codex子agent使用独立工作树实现/只读交叉审查；协调者串行真实PG、集成与发布。没有使用DS worker，没有重新开展业务演示、生产模型录制、截图或断网彩排。

## 已实现修复与证据索引

| 单元 | 变更与边界 | 记录 |
|---|---|---|
| 执行回执 | 本次实际100元不能由5000元限额/余额替换；绑定同plan/step/tool/执行结果，FAILED/UNKNOWN不说成功，缺绑定保守 | [execution-receipt-facts.md](execution-receipt-facts.md) |
| 问卷与授权根 | 六题权威版本/文本进入hash；G4只装当前声明根，NO_WRITE→X2不受旧根污染；原JWT/父链/epoch/来源/OTP保持 | [assessment-context-fixes.md](assessment-context-fixes.md) |
| 前端真实wire | 真实月份、四基金枚举/unknown、嵌套异常/产品/定时/订单；审批终态、UNKNOWN、一次刷新和当前planID | [frontend-demo-consistency.md](frontend-demo-consistency.md) |
| AA/外部事件 | D4严格console+mode→L3注册→同S1引擎；事件证据/幂等/到期校验；付款进度PAID与账本同事务 | [basic-event-bridge.md](basic-event-bridge.md) |
| 六类站内通知 | 每日09:00未来三天有效扣费提醒；扣费/定时/基金终态与审计/通知同事务，失败回滚与恢复幂等 | [customer-event-notifications.md](customer-event-notifications.md)；[reminder-runtime-wiring.md](reminder-runtime-wiring.md) |
| 手机号目标 | 仅已登记完整11位手机号精确来源；重复/部分/未知拒绝或澄清；App两入口可手工选填，不更改seed | [phone-transfer-authorization.md](phone-transfer-authorization.md)；[payee-phone-entry.md](payee-phone-entry.md) |
| G2指定AA进度 | 按真实split关联账户归属，保留双快照版本围栏；实际PG付款进度与跨客户拒绝 | [split-status-read.md](split-status-read.md) |
| 交易事实/账单 | 实际P2P/SELF/MERCHANT_CHARGE、商户ID和类别；新旧定时外转fresh日/24h/卡累计；本人不计外转 | [analytics-transaction-facts.md](analytics-transaction-facts.md)；[external-debit-categories.md](external-debit-categories.md) |
| 周期与G9 | 周费月均/年化与读取一致；当日限额占用、月节省、基金确认/到账日期进入实际面板/hash，日期与真实订单共享纯函数 | [analytics-transaction-facts.md](analytics-transaction-facts.md)；[approval-material-details.md](approval-material-details.md) |
| 联动读取 | X1保留月分析、X4实际异常/卡/账户；X3服务端G20当前选择及fresh账户/扣费/测评/产品，不改授权金额 | [cross-scene-gathering.md](cross-scene-gathering.md) |
| 七日资金预留 | 即时转账/普通申购/新增定时统一前后预留，扩大缺口REPLAN，缺依赖503；保护动作不受原缺口误阻 | [reserve-funds-schedule.md](reserve-funds-schedule.md) |
| X2有效月费 | G9/取消结果/派生同源，超cap或到期/缺事实不授权正金额，保持MONTHLY/本人储蓄/同源/完整步骤集合 | [x2-effective-monthly-fees.md](x2-effective-monthly-fees.md) |
| 授权到期 | 实际扣款按inclusive end_at拒绝过期授权；S1预演/L3共用，BLOCKED不动账本、游标/重放保持 | [merchant-mandate-expiry.md](merchant-mandate-expiry.md) |
| OTHER行内定时 | 实际目标解析、clock屏障/升序双客户锁和版本；旧/缺peer版本回滚，重放一次，BLOCKED仅源版本变更 | [scheduled-peer-customer.md](scheduled-peer-customer.md) |

表内记录均在同目录。29项逐ID生产入口、逻辑和代表测试见[核查矩阵](../reviews/demo-repairs-2026-10-31/basic-29-audit.md)，矩阵保留其精确基线和当时“候选”的历史口径；最终修复/验证由本记录、组件acceptance与对应PR回执联合核定。

## 自动化核验口径

- 根协调者真实tmpfs PG：D4/AA相关57通过；问卷/当前根相关68通过；提醒runtime/终态/通知回滚恢复9通过；交易事实重载5通过；G2真实付款按ID/跨客户1通过；OTHER定时6通过（旧3失败/3通过）；预留FORM与完整时钟23通过；实际代扣到期与events_api整文件11通过。
- 预留纯测旧5失败+新增定时另1失败，修后W3a44通过。首次全量collect发现新纯测/PG文件同名，已只重命名PG文件，初轮集成Python4434通过/1116条件skip；集成前端453通过。之后授权有效性增量独审571纯测+6新负控，账本独审476纯测+9独立loader负控。套件重叠，数字不相加；后续审查增量和最终完整CI以PR精确head回执为准。
- 独审曾发现FAILED/UNKNOWN同义成功与超授权上限账单仍正节省问题，已修复并独立复核闭合；X2同边界已独审与真实G4旧4红/2正控→新6绿，实际取消另9PG绿；不以初次旧测试关闭新负控。
- 现有测试库bank_p1_test在最终CI前无损改名bank_p1_preserved_20261031_reports01；旧e2e容器只停止，全部库/卷/代际/WIP/worktree/分支保留。当前仅safebank完整栈用于CI；临时数据库fixture独立建库/正常清理，不是第二套业务栈。

## 发布与保留边界

最终发布必须使用本PR完整40位head运行全部本机CI并取得PASSED后匹配head合并。结果、日志摘要、精确head/merge关系写在对应PR；本文件不把候选/专项结果预报成最终CI通过。

原报告中的六场景29项均已逐项检查。经上述修复与独立复核，本轮未发现其他已确认而遗留的基础业务开发缺口；未运行的现场组合不能由代码存在推定通过。D2 prepare/攻击评测D6/D8及Preview开户/PAN/系统推送等既有范围边界不扩为本轮功能。另一会话旧演示不证明修后新head现场通过。

两个独立reset六场景、完整43真实清单、生产录制和实际断网回放仍按用户范围留其他会话核验；未全过不tag demo-v1。攻击/指标/对比在既定依赖之后。公开safebank-progress暂停，平台上传仍由用户操作。
