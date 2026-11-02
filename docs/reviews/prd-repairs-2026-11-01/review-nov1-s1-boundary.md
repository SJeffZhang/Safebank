# November 1 independent S1 revoked-grant boundary review

**APPROVE** for exact head `294fc9bb00c2424e18fe786c1849b2d07322a8da`, parent `bc01392ff69984061773fcb2ed698753458bac3c`, within the scheduled.py revoked-grant/backfill scope. No new actionable finding. Real PG and integrated exact-head release gates remain root-owned.

## Exact archive and independent execution

Exported exact head to `/private/tmp/sbg-nov1-repairs/review-s1` using git archive. Reviewed only scheduled.py, the new boundary tests and acceptance. Root grant/WIP, shared AGENTS/PRD and product files were not edited. The exact root20a56d grant producer archive was separately used in one pure interface-control test; no WIP file was imported.

With DB tests explicitly disabled (`GATEWAY_DB_TESTS=0`) and bytecode/cache writes disabled:

- `python -m pytest tests/gateway/s1 -q -p no:cacheprovider`: **72 passed /36 skipped in0.13s**; `/private/tmp/sbg-nov1-repairs/review-s1-author.log`. Skips are not PG passes.
- `python -m pytest -c pyproject.toml /private/tmp/sbg-nov1-repairs/test_review_s1.py -q -p no:cacheprovider`: **27 passed in0.56s**; `/private/tmp/sbg-nov1-repairs/review-s1-independent.log`.

No DB, API/provider/live model, browser, Docker, deployment or demo state was touched. The S1 transport/SQL/claim/audit doubles are pure test boundaries; they do not prove actual PostgreSQL races or L3 event persistence.

## Revocation path is deny-only

Only MERCHANT_CHARGE with a stored REVOKED row enters the new path. It fetches a fresh L3 snapshot, attempts strictly bounded classification, and always returns through `_block`. It never enters event_execute, creates an approval or reconstructs creation consent. ACTIVE bank instructions with revoked grants, missing/malformed revocation bindings, or any mismatched source/customer/scope/payload/due use SCHEDULED_BYPASS.

MANDATE_NOT_ACTIVE requires a fresh DELETED merchant mandate, live billing, matching instruction/customer/account/merchant/billing/source-plan, canonical immutable amount/account/billing payload/hash, scope/hash agreement, covered current customer, matching aware period/due/current billing, and due time after creation and no later than simulated business time. The shared bankcore applier must independently return BLOCKED/MANDATE_NOT_ACTIVE before that reason is recorded. A matching grant alone cannot authorize a charge or supply that reason without fresh bank state.

## Original/seed consent and root tombstones

Ordinary revoked rows retain original source/hash/scope binding. Seed-consent terms additionally retain their kind binding. Root lazy tombstones instead require the exact revocation-only shape, cancel_subscription tool, nonempty plan/step, cancellation source-plan equality, deterministic customer/instruction tombstone digest, RED storage sentinel and real revocation time. They are accepted solely as cancellation proof, never as ACTIVE creation authorization.

Independent negatives covered mixed revocation+creation markers, non-mapping saved scope, wrong fresh billing owner, inactive billing, missing customer coverage, boolean amount, forged payload with a recomputed hash, forged amount with internally consistent payload/hash, naive due, different requested event ID and creation after due. Every applicable full-S1 case produced terminal BLOCKED/SCHEDULED_BYPASS with no execute call. Equivalent UTC representations of valid due/period still produced only BLOCKED/MANDATE_NOT_ACTIVE, with the input bank snapshot unchanged.

One additional interface control loaded the exact20a56d root grant_revocation.py archive, generated its actual PostgreSQL statement values using a captured connection, then fed that produced tombstone into the exact294fc9 S1 consumer with a fresh deleted mandate. It produced MANDATE_NOT_ACTIVE and zero event_execute calls. This proves producer/consumer marker-shape compatibility in pure code; it is not an integrated SQL/L3 acceptance claim.

## ACTIVE guard and backfill conflict

The normal grant path now rejects any non-null revoked_at even when status says ACTIVE. A tombstone relabelled ACTIVE has no valid creation-consent binding and remains BYPASS; the added original-ACTIVE positive control still reaches its existing execution path.

Backfill keeps the existing proof of a committed source creation, inserts with ON CONFLICT DO NOTHING and then returns `_load_grant` on the same transaction connection. It no longer returns its local proposed ACTIVE dictionary. A concurrent REVOKED winner therefore stays revoked, and a foreign or absent actual winner is not replaced by local invented consent. Independent controls simulated both an absent post-insert row and a persisted foreign-customer REVOKED winner, and observed exactly those actual results. No additional ACTIVE update was introduced.

## Required root gates / review limits

The new tests exercise real S1 dispatch, bankcore, block/finalize/notification code with boundary doubles; claim exclusivity, PostgreSQL wait/visibility, conflict rollback, durable event replay and real L3 fences were not independently run. Root must combine the committed-cancellation archive hook and this consumer at one exact full head before checking actual missing-row INSERT versus cancellation races, existing-grant preservation, genuine DELETED merchant event_block/instruction_run/period advancement, one CHARGE_BLOCKED notification, and replay with no funds transfer.

The previously reviewed root grant primitive and this S1 consumer now agree in pure interface tests. This scoped APPROVE does not replace the pending integrated PG/CI or turn prior formal reports into live passes. Original tests, reports and prior red evidence remain unchanged.
