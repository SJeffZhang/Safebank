# Independent retest reserve + HINT reviews (2026-11-01)

Review targets:

- Reserve/report alignment: `c0fcf42539baae441ddf9ee0d695810ab48b498e`, base `8e2810e6764e3fd3e37406b31e694605751e0356`.
- HINT action mapping: `15b9a6ad3507bdd2b2c1272f1f05391df0b062dc`, same base; integrated as root `61fd32c`.

Reviewer did not implement either target. Review was read-only for repository production/test files. Only this `/private/tmp` report was authored. No DB, Docker, deployment, real provider, browser, or scene replay. Root's later readcard work is outside scope.

## A. Reserve projection / report alignment

### Findings

No actionable P0, P1, or P2 findings. **APPROVE** for this unit, subject to root's final combined CI gate.

### Reasoning

- `PreflightStage.run` retains the existing strict `_reserve(before, after)` path for successful transfer/subscription/schedule plans. Missing obligation data still raises `DEPENDENCY_UNAVAILABLE`, HTTP 503; the new display branch does not replace that check.
- Pure protective/non-outflow plans now call the optional projection on the original snapshot. A `ValueError` from missing/incomplete obligation data leaves availability false and adds no refusal or fabricated reserve. Failed plans also preserve their original financial failure and use best-effort display only.
- Both successful projection paths set the new flag only after obtaining the pure bankcore report. B1 skips projection and remains false. The displayed amount remains the original snapshot's total reserve, not the shadow post-plan total or a bank freeze.
- Context -> DecisionStage -> PreflightView -> Agent wire carry the flag consistently. The Agent mirror rejects non-boolean values. Frontend `=== true` shows the computed amount (including real zero); false or missing legacy fields show "未计算".
- The flag participates in `PlanView`'s existing complete-preflight semantic hash. A temporary pure control verified that toggling only availability with reserve fixed at zero changes `view_hash`, and `content_hash(view)` still matches each newly built view. Approval/OTP binding code is unchanged.
- Existing G9 returns stored raw `view_json`, rather than reserializing old panels into the new schema. Consequently old saved hashes are preserved, while the frontend handles their missing flag as uncomputed.
- Preview already computes `reserve()` and now marks that value available. This does not grant any production capability.
- The changed PRD text explicitly labels this as a later semantic clarification. R6 changes documentation only, aligning §2.2 with the canonical §8.2 already present at base; no seed, detector, or threshold implementation changes are in this commit.
- Archived scene observations are kept distinct from the requirement-provenance correction. Historical labels are not promoted to PRD completion/CI claims.

### Independently executed verification

`AGENT_DB_TESTS=0 .venv/bin/python -m pytest -q -p no:cacheprovider tests/gateway/w3a tests/gateway/w4b1/test_planview.py tests/gateway/w4b1/test_material_details.py tests/agent/gateway/test_wire.py --tb=short`

Result: **164 passed, 8 skipped**. The eight conditional DB tests were not executed and are not counted as a DB pass.

`npm test -- --run src/mobile/Approval.test.tsx src/mobile/Approval.material.test.tsx`

Result: **16 passed** in two files.

Additional temporary pure controls:

- Availability-only semantic-hash change and hash self-consistency: passed.
- Agent wire rejects availability `1`, `"true"`, and `None`: all three rejected.
- All 10 archived original-report file hashes match its manifest.
- All 6 requirement-alignment file hashes match its manifest.
- The initial final PRD Git object `ffcfe23cdec8d5567240951f3423a9872c734d07:PRD.md` matches the provenance SHA256.
- Base `8e2810e...:PRD.md` already contains canonical extra anomalies 015 and 012; c0fcf42 does not alter seed/rule code.

One initial ad-hoc hash-control fixture omitted required `PreflightView.executable`, causing a fixture validation error. It was corrected to `executable=True`; this was not classified as a product failure.

Root's reported `make test-w3a` 64 passed is recorded in its acceptance artifact but was **not rerun by this reviewer**. No full CI or live business result is inferred from the pure/UI runs above.

## B. Persisted HINT ID -> card action

### Findings

No actionable P0, P1, or P2 findings. **APPROVE** for commit `15b9a6ad3507bdd2b2c1272f1f05391df0b062dc`, subject to root's combined gate.

### Reasoning

- The production `GatewayReplanHint` field is `id`. The changed mapper reads precisely that field and emits `CardAction(kind="HINT", ref=<id>)`; an arbitrary extra `ref` cannot override it.
- Only string IDs with the expected `hnt_` prefix and safe ID alphabet become actions. Invalid/non-HINT IDs are omitted, rather than coerced or derived from labels, amount, or reply text.
- Generation remains deterministic and does not mutate the decision. Existing REPLAN/user-resolver gating, multi-hint order, and empty-hint behavior remain covered.
- The action still contains only kind/ref. The existing frontend fetches visible labels from G22a and submits the selected hint ID through the existing flow. This change performs no acceptance, G3 registration, evaluation, or execution, and changes none of the scope/hash/TTL/epoch checks.
- The new SSE test runs the actual HTTP route and compiled gateway graph. External G20/G21/G4 responses, provider ports, identity, lock, and checkpoint storage are explicit test substitutes; it proves actual card/SSE mapping, not live G22a or authorization.

### Independently executed verification

`.venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_cards.py tests/agent/test_retest_hint_cards.py --tb=short`

Result: **33 passed**.

Independent isolated negative control: archive the exact base into a temporary directory, copy only the two final tests from `15b9a6a`, and execute the same test selection against unchanged production code. Result: **7 failed, 26 passed**, exit 1. Failures include the real wire `id` mapping, forged `ref` precedence, mixed-resolver hints, and actual compiled-graph SSE `actions=[]`. No working tree was reset. Existing checkpoint deserialization warnings appeared only in the baseline negative output; they are not introduced by this mapping change.

## Limits / handoff

These approvals cover the reviewed code paths and independently executed checks. They do not mark original 02/28/34/38/40 scenes passed, certify full PRD completion, or replace real database authorization tests and full exact-head CI. Nullable HTTP entry was implemented by this reviewer and is explicitly **not independently reviewed in this report**. Root retains that unit's separate review responsibility.
