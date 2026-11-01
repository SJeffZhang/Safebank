# OTHER定时事件的双客户版本屏障（2026-10-31）

基线8625f09d42ef8955cf077c5ba967233c104adde9；分支fix/scheduled-peer-customer。本worker不运行DB/API/Docker/模型/浏览器，真实PG由协调者串行运行。

## 先行PG检查点（产品实现尚未修改）

新增tests/obligations_data/test_scheduled_peer_event.py。夹具复用install_core/install_obligations，并在每次调用EventService前实际查询断言pye_zs_ww由cus_zs持有、target_account_id=acc_ww_chk、目标customer=cus_ww。上述实体在既有tests/obligations_data/fixtures.py明确创建；不把不存在ID当产品失败。

六个PG测试覆盖：OTHER单次100000分双方账本/余额及两个version+1、旧版本重放不重复记账；peer stale和缺peer expected_version都STATE_CHANGED且交易/游标/job/execution/version全回滚；卡冻结的BLOCKED只推进源游标和源version；OWN和EXTERNAL路径保持仅源客户版本屏障。

已运行`.venv/bin/pytest --collect-only -q tests/obligations_data/test_scheduled_peer_event.py`：6 tests collected；ruff check/format通过。**没有运行PG，不标红测通过或修复通过。** 协调者可在此检查点用`OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest -q tests/obligations_data/test_scheduled_peer_event.py`对旧产品实现先亲验。

静态候选：execute_event仅装载源客户scope，锁/比对/增源版本；与PRD §8.4双方客户版本规则冲突。待协调者PG回执和最小修复后补记。
