# G2 owned AA request status lookup

Date: 2026-10-31. Branch `fix/split-status-read`; parent `5e589b561b26d64d803b74081f8f6550fad48e86`.

## Root cause and scope

The 29-requirement audit found `fetch_read` still unconditionally rejected every non-null `get_split_status.request_id`, using an obsolete comment that BankState lacked SplitView. The frozen BankState now carries `splits`, and L3 `reads.py` already supports by-ID reads. The AA list page currently uses a null ID, so this was a broken specified-request branch rather than absence of all AA progress capability.

The old branch is replaced with an authoritative snapshot lookup by split ID and the existing `owned(state, 'account_id', split.account_id, customer_id)` check. A missing split, missing account or differently owned account remains 404 before the L3 read. List/null-ID/pagination paths, all other entity ownership checks, strict result validation and both snapshot version fences remain in place. No schema or write/authorization behavior is changed.

## Verification

- Six new in-process contract regressions on old production code: **1 failed, 5 passed**. The owned split's by-ID request failed at the obsolete guard with NOT_FOUND; all denial and null-ID/list controls already passed.
- Repaired code: `.venv/bin/pytest tests/gateway/w2/test_split_read_ownership.py tests/gateway/w2/test_*.py -q --tb=short -p no:cacheprovider` — **209 passed**.
- New tests assert actual split ID, received/outstanding cents, one matching L3 read, two snapshot fences; unknown/cross-owner/missing-account rejection without invoking read; and exact preservation of null-ID/list cursor/limit arguments.
- Changed Python Ruff check/format and `git diff --check`: passed.

## Gates and boundaries

No actual API/network, database, Docker, LLM, browser, deployment, push or merge was run. Test L3 is an in-process contract double. Independent review and full/fresh real-PG producer/read acceptance belong to the coordinator; this candidate is not claimed as already passing those gates.

Suggested coordinator real-PG regression: own AA created and paid through D4 → G2/F app `get_split_status(request_id=<actual own ID>)` returns updated received/outstanding; another customer's ID and nonexistent ID return 404; null/list/pagination unchanged. Existing L3 by-ID PG coverage is `tests/obligations_data/test_split_exec.py::test_get_split_status_by_id_pagination_and_404` and must not be confused with the G2 branch fixed here. Shared AGENTS/PRD intentionally left to the coordinator.

## 协调者独立核验

`3439a63`差异只移除旧无条件404，按实际split.account_id和当前客户owned校验，保留双快照versions/clock fence及L3客户参数。独立实际PG经原生产prepare/execute创建AA并注册受控付款、S1等价L3执行后，G2 fetch_read按ID返回真实received/outstanding，cus_ls同ID404；1 passed/3.18s。此项验证真实银行数据库与G2读取逻辑；HTTP JWT/ledger仍由既有test_read_ledger另验，不冒充本测走过公开BFF。
