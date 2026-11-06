# Review: L2 input robustness (commit 6a33b02)

**Verdict: APPROVE** — no P0/P1. Five P2 items, none of which loosens authorization or changes well-formed behaviour.

Reviewer: independent read-only review (Claude subagent), 2026-11-06. Scope: `git show 6a33b02` against its parent on branch `fix/l2-input-robustness`.
Commands run: `.venv/bin/pytest tests/gateway/robustness -q` gave 1 passed and 4 skipped. The 4 DB tests skip because `GATEWAY_DB_TESTS` was not set, so they were **not** run. `ruff check` passed. Two ad-hoc probe scripts lived in the session scratchpad and are not in the repo.

## Findings

### P2-1 Lone UTF-16 surrogate escapes pass the guard and can still crash
- `services/gateway/app/middleware.py:79-106` (`_input_problem`) checks for NUL only. A body such as `{"request_id":"\ud800x"}` is valid UTF-8 and valid JSON, and `_input_problem` returns `None` (checked with a probe).
- Once parsed, the string can't be encoded as UTF-8. `canonical_bytes` uses `json.dumps(..., ensure_ascii=False)` followed by encode, and it raises `UnicodeEncodeError: surrogates not allowed` (checked). The same happens when asyncpg writes the string to a text or jsonb column.
- Pydantic `str` and `Id` fields reject such strings (`string_unicode`), so every `request_id` field is safe. Fields typed `JsonValue` or `Any` accept them (checked), for example `InternalRead.args: dict[str, JsonValue]` and any free-form args or payload that reaches `digest_hex` or the database. In that case the request still becomes an unhandled 503.
- I did **not** confirm that a public route reaches this. The fuzz matrix (`tests/gateway/robustness/test_l2_input_robustness.py:20-42`) has no surrogate case, so it would not catch one either.
- Fix: in `_input_problem`, when `"\\ud"` appears in `decoded.lower()`, run `json.loads` and walk the strings (as `_has_nul` does). Reject with `invalid_unicode` if `s.encode("utf-8")` fails. Add a `"lone_surrogate"` entry to `BODIES`.

### P2-2 Client disconnect during body read produces a logged "No response returned." error
- `middleware.py:124-125`: on `http.disconnect`, the guard returns without sending a response. The trace middleware is a `BaseHTTPMiddleware` and sits outside the guard. In Starlette 1.9.0 it then raises `RuntimeError("No response returned.")` (`starlette/middleware/base.py:180`). The new `_unhandled` handler logs that as an error, and uvicorn logs it again.
- There is no client impact because the client is gone. Before this change a disconnect surfaced as `ClientDisconnect` in the handler, so this is noise, not a regression.
- Optional fix: `raise starlette.requests.ClientDisconnect()` so the cause is explicit, or skip logging `ClientDisconnect` and the resulting RuntimeError in `_unhandled`.

### P2-3 An oversized body is drained to the end instead of rejected early
- `middleware.py:121-132`: after `size > MAX_BODY_BYTES` the loop keeps calling `receive()` until `more_body` is false. Memory stays bounded because chunks are discarded, but a client can keep a worker reading an arbitrarily long stream. This was already true before the change, since there is no uvicorn or FastAPI limit.
- Suggested fix: break out and send the 422 as soon as `size` exceeds the cap, or check `Content-Length` first.

### P2-4 Unauthenticated callers now get 422 before 401 (acceptable)
- The guard runs before routing and before the app-level `caller_dependency`. A caller with no credentials who sends a NUL, bad-UTF-8, oversized or long-path request gets `422 CONTRACT_INVALID {reason}` instead of 401 or 404.
- This leaks no state or route existence: the check depends only on path length and bytes, never on whether the route exists. `details.reason` exposes only the generic limits. FastAPI already read the full body before dependencies ran, so pre-auth buffering is not new.
- Acceptable; no change needed.

### P2-5 Test coverage gaps (the assertions are otherwise genuine)
- `test_database_error_in_produce_is_reraised_unmasked` is a real regression test. With the old code, the cleanup `DELETE` on the aborted transaction raises `InFailedSQLTransaction`, which replaces the original error, so the `"division by zero"` assertion would fail. Its retry half also shows that no stale in-flight row survives.
- Not covered by the suite (I checked these ad hoc):
  - the `body_too_large` branch (the largest fuzz body is about 20 KB)
  - `%00` in a query string (only `%00` in the path is tested)
  - replay of a multi-chunk streamed body
