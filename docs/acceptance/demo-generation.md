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

## Actual acceptance (implementation c905ef2)

- Lifecycle regression suite13 passed; lifecycle + try-agent83 passed.
- Two real resets created distinct bank/gateway/agent database sets in the existing
  isolated PostgreSQL instances. Initial digests identical:
  `9684244339a827ca9688eea06e7a09b873f0915cd159b98376f8f1663c2ea9dd`.
- First generation: real L1 yellow transfer1 yuan to 王五 executed; checking600000→599900 cents.
  After second reset old DB still599900, new DB600000; old checkpoints11/new0;
  old notifications2/new0. Original pre-generation bank also retained.
- Previous browser cookie and gateway app JWT both rejected401 after key/service rotation.
  Initial BFF probe omitted required client header and correctly returned403 CSRF_REJECTED;
  corrected test headers match frontend and allowed login; no security policy changed.
- SQL verified runtime cannot cross-connect bank↔gateway, bank runtime lacks schema CREATE,
  bank_ro has SELECT and lacks INSERT. Migrations at expected heads, global ledger balanced.
- Injected failure AFTER real new-service switch but BEFORE pointer publication: old pointer
  retained, original generation services recreated, exact prior state digest restored.
  The failed prepared generation is retained for inspection, not automatically deleted.
- Public progress for #96 updated and online curl verified. Data volumes never removed.

Exact final-head local CI required before merging this component; result recorded in PR/AGENTS.
