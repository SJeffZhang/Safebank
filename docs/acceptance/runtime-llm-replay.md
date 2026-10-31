# Production LLM record/replay transport acceptance

Date: 2026-10-31. Base: `ccf5d3330b44f4d5049fbbdb5bf5d78ac352ba1b`.
Scope: `services/agent/app/llm/{client,replay}.py`, new `tests/agent/test_runtime_replay.py`.
No real model/API call, Docker operation, database mutation, deployment, push, merge or tag was performed. No secret files were read. Tests use existing local Python 3.12 environment and synthetic credentials.

## Runtime contract

All production adapters already share `make_llm()` in `services/agent/app/main.py`; the factory now selects `LLM_MODE=live|record|replay` (default `live`). Existing `make_llm(api_key=None, http_client=None)` remains supported. DeepSeek `deepseek-v4-pro`, `/beta`, temperature 0, thinking disabled and SDK max_retries 0 remain fixed.

Production modes record/replay use `LLM_PROFILE=B2` by default (only B1/B2 accepted), `LLM_CASE_ID=default`, and `LLM_REPLAY_ROOT=evaluation/replay`. Path: `<root>/<profile>/<case>/replay.jsonl`. Case names permit 1–80 ASCII letters/digits/underscore/hyphen and begin with a letter/digit. These are deployment environment settings, never user/caller request options. Component-only legacy default `b1/default` is preserved.

Coordinator must expose a persistent directory to the agent, writable for record and readable for replay, e.g. mount a host recording directory at `/app/evaluation/replay` and set `LLM_REPLAY_ROOT=/app/evaluation/replay`. Set mode/profile/case in the agent process environment and restart that process when changing them. No Compose or runner changes are included here. Live/record use the usual real API credential; replay uses an inert SDK placeholder and does not load `.env`, require a key, construct a live HTTP transport, or use an injected provider client. Both invoke/ainvoke use only in-process replay handlers.

Recordings append successful provider JSON responses; HTTP failures propagate without recording or retry. One header is maintained across factory instances, with file locking plus fsync and atomic replacement, private 0600 files. Interrupted replacement retains the previous complete recording. Repeated canonical keys preserve the ordered response sequence; exhausting the sequence is an explicit error, not endless reuse.

Schema fingerprint covers actual understanding/plan/read tool schemas, all system prompt constants, model settings and source hashes for prompt-building/replay code. Implementation changes conservatively invalidate recordings, including formatting-only changes in these modules. Freeze the final integrated code before collecting real recordings. Header schema/profile, entry shape and format-2 checksums are validated; stale/missing/corrupt/miss never fall back to live.

Request canonicalization shares one ID map across all payload fields, removes wall fields (including JSON message content), normalizes runtime ULID/UUID bank IDs, and structurally normalizes provider tool-call IDs/references. Provider IDs such as `call_00_sDkWb8RQFLPMXzFy3j6n7444` are covered. Recorded response source/utterance references map back only to IDs in the current request. New provider call IDs are generated locally for replay; unbound bank IDs/placeholders fail closed. Seed IDs and business values remain meaningful. Replay does not cache or replay L2 approvals/executions: downstream ports still perform real L2 validation and authorization.

No request messages or HTTP headers are persisted. Sensitive structured fields, JSON-string arguments (including malformed quoted fields), labeled plain-text credentials, known request-header credentials and their response echoes are scrubbed. Token usage counters remain intact. This is defense in depth: PIN/OTP/JWT must still stay outside LLM prompts, and arbitrary unlabeled free-text secrets cannot be inferred by a generic recorder.

## Actual validation

Commands use `/Users/jet/Downloads/WeBank/.venv/bin/python` from the isolated worktree.

