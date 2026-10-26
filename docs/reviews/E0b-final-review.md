# E0b Final Review — Runner & Evidence

**Reviewer**: DeepSeek V4 Pro (independent, read-only)
**Scope**: `6ac6cbd..HEAD` in `evaluation/` and `tests/evaluation/` only
**Date**: 2026-10-27

---

## 0. Checklist

- [x] ① Anti-fabrication: no path from expected → actual; anti-fab tests actually catch
- [x] ② Secret stripping: live key via env only; outputs/logs/reports never leak key; `_strip_secrets` regex correct
- [x] ③ StopRun: covers 402/403/429/connection error/L2 429/request cap; partial results written
- [x] ④ Login reuse: doesn't bypass L2 rate limits or relax L2 rules
- [x] ⑤ Evidence fields: clarify_source, understanding, l2_g3_response, gather_reads, error_message/where, g4_decision/findings → cases.jsonl
- [x] ⑥ Metrics: definitions not changed from original contract

## 1. Findings

### P0 — none found

### P1 — none found

All six checklist items verified clean. Detailed analysis below.

### P2 — 3 minor observations, all non-blocking

**P2-1**: `_JWT_RE` requires ≥10 Base64url chars in the second and third segments (`{10,}`). A JWT with an extremely short payload (e.g. `eyJ.eyJhLml0.dGVzdA`) would not match. Real JWTs always have substantial payloads; the tightened regex is a strict improvement over the old `[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+` which falsely matched `steps.0.arg_sources.2.source`. Non-blocking.

**P2-2**: `_SECRET_KEY_RE` contains `auth` as a substring, so fields like `authorization` / `auth_header` get their entire value replaced by `[REDACTED]` before Bearer/JWT substring replacement runs. This is correct and conservative — fields named after auth concepts should have their whole value treated as a secret. The substring replacements still apply to non-key-matching fields like `error_message`. Non-blocking.

**P2-3**: `_extract_error_info` filters traceback frames to only `services/` and `evaluation/` paths (max 3 innermost). Frames in Python stdlib, site-packages, or project root are silently skipped. This is by design to avoid leaking framework paths, but means some error sources in `packages/` (contracts, bankcore) won't appear in `error_where`. Non-blocking — `error_message` still captures the exception text.

---

## 2. Detailed Verification

### ① Anti-fabrication — VERIFIED

**No expected→actual path exists.** All actual results come from the real gateway or the real L1 graph state:

- `classify_outcome` (loop.py:76–197) is a pure function whose inputs are `transport.requests` (recorded real HTTP calls to G4/G21), `last_state` (real L1 graph state dict), `last_error` (real exception), and `g3_responses` (from `register_utterance`). Expected values are never passed to it.
- `run_corpus` (loop.py:200–402) reads `expected_outcome` / `expected_risk` / `expected_params` from the case dict and stores them directly into `CaseRun.expected_*` fields — these are the reference labels, never fed into actual-outcome computation.
- `CaseRun` dataclass (compute.py:71–117) separately tracks `expected_*` and `actual_*` fields.

**Anti-fabrication tests actually catch the old fabricated implementation**:
- `test_anti_spoofing_tweaked_expected_does_not_change_actual` (test_cli.py:69–125): The original fabricated CLI from T7 would have copied `expected.decision` into `actual_outcome`. This test force-sets expected=DENY on a non-attack case, runs real Docker+tmpfs+gateway+L3, and asserts `actual_outcome != DENY` (unless the original expected was also DENY). If code path ever reverts to fabrication, this test catches it immediately.
- `test_l3_real_tmpfs_in_meta` assertion in `test_fake_limit_1_creates_output_through_real_g4` (test_cli.py:65–66): Proves `l3=real-tmpfs` in every report, confirming the CLI provisions real tmpfs PostgreSQL and connects to a real gateway — not fake/mock.

### ② Secret stripping — VERIFIED

**DEEPSEEK_API_KEY read from env only, never printed**:
- `_live_ports_factory` (cli.py:353–377): Reads `os.environ.get("DEEPSEEK_API_KEY")` via `make_llm()` which internally reads the env var. The factory never prints it.
- `_run_eval` (cli.py:475): `api_key = os.environ.get("DEEPSEEK_API_KEY", "")` — only used for empty check, never logged.
- `main()` (cli.py:657): Checks mode, sets limit, no key access.

**`_strip_secrets` regex — correctly tightened, neither too broad nor too narrow**:
- JWT regex: `eyJ[A-Za-z0-9_-]{7,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}` — requires the `eyJ` header prefix (Base64url of `{"alg`), correct Base64url charset (`A-Za-z0-9_-`), and minimum segment lengths. Old regex `[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+` matched arbitrary three-dot strings including `steps.0.arg_sources.2.source`.
- Bearer regex: `\b(Bearer)\s+\S+` with capture group — now does substring replacement (`Bearer [REDACTED]`) preserving surrounding text, instead of whole-string redaction. `test_error_message_stripped_of_bearer_token` confirms diagnostic prefix "401 Unauthorized" survives.
- Key-name regex unchanged — strips `*token*`, `*key*`, `*secret*`, `*password*`, `*pin*`, `*bearer*`, `*jwt*`, `*auth*`, `*credential*` fields.

