# L3 风险测评有效期日期处理 — 独立只读评审

范围：`origin/main..HEAD`（4 个提交：beef1e2, 2d8fcb6, bf2ad46, 5411636）
评审方式：只读，不编辑代码、不提交、不推送。未读 AGENTS.md / PRD.md。

## Verdict

**APPROVE**（无 P0/P1；两条 P2 观察项不阻塞）

## Findings

### P0

（无）

### P1

（无）

### P2

1. **本评审未能实际运行给定测试命令**（环境限制，非代码缺陷）。`OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/obligations_data/test_risk_profile.py tests/obligations_data/test_risk_exec.py -q` 与 `docker info` 均被本评审会话的权限策略拒绝（Docker 不可用，tmpfs PG 夹具无法拉起）。按项目既有约定「以协调者复跑为准」，此项由协调者复核；本报告的结论基于静态核对与既有测试代码语义推导。
2. **同一字段在 L2 网关存在独立、语义不一致的判定（超范围，仅提示）**：`services/gateway/app/utterance/service.py:80` 用字符串比较 `customer.risk_valid_until < bank.sim_now.date().isoformat()`，`services/gateway/app/pipeline/intent.py:219` 用 `datetime.fromisoformat(...).date()`（按存储时区取日，非 Asia/Shanghai 业务日）。二者都能解析 aware 字符串、不会崩，但与本 diff 的「Asia/Shanghai 业务日」口径不完全一致。本次 diff 仅改 L3 reads.py，L2 不在范围内，故不阻塞；建议后续单独核对 L2 是否需对齐口径。

## Checks

1. **修复目标确认**：旧代码 `date.fromisoformat(customer.risk_valid_until)` 在 `risk_valid_until` 为 aware datetime 字符串（种子数据 `fixtures/l1_seed_v1.json` 中确为 `"2027-09-01T10:00:00+08:00"`）时抛 ValueError → HTTP 500。新 helper `_risk_still_valid` 用 `datetime.fromisoformat` 解析，`datetime.fromisoformat` 同时接受 date-only（返回午夜 naive）与 aware 字符串，修复成立。
2. **业务日语义正确**：`_risk_still_valid` 对 naive 值 `replace(tzinfo=BUSINESS_TIMEZONE)`（Asia/Shanghai）后 `_business_time(dt).date() >= _business_time(sim_now).date()`。`>=` 实现「有效期覆盖到期日整天」（到期日当天仍有效，次日失效），与意图一致；Asia/Shanghai 无夏令时，`replace(tzinfo=…)` 无歧义。
3. **sim_now 恒为 aware，无新增崩溃面**：`services/bank-backend/app/state.py:284` 在 `load_bank_state` 内 `now = now.astimezone(ZoneInfo("Asia/Shanghai"))`，因此 `state.sim_now` 进入 `read()` 时恒 aware，`_business_time(sim_now)` 不会因 naive 而抛 ValueError（`reserve._business_time` 对 naive 会 raise，但此处不可达）。
4. **两个调用点均已改用 helper**：`reads.py:225`（`get_profile`）→ `:236`；`reads.py:389`（`get_risk_profile`）→ `:400`。`rg` 确认 `reads.py` 内已无其它 `risk_valid_until` 比较路径。
5. **`date` 导入移除安全**：`from datetime import date, timedelta` 改为 `from datetime import datetime, timedelta`。全文件 `rg` 确认 `date` 类名不再被引用（`:95` 的 `.date()` 是方法、`:107` 的 `date=` 是关键字参数名），无 NameError 风险。
6. **列类型匹配**：`models.py:42` `Column("risk_valid_until", String)`，helper 形参 `valid_until: str` 与实际存储类型一致；`packages/contracts/.../state.py:101` `risk_valid_until: str | None`。调用前有 `customer.risk_valid_until is not None` 短路保护。
7. **边界用例覆盖**：新 `test_risk_profile.py` 覆盖未来 aware 字符串（is_valid=True）、过期 aware 字符串（is_valid=False）、未测评（is_valid=False）；既有 `test_risk_exec.py::test_risk_profile_four_validity_states` 覆盖 date-only 字符串、过期、以及「到期日 == sim_now 日期仍有效」边界（`NOW = 2026-10-15T09:00:00+08:00`，valid_until `"2026-10-15"` → is_valid True）。语义断言与实现一致。
8. **测试命令**：因本会话权限拒绝未执行（见 P2-1），结果留待协调者复跑。