- Initial factory TDD: 6 failed / 3 passed. Failures proved missing production recording, key-required replay, and absent stale/profile/corruption enforcement.
- Async stream test: 1 failed / 66 passed before fix; sync provider response streams could not be returned directly to AsyncClient. Fixed by thread dispatch and detached response content.
- Secret red tests: malformed quoted PIN/OTP and generic token fields failed; fixed and rerun. Numeric secret echo separately failed JSON parsing under raw serialization replacement; fixed with structural value replacement.
- Final `pytest tests/agent/test_runtime_replay.py tests/agent/test_replay.py -q`: **73 passed** (26 new runtime tests, 47 existing component tests).
- Final `pytest tests/agent -q`: **661 passed, 39 skipped**. Skipped external environment tests are not claimed as passed.
- `ruff check` and `ruff format --check` on owned Python files, plus `git diff --check`: **passed**.
- Final project default `NO_PROXY=localhost,127.0.0.1,::1 pytest -q`: **3602 passed, 1071 skipped in 33.59s**, exit 0. Log: `/tmp/sbg-runtime-replay-final-pytest.log`. No external test-enabling environment flags were set.

## Unverified and coordinator gates

MockTransport checks prove transport/factory behavior, not six-scenario banking correctness or actual model stability. Real production recordings, independent reset/fresh-ID replay against L2/L3, network-disabled container proof, demo runner wiring, full local CI and independent security review belong to the coordinator. This work supports the current non-streaming production adapters; streaming/SSE provider recording is outside this component. Do not label `demo-v1` frozen based on these unit results.

Shared docs to update during integration: AGENTS §3/§4 status and actual commands; PRD §6.6 physical file layout currently says `<profile>/<case_id>.jsonl`, while the approved factory/runner contract here uses `<profile>/<case_id>/replay.jsonl`. Those shared files were not edited by this task.

## Independent review correction round 1

The initial commit was REQUEST_CHANGES on R1 (P1 literal-placeholder source laundering), R2 (P2 numeric secret in malformed JSON), and R3 (P2 header format downgrade). The three cases were reproduced before changing production code: **20 failed, 26 deselected** in `/tmp/sbg-replay-review-red.log`.

- R1: literal `<` and escape-alphabet characters now have reversible encoding before recorder-generated references are introduced. Replay decodes literal spans separately and never reinterprets inserted current IDs. Literal `<ID1>`, `<CALL1>`, `<NEWCALL1>`, `<UNBOUND1>` and escape-looking text retain their original meaning in both tool arguments and free text; a literal request placeholder cannot collide with an actual source request. Valid embedded JSON is decoded before normalization, closing the separately reproduced Unicode-escape variant (`\u003cID1\u003e`) and allowing genuine Unicode-escaped runtime IDs to rebind correctly. Actual unbound runtime IDs still fail closed. The earlier literal `<ID999>` check now correctly expects preserved literal text, rather than treating that text as a generated reference.
- R2: malformed argument fallback now scrubs unquoted credential values through structural delimiters, covering integer, exponent and incomplete numeric values, while preserving unrelated amount fields. Escaped ASCII secret field names are decoded before matching; an additional `p\u0069n`/`o\u0074p` red test was reproduced and fixed. Existing quoted/valid-JSON/echo scrubbing remains covered.
- R3: all loaders and record-file reopening require exact integer format 2; missing, downgraded, unknown, string and boolean versions reject before provider IO. Every entry checksum is mandatory. Legacy unit fixtures were explicitly migrated to format 2 with computed digests; there is no implicit legacy production fallback.

Final correction checks: runtime+existing replay **95 passed**; all Agent **683 passed, 39 skipped**; scoped ruff check/format and diff-check passed. The production source fingerprint changes, so prior recordings require re-recording. No real model/network, Docker, DB, shared document or deployment actions occurred during this correction. Independent re-review and coordinator live/offline integration gates remain open.

Final correction project-default run: `NO_PROXY=localhost,127.0.0.1,::1 /Users/jet/Downloads/WeBank/.venv/bin/python -m pytest -q` → **3624 passed, 1071 skipped in 41.17s**, exit 0. Log: `/tmp/sbg-runtime-replay-r1-final-pytest.log`.
