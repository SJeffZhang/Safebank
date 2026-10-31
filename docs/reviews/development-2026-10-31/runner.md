# Final incremental review — APPROVE

Final candidate: `2548f49e5e1ba0f368da991e5738ef19734b157b` (includes R1 `d357353c2d28214f551153506edc7c863349ce7d`).

**Verdict: APPROVE. R1 and R2 are CLOSED for this exact candidate.** No new blocking issue found in the reviewed increments. This certifies the code review and pure checks only; real recording/offline/SQL/CI and serial integration remain root gates.

## R1 closure

- `scripts/demo_runner.py:492` creates a fresh128-bit run nonce independent of a selected directory basename; `:538–543` adds a fresh per-case UUID to the operator trace path and driver environment.
- The trusted driver forwards `TRY_AGENT_REQUEST_ID` as a real `--request-id` argument. `scripts/try_agent.py` validates it with `uuid.UUID` before PIN prompting, uses it as Config.request_id, and preserves its unchanged default generation when absent. No authorization rule, credential or L2 identity is replaced by this trace-correlation field.
- `demo_runner.py:575` now requires the actual trace config.request_id_prefix to equal this case's UUID before accepting a trace. A startup exception exit1 therefore cannot reuse a previous matching customer/text trace. The two-parent/same-basename probe now rejects and preserves the prior trace. Legitimate COMPLETED controls, optional-UUID parsing and actual driver argv forwarding are included in the independently rerun suite.

## R2 closure

- One `_is_sensitive_key` predicate covers structured fields, recoverable nested JSON and malformed-field token detection, including Unicode-decoded/NFKC/Cf-normalized field names.
- Valid JSON strings are recursively sanitized; decoded known credential values are also scrubbed from echoed text/numbers. Malformed secret-bearing strings raise a fixed `DemoFailure` before `:586` can write the case trace. The string is not repaired into an executable valid call, and benign malformed call content remains malformed.
- The same sanitized config.text is reused for manifest typed_text (`:591`), so metadata does not copy the unredacted input around the trace scrubber. The malformed-sensitive orchestration regression confirms no case trace is written, manifest cases remain empty/FAILED, and later cases do not run. Normal monetary/source data and legitimate report controls pass.
- As with any label/value-based defense, this component cannot identify arbitrary unknown unlabeled secrets. The architecture must continue excluding actual credentials from L1 prompts; this is not a new permission or secret-discovery mechanism.

## Fresh independent evidence

1. Exact final candidate affected runner/path/lifecycle/all try_agent suites: **160 passed /3.82s**, exit0. Database flags zero, pytest cache disabled, no real API/Docker/DB call. Log: `/private/tmp/sbg-demo-runner-incremental-review.log`.
2. Independently loaded original `a338644` runner source into an isolated Python module without editing checkout files, then reran ten final R1/R2 regressions: **10 failed /59 deselected /0.15s**, exit1. These cover same-basename stale trace, four structured/nested sensitive cases, four malformed/Unicode cases, and manifest metadata copy. Log: `/private/tmp/sbg-demo-runner-review-old-red.log`.
3. Scoped ruff check/format and incremental diff whitespace check passed. First ruff command hit a managed-worktree cache permission error; rerun with `--no-cache` passed, without escalation or changing files.
4. Final `git status --short` empty; HEAD exactly2548f49. No implementation or existing test file was changed by this reviewer. Only this report was updated.

Root's planned integration of a separate independent read-only SQL oracle for current receipts is outside this incremental diff and is not a defect. The complete frozen demonstration still requires actual generation/clean-image identity, live recording, physical external network blocking with L2/PG available, per-case real SQL, two reset six-scene rounds, honest43 table and final full exact-head CI. This approval does not certify a demo-v1 tag.

---

# Initial review history (superseded by final approval above)

# Demo runner independent safety review

Verdict: **REQUEST_CHANGES** — two reproducible P2 blockers below.
Candidate: `a338644ce5fcf0aa4093c6a9bcb3e0b2a4b982ed`.
Base: `ccf5d3330b44f4d5049fbbdb5bf5d78ac352ba1b`.
Worktree: `/Users/jet/.codex/worktrees/safebank-demo-runner/WeBank`.

## Scope

Read common/task4 brief/report, current AGENTS §§1–3/8 under the coordinator's scoped-reading ruling, task-relevant PRD replay/deployment clauses, all seven changed files, and adjacent trusted driver/try_agent trace plumbing. Application checkout remained read-only and clean. Only this report was written persistently; additional pure probes used temporary directories that held synthetic inputs. No real Docker, provider, database, secret-file, deployment, push/merge/tag or application edit performed. Root retains actual offline and business SQL gates.

## R1 — P2: reusable run-directory basename permits a stale trace after driver startup failure

Locations: `scripts/demo_runner.py:381`, `:387–421`; adjacent existing driver `docs/handoffs/2026-10-30-tools/drive_try_agent.py:84` (PIN-file load can raise before any new trace is written).

`TRY_AGENT_OUT` is `/tmp/demo-<run_directory.name>-<case.id>.json`. Exclusive creation checks the entire host run directory, but different parents may use the same basename. Thus `one/same-name` and `two/same-name` request the same operator-container trace path and both have `run_id=same-name`. The runner neither supplies nor verifies a fresh invocation/request identifier. It accepts process exit1, which is both try_agent COMPLETED and Python's unhandled-startup-exception exit. Matching customer/text/exit plus nonempty old events pass the current validation.

Independent pure reproduction used the existing `tests/test_demo_runner.py` synthetic `environment` fixture, without modifying it or the implementation:

