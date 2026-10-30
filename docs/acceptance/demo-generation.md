# C: Demo database generations (2026-10-30)

Input: existing isolated e2e deploy checkout with e2e.override.yml and matching ignored secrets.
Output: fresh bank/gateway/agent DB generation, migrations+seed, JWT pair and private Compose overlay,
local current.json pointing at healthy generation. Existing DBs and volumes retained.
`make demo-reset` prepares new generation then switches services; `make demo-up` starts current one.
No HTTP admin reset added; existing L3 reset remains idempotent seed installation only.

Checks: lifecycle9 passed; driver+batch tests70 passed (activeDB integration combined run follows).
Independent review APPROVE in docs/reviews/2026-10-30-demo-generation.md.
Failure rollback, old-session invalidation, identical initial digest after two real resets and
old DB retention still require actual deployment acceptance. Not complete/not frozen.
