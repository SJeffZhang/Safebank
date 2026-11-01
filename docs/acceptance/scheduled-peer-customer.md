# OTHER定时事件的双客户版本屏障（2026-10-31）

基线8625f09d42ef8955cf077c5ba967233c104adde9；分支fix/scheduled-peer-customer。本worker不运行DB/API/Docker/模型/浏览器，真实PG由协调者串行运行。

## 先行PG检查点（产品实现尚未修改）

新增tests/obligations_data/test_scheduled_peer_event.py。夹具复用install_core/install_obligations，并在每次调用EventService前实际查询断言pye_zs_ww由cus_zs持有、target_account_id=acc_ww_chk、目标customer=cus_ww。上述实体在既有tests/obligations_data/fixtures.py明确创建；不把不存在ID当产品失败。

六个PG测试覆盖：OTHER单次100000分双方账本/余额及两个version+1、旧版本重放不重复记账；peer stale和缺peer expected_version都STATE_CHANGED且交易/游标/job/execution/version全回滚；卡冻结的BLOCKED只推进源游标和源version；OWN和EXTERNAL路径保持仅源客户版本屏障。

已运行`.venv/bin/pytest --collect-only -q tests/obligations_data/test_scheduled_peer_event.py`：6 tests collected；ruff check/format通过。**没有运行PG，不标红测通过或修复通过。** 协调者可在此检查点用`OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest -q tests/obligations_data/test_scheduled_peer_event.py`对旧产品实现先亲验。

静态候选：execute_event仅装载源客户scope，锁/比对/增源版本；与PRD §8.4双方客户版本规则冲突。待协调者PG回执和最小修复后补记。


## 协调者旧码真实PG回执

协调者在测试检查点799df5b集成后亲跑旧产品实现：本文件**3 failed / 3 passed**。OTHER实际返回BLOCKED/NOT_FOUND；stale-peer与missing-peer未抛BankError；OWN、EXTERNAL及BLOCKED只增源版本对照通过。没有夹具不存在的失败，归属断言已通过。原日志由协调者保存于/private/tmp/sbg-reserve-peer-reload-pg.log；本worker未运行PG，也不复制其中连接资料。

## 最小修复

仅改L3 EventService的scheduled目标装载/双客户屏障必要分支。根据真实standing_instruction、属于源客户的payee、目标account归属解析peer，StateScope取实际源/peer客户集合，绝不信任callback额外scope或expected_versions键来扩目标。OTHER在event job锁之后取clock FOR SHARE屏障，再按customer_id升序FOR UPDATE锁两个state_versions；源/peer缺版本、任一旧版本或旧clock均STATE_CHANGED，不用当前值补缺失。锁后重新读取目标绑定，发生变化即失败。装载双客户state后仍调用原bankcore事件函数和persist；SUCCESS且有实际transfer_id时双方各version+1，BLOCKED/FAILED_INSUFFICIENT等只改变源游标则只增源版本。终态重放在版本检查前仍返回原结果，不重复记账。

OWN/EXTERNAL继续原单客户屏障，REMINDER/fund/D4分支保持原实现；没有修改bankcore/events、ledger、analytics、state、gateway或S1。S1正常执行经既有_re_evaluate的transfer快照和_pinned传全部涉及版本；本修复不在L3伪造缺失peer版本。所有锁都在既有L3事件事务内，无跨HTTP持锁。

## 纯测与待验

新增SQL AST内存边界配合真实bankcore：旧实现4 failed / 1 passed；新实现11项通过，覆盖双scope/排序锁/两版本+1、缺旧source/peer、旧clock、锁后目标变化、重放、BLOCKED/余额不足不增peer、callback额外版本键不扩大scope、他人payee不能提供目标。

实际运行：`.venv/bin/pytest -q tests/bankcore tests/obligations_data/test_scheduled_peer_runtime_unit.py tests/obligations_data/test_reminder_runtime_unit.py tests/obligations_data/test_reminders.py` → **526 passed**（1.07秒）；相关ruff check/format及diff检查通过。PG新增文件再次collect：6 tests collected（0.23秒），**修复后真实PG尚待协调者亲跑，不记为通过**。需在最终集成head上复验双方账本/版本和stale回滚，并由未实现者独审；本单元不推送、不合并、不更新共享AGENTS/PRD。