1. Run one case `Case('a','cus_zs','first')`, replay observation, output `tmp/one/same-name`.
2. Create a new `DemoRunner` for the same fake deployment and same case, output `tmp/two/same-name`.
3. At its driver subprocess boundary, return exit1/stderr `Traceback: PIN file unavailable`, writing no new trace. At its operator `cat` boundary return the first run's matching trace with `request_id_prefix='old-request'`.
4. Both runs return `status=OBSERVED` with one case. Both driver environments have exactly the same `TRY_AGENT_OUT` value. No new model/banking request occurred in step3.

Measured probe output:

```json
{"probe":"stale trace after driver startup exit1","paths_identical":true,"first_status":"OBSERVED","second_status":"OBSERVED","second_cases":1}
```

This does not by itself bypass L2 or manufacture a valid SQL attestation; it falsely accepts an old observation and can hide a failed new driver invocation, contrary to the component's current-trace gate.

Required narrow fix: assign a fresh unpredictable invocation identifier independent of a user-selected directory basename, bind each expected trace to that invocation/request, and reject startup exit1 when no matching new trace exists. Preserve old runs and avoid overwriting/deleting user data. Add the two-different-parent/same-basename/startup-exception regression, plus a normal successful COMPLETED control. Merely checking the directory again or the old matching customer/text is insufficient.

## R2 — P2: explicit credential variants survive trace sanitization and are written to evidence

Locations: `scripts/demo_runner.py:132–151`, used immediately before `:422–425` writes the purported sanitized trace.

Structured key recognition only accepts exact `pin`/`otp`, although related explicit keys such as `otp_code` are credentials. The free-text regex also fails on quoted JSON keys because the quote intervenes between `pin` and the colon. Nested JSON strings are common in tool/model event content, so the recursive dict walker is not sufficient.

Independent pure probe:

```python
sanitize_trace({
    "otp_code": "276543",
    "pin_json": '{"pin":984321,"ok":false}',
})
```

Measured unchanged output:

```json
{"otp_code":"276543","pin_json":"{\"pin\":984321,\"ok\":false}"}
```

These are synthetic sentinels, not project credentials. No actual secret file was read. They establish an artifact-cleaning defect; this review does not claim the current normal CLI exposes a real OTP/PIN via this path. Existing CLI header/request masking still applies, but this new defensive layer and its “sanitized trace” contract must not persist explicitly labeled credentials if they reach event data.

Required narrow fix: use a consistent credential-key classification and parse/clean recoverable nested JSON strings, or conservatively refuse to persist secret-bearing strings that cannot be safely sanitized. Preserve normal amounts, source IDs and malformed-call failure semantics. Do not convert a malformed call into a valid authorization-bearing call or grow an endless suffix regex list. The independently approved runtime recorder at `a2f9d86a3e4131d2603778f6cdfe51f15e73e9e6` is available as a strategy reference; do not change its implementation as part of this runner fix. Add structured `otp_code`, valid quoted numeric PIN JSON, and malformed/Unicode-sensitive-string negatives, with harmless financial content controls.

## Checks that passed static review

- Subprocesses use argv lists, without shell interpolation; case IDs are bounded and duplicate/missing selections fail. Generation names and checkout SHAs are validated. Existing run directories and files are exclusive, and no database/volume/network/worktree deletion command is introduced.
- Portable deployment selection is explicit argument → dedicated environment → current directory containing the override, otherwise an actionable failure. It does not guess an old machine location or reset implicitly. Existing lifecycle corruption/rollback tests remain present.
- Replay requires every inspected attached agent network to be Docker-internal, refuses missing/host/none inventory, rechecks after agent recreation, and requires gateway health/PG TCP plus direct external TCP failures. These are actual runtime checks in code, not an environment-only assertion. Real Docker proof remains unrun here.
- Provider75 and timeout124 stop before the next case; missing/invalid/unknown driver outcomes stop the batch. The process-group timeout path kills local driver descendants and withholds raw output. Real in-container disconnect/cancellation behavior remains a coordinator deployment gate.
- Runtime overlays are exclusive0600, preserve the generation Compose input, pin B2 case/profile, mount recordings read-only in replay, and clear the replay API key. No new live fallback is introduced.
- Receipt attachment requires a complete OBSERVED case list in exact selected order, matching run/SHA/generation/case/tracehash, SQL verifier, strict true flag and nonempty strictly true checks; separate verify-run rehashes traces. Receipts are explicitly trusted coordinator SQL attestations, not built-in validators or signatures. That documented limitation is not a defect.

## Independent verification

- Exact candidate affected suites, with real database flags zero and pytest cache disabled: **76 passed /4.12s**, exit0.
- Command: `PYTHONDONTWRITEBYTECODE=1 GATEWAY_DB_TESTS=0 OBLIGATIONS_DB_TESTS=0 AGENT_DB_TESTS=0 GATEWAY_SECRET_TESTS=0 /Users/jet/Downloads/WeBank/.venv/bin/pytest -q -p no:cacheprovider tests/test_demo_runner.py tests/test_demo_deploy_path.py tests/demo_environment tests/try_agent/test_live_driver_guard.py`.
- Log: `/private/tmp/sbg-demo-runner-independent-review.log`.
- Two additional pure probes above reproduced the untested defects without modifying the checkout or existing test files.
- Final checkout identity/status rechecked separately before returning this report.

## Remaining coordinator gates

After R1/R2 red/green and independent incremental review: serial rebase/integration after AA, bill and runtime factory; precise-code clean image/deployment; actual final production recording and blocked-network replay; real L2/L3/approval/OTP/clock and independent SQL receipts; two fresh six-scene rounds and honest43 table; final whole-workflow exact-SHA CI and shared docs. No demo-v1 freeze/tag is certified by this review.
