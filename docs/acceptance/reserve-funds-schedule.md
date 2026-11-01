# 普通申购与新增定时的七日预留修复

基线54926153，用户演示报告后六场景核查发现：PreflightStage仅即时transfer调用reserve，普通MMF/非货币申购及新增定时跳过未来义务比较。不是L3余额扣错；问题是L2本应REPLAN却可能进入审批。

- 新纯测普通申购旧码5失败，含两产品shortfall/安全金额展示/缺义务拒绝；新增定时旧码另1失败。合法完整原始义务3000分，2000000分申购使缺口0→3000；金额授权不改。
- 当前只对transfer、subscribe_product、schedule_transfer运行既有同一bankcore前后预留比较；不把原已有缺口变成冻结/取消保护动作的阻断。账户hint查询支持account_id而非假设from_account_id，仍只缩小现有金额；DecisionStage继续决定哪些动作可给hint。
- 相关W3a纯测44 passed/7 DB skipped；真实G19/G4 FORM旧码1失败（原REQUIRE_APPROVAL），修后REPLAN、无approval/execution、reserve3000，和时钟文件合跑23 passed/19.19s。
- 现有时钟测试4失败另经诊断：09:00新增合法提醒使原“无事件”fixture不成立；多周扫描2.23秒完成超旧2秒轮询。已把无事件fixture置09:00前1分钟，推进用终态/RETRYING条件+30秒硬上限，增加精确目标时钟断言。诊断原样终态3 passed/10.41s，正式全时钟与FORM23 passed。无产品时钟规则变更。

只运行自动化临时PG，不触现有演示数据/卷、不调模型、不声称现场演示或demo-v1冻结。独审与最终完整head CI另记。

首次集成全量测试collect发现纯测与PG同名模块冲突（不是产品红测），PG文件改为`test_form_subscribe_reserve.py`后重跑。
