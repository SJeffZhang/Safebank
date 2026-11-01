# November 1 independent understanding / SSE error review

**APPROVE** for exact head `b27a3c802ce64c5ac3f01d5a5f3090a264e15e20`, parent `4f2240362a699eec5f9e61a8aeaad8ab99afa057`, within the requested understanding-feedback/SSE-error scope. No actionable finding from this review.

## Exact archive and commands

Target exported with git archive to `/private/tmp/sbg-nov1-repairs/review-errors`. Only the exact-head diff in `services/agent/app/llm/understand.py`, `services/agent/app/main.py`, the two NOV1 tests and acceptance was reviewed. Root WIP and shared documents were not modified.

Using the existing Python environment with `PYTHONDONTWRITEBYTECODE=1`:

- `python -m pytest tests/agent/test_nov1_understanding_errors.py tests/agent/test_llm_adapters.py -q -p no:cacheprovider`: **68 passed in1.28s**; `review-errors-author.log`.
- `python -m pytest -c pyproject.toml /private/tmp/sbg-nov1-repairs/test_review_errors.py -q -p no:cacheprovider`: **10 passed in0.90s**; `review-errors-independent.log`.

All logs/tests are under `/private/tmp/sbg-nov1-repairs`. No DB, provider/API/live model, browser, Docker or deployment was used. Root's two actual Agent-PG results remain root-owned evidence and were not independently rerun.

## Understanding structure feedback

`_structure_retry_note` emits only fixed schema field names and bounded numeric indexes, with at most five error locations. It never emits str(exc), raw input, arbitrary extra-key names, error messages or validator context. The span guidance is a fixed zero-based half-open interval rule; it does not overwrite a rejected span or create a guessed replacement. The original user's text is still supplied unchanged as understanding input, rather than substituting model-proposed text.

Independent cases placed a unique arbitrary key/value canary at the top level, inside a slot, inside a wrongly typed nested field, and alongside an invalid-span validator error. Neither the generated retry suffix nor public SSE payload contained the canary, input_value, Pydantic documentation URL or traceback. Each invalid→valid case still used exactly two calls. The existing strict validation remains in force; two invalid structures still terminate at the original one-retry budget.

Provider invoke remains outside the structure-error catch. Existing direct402/403/429 tests stop after one call. Independent tests additionally supplied one invalid structure followed by each of402/403/429: the second error propagated with exactly two total attempts, never a third model call or automatic repair. Public payload retained only the stable provider-limit code and integer status, without provider body text.

## Public SSE safety and execution-state wording

`main._stream_error_payload` returns stable fixed code/type/message strings. Structure/tool-output errors use MODEL_OUTPUT_INVALID; other unexpected errors use AGENT_REQUEST_FAILED;402/403/429 retain PROVIDER_LIMIT_REACHED plus numeric status_code. It does not inspect or stringify exception input/context/provider response bodies.

The public messages deliberately do not assert that business execution was zero. Independent tests exercised the actual FastAPI stream endpoint and compiled basic graph with an InMemorySaver, injected pure test ports, identity dependency override and a no-DB thread-lock double. In each case the execution test port returned first, then respond raised an unexpected exception, ValidationError or provider-limit error. Call order was execute→respond→lock close; the actual SSE response was status200 with one error event, preserved message/type plus stable code, no canary/validator diagnostics, and no claim of “未执行/零执行”. This is real endpoint/graph error-path testing with memory state, not real L3 banking execution or JWT/PG acceptance.

## Compatibility / limits

The existing SSE error event framing and message/type fields remain available; added code/status_code fields are additive. BFF accepts the existing type/message shape, and frontend error handling still consumes message. The stable type value no longer exposes Python exception class names. HTTP authentication handling is unchanged by the diff.

No model, retry or authorization policy was relaxed, no failed span was auto-corrected, and no post-execution error was translated into a claim of bank rollback. Server-side exception logging remains diagnostic; these tests establish the public boundary, not a new logging-retention policy. The unchanged HTTPException/thread-lock paths and actual PG checkpoints remain outside this pure review's runtime coverage. Full integrated-head CI, root PG gates and any future formal case25 live rerun are still required separately.


## Final integrated CI follow-up — legacy SSE test contract

**APPROVE** for the single test-only adjustment in exact `2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f` relative to its parent. Read-only git diff confirms changes are limited to `tests/agent/test_sse_wire.py` and documentation; no Agent production mapping, logging, privacy or authorization code changed.

The old assertion expected public SSE.type=TypeError, which conflicts with the already-reviewed b27 public error contract. The replacement asserts AGENT_REQUEST_FAILED and AgentError, and adds explicit rejection of both internal class name TypeError and the original “string indices” exception message in the complete collected error events. The injected exception remains a real TypeError from the same stream path. Thus this updates the expected public contract and strengthens the privacy negative assertions; it does not accept arbitrary errors to make CI green.

The server-side log assertions remain: exact “run stream failed” record must exist, its exc_info must be present, and the new additional assertion requires exc_info[0] is TypeError. Neither the original exception nor diagnostic logging is replaced with the public AgentError. No assertion was removed that required server exception information; the new check more specifically proves the logged exception identity.

Root reports the preceding b913 full CI stopped at this old test (1 failed /41 passed), then the corrected complete AGENT_DB group yielded42 passed. These are root-owned runtime results, not rerun by this reviewer. **b913 is not CI PASSED**. The final changed head must run the entire CI from the beginning; this test-only review and Agent group result do not substitute for that. Root's preserved old DBprd01 and planned nextprd02 are not altered or independently inspected here. No DB/provider/API/browser/Docker/service execution was performed for this follow-up.
