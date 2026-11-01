# NOV1 independent review follow-up: failed steps and hint wall TTL

Base: 11c3c51a714efee39cb6b4b49e87f21feb99bf44. Scope: llm/verify.py and tests/agent/test_nov1_replan_receipt.py. Independent review: /private/tmp/sbg-nov1-repairs/review-nov1-plan-replan.md; original independent probe remains unchanged at /private/tmp/sbg-nov1-repairs/test_review_plan_replan.py.

The review found two P2 financial explanation defects. A missing/unknown finding.step_id, or a two-step hint shrinking an unfailed step instead of the failed one, still produced a maximum. An expired hint also remained displayable as current. These did not establish unauthorized L3 execution.

The deterministic receipt now requires every shrinkable user finding to identify a real original transfer step. The complete set of changed hint amounts must equal that failed-step set; an unrelated step cannot acquire a maximum, and a failed step cannot remain unchanged while another is reduced. It also parses expires_at_wall as an aware timestamp and compares it with datetime.now(UTC), never profile.sim_now. Missing, malformed, naive, or expired wall TTL produces reasons without a maximum. Current plan/session/target/source/strictly shrinking scope checks remain. Valid nullable scope sources and SELF transfer hints retain a positive control. No hint/scope is mutated or adopted; no model call is added.

Red/green on 2026-11-01:

- Original C probe run on the base: **4 failed,11 passed** (unknown/None failed step, wrong changed step, expired hint).
- Extended author tests before production change: **6 failed,20 passed**, additionally covering malformed/naive wall TTL; source=null SELF with a future simulation clock remains legal.
- After production change: author26 + independent15 → **41 passed**. Ruff check/format passed.
- First default suite run inside the sandbox: **8 failed,4512 passed,1123 skipped**. All eight failures are localhost socket bind EPERM, not this component: two tests in tests/agent/gateway/test_real_transport.py and six real-listener cases in tests/gateway/shared_runtime/test_servers.py (clean/runtime_failure/cancel/cancel_twice/timeout/cancel_during_shutdown). The authorized unsandboxed localhost-unit rerun is recorded below.
- Authorized localhost-unit default rerun: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider` → **4522 passed,1121 skipped in42.77s**. No live/PG opt-ins enabled.

No live model/provider/service API, PG, Docker, browser, generation reset, shared AGENTS/PRD, or other worktree changes. Root owns independent re-review and combined precise-head CI/PG. The pending hint publishing/adoption lifecycle is a separate next component, not claimed by this change. Original report and independent red archive remain unchanged.
