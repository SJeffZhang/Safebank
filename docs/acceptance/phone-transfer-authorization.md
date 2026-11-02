# T-1 saved-phone transfer authorization

Date: 2026-10-31. Branch: `fix/phone-transfer-authorization`; parent `0a1a89c45d2a573fe560ddb40949e1fa07f0c341` (assessment/context work remains on its original branch).

## Scope and authority

PRD T-1 requires name/alias/phone payee matching. The frozen internal `Payee.phone` is an optional saved contact field, owned by `Payee.customer_id`. `DirectoryEntry` has no phone field. L3 `list_payees` searches the saved phone but returns only `phone_masked` to L1, so search support alone did not authorize a typed phone transfer.

Old parser `_payees` and the transfer exact-match branch considered only name/alias. The existing scalar provenance matcher also did not recognize a payee phone span. Coordinating parent explicitly extended this task's ownership to the narrow `taint/text.py` payee_id phone branch; no other provenance rule was changed.

Input: a full typed transfer/scheduled-transfer clause containing the complete 11-digit saved phone of the current customer's payee. Output: the existing payee target fingerprint and amount parsed from that same command, or clarification/no write scope. Parser accepts optional 手机号/手机号码 target labels; a provenance span must be the exact phone digits alone. The shared pure helper applies only NFKC compatibility normalization and rejects whitespace, separators, masked/partial numbers or non-string phone data. No search, contact creation, external-account inference, new state or new trust type is introduced.

Only the saved `Payee.phone` matches a phone-looking target; numeric aliases/names and account-number coincidence cannot supply phone authority. Same phone on multiple owned payees produces the existing payee clarification. The scalar provenance extension is only `payee_id`, requires one same-customer saved match and leaves the existing persisted current-root/JWT/liveness/epoch/intent checks intact. It does not use a scope value as source evidence.

## Red/green verification

- First 28 pure regressions on old code: **10 failed, 18 passed**. Missing legal phone matching, duplicate-phone clarification, numeric-alias rejection, and phone-source matching caused the failures.
- Added actual ProvenanceStage tests using in-memory persisted-root/evidence query fixtures. The initial fixture tried to evaluate an unused joined read-ledger query and raised AttributeError; the test now explicitly supplies an empty read ledger, since no read-backed parameter participates. This was a fixture error, not counted as a product failure.
- Finalized 32 regressions, temporarily replacing only owned parser/text production files with exact parent code and restoring the repair in `finally`: **11 failed, 21 passed**. Restored repair: all included in the **1933 passed** broader pure suite below.
- `.venv/bin/pytest tests/gateway/w1/test_*.py tests/gateway/w2/test_*.py tests/gateway/w3b/test_*.py tests/gateway/w4b1/test_*.py -q --tb=short -p no:cacheprovider`: **1933 passed**.
- Changed Python files: Ruff check/format pass; `git diff --check` passes.

Coverage includes immediate/scheduled phone transfer, whole-width digit normalization, target-label grammar, duplicate owned phones, unknown/partial/masked/reformatted phone rejection, numeric-alias/account coincidence, other-customer phone rejection, quote/question/negation/conditional/cancellation guards, exact current typed span acceptance, old undeclared root rejection, shortened/expanded span rejection and no scalar promotion for other parameters.

## Limits and remaining gates

No real DB/API/LLM/Docker/browser/deployment/record/replay was run; no push/merge or shared AGENTS/PRD edit. Coordinator owns independent security review and required full local CI/real PG checks.

All payee phone fields in the current `l1_seed_v1.json` are null; the tests add a synthetic authoritative saved contact phone. This does not pretend that the existing demo contacts already have recorded phone numbers. International/prefixed country-code, partial, spaced or hyphenated phone formats remain unsupported and do not gain authorization through this feature. No seed data was changed.
