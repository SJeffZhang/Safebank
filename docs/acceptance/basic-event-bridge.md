# 基础外部事件与AA付款链路开发修复

基线22a87799；依据原始browser-demo报告D4缺口，修复只在开发/自动化环境验证，未新发真实模型请求、截图或彩排。

## 根因与改动

- G4外部控制台D4原为503占位。新增严格共享载荷，仅D服务+console JWT且DEMO/EVAL模式可注册；时间取L3模拟时钟，不接受浏览器伪造console_session_id。
- L3注册的扁平大写kind载荷与旧执行器的包裹/小写结构不一致。支持两个历史表示，按当前不可变事件kind取DEMO证据，经同一S1事件引擎执行，不直接绕过L2。
- 旧执行使用常量system/event派生账本ID，第二个事件会相撞；现绑定真实event_id。request_id按客户幂等、内容冲突409；重试保留首次时钟与console证据，不重复入账。
- 原AA纯函数改变内存进度但没有持久化付款项。现付款项PAID/paid_at/payment_event_id与账本在同一执行事务持久化，按客户/账户/未付状态匹配；重复付款新事件明确BLOCKED，原事件重放不再入账。
- DEMO事件必须匹配注册的event/kind/console证据，未来事件不能提前执行。纯函数拒绝不会被事件层错误标SUCCESS。卡刷卡路径校验当前卡、渠道、限额，普通外部扣款保留不同交易kind。

## 红绿证据

- 新纯测初版旧实现10失败，覆盖扁平/大写、AA进度、事件ID碰撞、503及严格整数。首次测试钩子误写authorize函数名已修正后复跑，未把该harness错误计红。
- 新真实tmpfs PG回归：付款项仍UNPAID、4类证据不匹配仍执行、未来事件提前执行，6失败/1正控通过；修复后全部通过。
- 最终相关L3/S1/D4数据库回归57 passed / 18.07s，命令 `OBLIGATIONS_DB_TESTS=1 GATEWAY_DB_TESTS=1 uv run --group poc pytest -q tests/obligations_data/test_demo_event_flow.py tests/obligations_data/test_demo_api.py tests/obligations_data/test_events_api.py tests/gateway/w5_clock tests/gateway/s1`。
- 纯测、外部账本规则、fresh import组合36通过；格式化后ruff check通过。
- 原DEMO事件单测fixture的任意{"by":"test"}证据与未来时间已改成真实typed证据及到期时钟；S1成功fixture补真实immutable payload，不降低负控。

## 边界

测试用全新tmpfs数据库；旧演示数据/卷均未删除。临时测试库生命周期由既有fixture管理。未记录生产演示通过，未实现攻击/D8或提交上传。独立安全评审与最终完整head CI仍需通过后才可发布。
