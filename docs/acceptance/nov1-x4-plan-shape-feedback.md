# NOV1 X4 strict-entity retry feedback

Base: aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1. Worktree: safebank-demo-runner/WeBank, branch fix/nov1-write-planning. Scope: services/agent/app/llm/plan.py and tests/agent/test_nov1_x4_plan_retry.py.

Formal report ID37 preserved the terminal `to_account_id must be a tagged entity object` error after real card/account/anomaly reads. It did not preserve the raw model proposals. The base already had ValueError conversion and one retry; those are not new fixes here. The remaining directly reproduced defect is that `validation_summary(ValueError)` reduced the retry feedback to the type name `ValueError`, removing the field and required shape.

The adapter now supplies a bounded list of known entity field locations and literal id/none schema examples to the existing one regeneration. It does not transform any target, amount, source, or step; the strict contracts converter and existing read/span/source validations still reject invalid proposals. Feedback never echoes invalid model values. Provider 402/403/429 propagate immediately. Repeated malformed proposals still stop after two total calls.

Synthetic fixtures use the formal X4 utterance and production strict schema with real validated owned-read envelopes; they are explicitly not claimed to be captured production model output.

Verification on 2026-11-01:

- Before production change: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/agent/test_nov1_x4_plan_retry.py -q -p no:cacheprovider` → **3 failed, 3 passed**. Each failure showed the actual second request feedback was only `上次计划未通过校验（ValueError），请修正后重新生成。` instead of identifying the field and shape.
- After change and additional security negatives: same file covers three invalid entity shapes, preserved two-step source/target/amount/dependency, malicious-value non-echo, failed provenance after regeneration, repeated invalid shape budget, external-payee shape, malformed tool-tag safe feedback, and provider stops.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/agent/test_nov1_x4_plan_retry.py tests/agent/test_cross_scene_x4_gather.py tests/agent/test_llm_adapters.py tests/contracts/test_from_strict_plan_malformed.py -q -p no:cacheprovider` → **98 passed** at the intermediate test set.
- Final `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/agent tests/contracts -q -p no:cacheprovider` → **1044 passed, 39 skipped**. Skipped paths are not passed live/PG evidence.
- Changed-file `ruff check` and `ruff format` passed. Initial lint caught line lengths; formatting corrected them before final verification.

No API/model/browser/Docker/DB calls, no generation reset, no shared AGENTS/PRD edits. Root still owns independent review, PG/CI, and any future authorized production-model verification. This component proves actionable bounded recovery feedback and safe outcomes under scripted transport; it does not prove formal case37 production-model success or full X4 financial execution.