**Comprehensive test coverage** (test_case_evidence.py + test_error_diagnostics.py + test_cli.py):
- JWT embedded in text → only JWT substring replaced (T12)
- Bearer in mid-string → only token replaced, prefix kept (T12)
- ValidationError paths like `steps.0.arg_sources.2.source` NOT mistaken for JWT (T12)
- Lowercase bearer (P1-2)
- Tuple headers (P1-1)
- Sets/frozensets (P2-3)
- Depth limit placeholder (P2-1)
- PIN, secret, jwt key fields
- Nested lists
- Business fields (card_id, payee_id) preserved
- Sentinal leak test: DEEPSEEK_API_KEY never in stdout/stderr/output files (test_cli.py:133-187)
- Fake key "sk-test" not leaked in StopRun output path (test_cli.py:299-305)

### ③ StopRun — VERIFIED

**Coverage matrix**:

| Error condition | Catch point in loop.py | Raises StopRun |
|---|---|---|
| 402 in exception message | line 308–309 (in-graph try/except) | ✅ |
| 403 in exception message | line 308–309 | ✅ |
| 429 in exception message | line 308–309 | ✅ |
| `ConnectionError` | line 310–311 | ✅ |
| `httpx.ConnectError` | line 312–313 | ✅ |
| `GatewayRateLimited` (L2 429) | lines 390–391 (outer try/except) | ✅ |
| 402/403/429 in outer except | lines 394–399 | ✅ |
| `max_requests` exceeded | lines 316–321 | ✅ |

**Partial results preserved**: Every `StopRun` raise carries `runs` — all completed CaseRuns before the error. `_run_eval` handles `StopRun` gracefully (cli.py:507–524, 564–581), calls `compute_metrics` + `write_outputs` on partial runs, returns exit code 3.

**Tests**: `test_stop_run_on_402` (real Docker), `test_live_402_stopped_by_run_eval` (mocked), `test_429_on_create_session_stops_with_runs` (real Docker + monkeypatched harness), `test_live_max_requests_default_in_parser`.

### ④ Login reuse — VERIFIED

**Login caching** (harness.py `new_session`, lines 173–191):
- `_login_cache: dict[str, str]` per EvalHarness instance
- First `new_session(customer_id)`: fresh `/auth/login` → cache token → `/sessions` → return sid
- Subsequent: reuse cached token → `/sessions` → return sid
- `GatewayRateLimited` raised → propagates (becomes `StopRun` in `run_corpus`)
- Other exceptions (e.g. 401 expiry) → `del self._login_cache[customer_id]` → fresh login

**Does NOT bypass L2**: `_check_rate_limited` is called on both `_login` and `_create_session` responses. Any L2 429 response becomes `GatewayRateLimited` → `StopRun`. Login caching reuses the same gateway token and session-creation path — it doesn't invent tokens or skip gateway calls.

**Tests**: `test_same_customer_logs_in_once` (5 sessions → 1 login), `test_429_on_create_session_stops_with_runs`, `test_401_on_cached_session_triggers_relogin` — all pass against real Docker+gateway.

### ⑤ Evidence fields — VERIFIED

All six evidence categories are written to `cases.jsonl` via `write_outputs` (compute.py:482–523):

| Field | Source | Strip secrets? |
|---|---|---|
| `clarify_source` | `classify_outcome` from transport + G3 evidence | N/A (enum string) |
| `clarification` | `last_state["clarification"]` dict | ✅ `_strip_secrets` |
| `understanding` | `last_state["understanding"].model_dump()` or dict | ✅ `_strip_secrets` |
| `l2_g3_response` | G3 responses from `register_utterance` | ✅ `_strip_secrets` |
| `error_message` | `_extract_error_info` (str(exc)[:800]) | ✅ `_strip_secrets` |
| `error_where` | `_extract_error_info` (traceback frames) | ✅ `_strip_secrets` |
| `gather_reads` | `extract_gather_reads` (transport POST /tools/read/) | ✅ `_strip_secrets` |
| `g4_decision` | `extract_g4_evidence` (last successful evaluate) | N/A (string) |
| `g4_findings` | `extract_g4_evidence` | N/A (list of dicts) |

**Understanding on ERROR** (loop.py:347–355): When `actual_outcome == "ERROR"` and `last_state` is available (multi-turn case where a previous turn succeeded), `understanding` is extracted from the pre-error state. Verified by `test_error_with_understanding_from_last_state` (test_loop.py:425–443).

### ⑥ Metrics — VERIFIED

`compute_metrics` (compute.py:265–384) was **not modified** in this diff range. The function body is byte-identical to the baseline `6ac6cbd`. Only the `CaseRun` dataclass gained new fields (with defaults, backward-compatible), and `write_outputs` gained new serialization keys. The existing metric test `test_existing_17_metric_tests_stable` (test_case_evidence.py:630–636) passes, confirming old metric behavior is preserved.

---

## 3. Verdict

**APPROVE — no P0/P1 issues.**

Three minor P2 observations (non-blocking):
1. `_JWT_RE` minimum segment length `{10,}` won't match academic/demo tokens with tiny payloads — real tokens always pass.
2. `_SECRET_KEY_RE` whole-value redacts auth-named fields — correct behavior, not a gap.
3. `error_where` excludes `packages/` frames — `error_message` still captures the exception text.

All six checklist items verified clean. No fabrication path exists. No secret leak path found. StopRun coverage is complete. Login reuse respects L2 rate limits. All evidence fields serialized with secrets stripped. Metrics definitions unchanged.