- `_matrix` asserts only `< 500`. That is enough for the crash goal but does not show that existing 401/404 semantics are unchanged; `_targeted` covers the exact 422 code for ten cases.
- Suggestion: add one targeted case for each of `body_too_large`, query `%00`, a chunked well-formed body that still succeeds, and a lone surrogate (see P2-1).

## Verified as fine
1. **Middleware order.** `add_middleware(InputGuard)` runs before `@app.middleware("http")`, and Starlette puts later-added middleware outermost. The order is therefore trace middleware, then InputGuard, then the exception middleware, then the router, then route dependencies (including auth).
   - The trace middleware's `Request` and the guard's `Request(scope)` share `scope["state"]`, so the guard's 422 carries the same trace id as `X-Request-ID` (checked).
   - The internal app installs the same middleware.
2. **Body replay.** A probe streaming five chunks through `httpx` reached the handler with all 580 bytes. After the replay, `receive()` falls through to the original receive, so later `http.disconnect` messages are delivered correctly.
   - GET and other empty bodies take the `if not body: return None` path.
   - Lifespan and websocket scopes pass straight through.
3. **NUL detection.**
   - Path: `scope["path"]` is percent-decoded, so `/plans/pln_%00` is caught.
   - Query: a literal `b"%00"` match has no false positive; `%2500` is not flagged (checked).
   - Body: a raw `\x00` is caught directly. A `\u0000` escape is confirmed by real `json.loads` plus `_has_nul`, which walks dict keys as well as values. An escaped backslash such as `"\\\\u0000"` is correctly not flagged (checked).
   - Malformed JSON is left to the route parser, which uses the same `json.loads`. `RecursionError` in `json.loads` or `_has_nul` is caught.
4. **No route takes a non-JSON body.** I found no `Form`, `UploadFile`, `request.body()`, `request.stream()` or `request.form()` in `services/gateway/app`.
5. **The 200-character path cap** is far above every real route. The longest is `/demo/clock/advances/{job_id}` at about 85 characters with a 64-hex job id; gateway IDs from `new_id` are prefix plus 26 ULID characters. `request_dedup.route` values (method plus path) stay under `VARCHAR(256)`.
6. **`str` to `Id` (strict, 1..256 characters)** rejects no legitimate caller. Pydantic v2's lax `str` already rejected ints. The callers checked:
   - frontend: `uid()` produces `req_` plus a UUID (40 characters)
   - BFF: `req_` plus 24 hex characters
   - agent: `GatewayClient._require_id` already enforces 1..256
   - bank-backend scheduler: posts `event_id=job["id"]`, a `String(256)` column, and `request_id` stays optional
   - `request_id: null` in `Banking.tsx:429` is a read-tool argument, not one of these request models.
7. **Swallowing cleanup errors cannot leave an in-flight row committed.** All seven callers of `request_idempotent` let the exception escape from `async with conn.begin()` or `db.begin()`, so the transaction rolls back and nothing catches and commits:
   - `sessions.py` (`_dedup`)
   - `auth.py:128`
   - `approvals.py:484`, `:597` and `:857`
   - `notify_audit.py:320`
   - `plans.py:522`

   Only `SQLAlchemyError` is swallowed; `CancelledError` and other exceptions still propagate.
8. **G8 (`execution.py:1187-1191`).** A JSON decode error and the non-existent `Code.INVALID_REQUEST` both used to produce 503; now both give 422 `CONTRACT_INVALID` with no internals in the response.
9. **Logging.** It records trace id, method, path (which holds only IDs, not secrets) and `exc_info`. The engine is created with `hide_parameters=True` (`db.py:17`), so SQLAlchemy errors do not include bound parameters; no request bodies, tokens or OTPs are logged.
   - Exception messages such as pydantic's `input_value` could include data, but uvicorn already logged the same traceback, because `ServerErrorMiddleware` re-raises after the handler. No new exposure category; the logs are now duplicated.
10. **No authorization, taint or approval rule changed.** The diff touches only input shape, error codes and logging.
