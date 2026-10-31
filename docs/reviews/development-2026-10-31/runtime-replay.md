# Production LLM record/replay independent security review

**Result: REQUEST_CHANGES.** Commit `be58c3ad74eede6f04748eae4334f34e047ef2a7`, base `ccf5d3330b44f4d5049fbbdb5bf5d78ac352ba1b`, reviewed 2026-10-31. Three reproducible gaps remain. No direct L2 authorization bypass was demonstrated; finding R1 changes the authorization-source semantics of the recorded model output and therefore blocks using this implementation as trustworthy replay evidence.

Read-only code review plus synthetic in-process HTTP probes. No real API, Docker, database, deployment, secret-file access, implementation edits, push, merge or tag. Read current AGENTS §§1–3/8 and relevant PRD §6.6 under the task-reading exception. Only this independent report is written.

## Findings

### R1 — P1: literal replay placeholders acquire current request authority

Location: `services/agent/app/llm/replay.py:164–177`, with normalization at `131–161` and record call at `326`.

`restore()` substitutes every `<IDn>` in the serialized recorded response using the current request's map, regardless of whether the placeholder was produced by the recorder or was already literal text in the model response. An invalid source in live execution can consequently become a valid current source in replay.

Synthetic reproduction: record a successful response whose tool arguments are `{"source_ref":"<ID1>"}` while the request contains `src_a1b2c3d4-e5f6-7890-abcd-ef1234567890`. Replay the same request with source `src_11111111-2222-3333-4444-555555555555`.

Observed:

```text
literal_placeholder_live_source <ID1>
literal_placeholder_replay_source src_11111111-2222-3333-4444-555555555555
```

The live model did not emit a valid source, but replay emits one. The same ambiguity exists for literal reserved `<CALLn>`, `<NEWCALLn>` and `<UNBOUNDn>` tokens. The existing `<ID999>` test only checks an unbound literal and misses bound literals.

Required correction: encode/escape literal reserved tokens separately from recorder-generated references, or reject them explicitly at recording/replay boundaries. Rebinding must apply only to references known to originate from actual recorded request IDs. Add red/green checks for a **bound** literal placeholder in JSON tool arguments and free text, while retaining genuine fresh-ID rebind and provider-call continuity tests.

### R2 — P2: malformed tool JSON with an unquoted numeric secret is persisted

Location: `services/agent/app/llm/replay.py:441–459` and `478–496`; fallback matching is `_QUOTED_SECRET` at `425–429` and `_SECRET_TEXT` at `418–422`.

If a tool argument string cannot parse as JSON, both secret discovery and scrubbing only recognize quoted values or plain labeled text without a JSON key's closing quote. A numeric secret in an otherwise malformed object is missed.

Synthetic provider tool arguments:

```text
{"pin": 984321, "ok":
```

After `RecordTransport` wrote a private synthetic recording, `"984321" in path.read_text()` was **True**. No real credential was used. Similar malformed numeric OTP values are exposed by the same branch. Valid JSON numeric secrets are covered; malformed quoted values are covered; this combination is not.

Required correction: fail closed or scrub sensitive values conservatively in malformed JSON argument strings, including numeric/unquoted values and incomplete values. Do not return the original malformed secret-bearing string to disk. Preserve legitimate amount values and valid JSON serialization in the stored envelope. Add the captured malformed numeric case and an OTP case as red/green tests. The existing architecture must still keep real PIN/OTP/JWT outside model prompts.

### R3 — P2: a header format downgrade disables entry checksum validation

Location: `services/agent/app/llm/replay.py:248–269`, specifically the conditional `if header.get("format") == 2` at `264`.

The runtime writer always emits format 2, but the loader accepts a missing, unknown or downgraded format and skips all entry checksums. Legacy component support is implicit rather than an explicitly selected format contract.

Synthetic reproduction: create a normal format-2 recording, change `header.format` to `1`, and alter the entry response without updating its digest. Loading and invoking replay succeeded and returned the modified response. Observed `downgraded_format_bypassed_checksum True`.

Required correction: production record/replay must require the current format and reject unsupported formats, or route old formats through an explicitly requested migration/legacy mode outside the production factory. Test missing/unknown/downgraded format, with both loaders and reopening a record file. Existing legacy unit fixtures may be migrated rather than silently bypassing validation. Checksums are corruption checks, not a signature against an attacker with filesystem write access; this finding does not request signed artifacts.

## Verified boundaries and evidence

- Corrected independent test command, from the isolated worktree:

```text
PYTHONDONTWRITEBYTECODE=1 /Users/jet/Downloads/WeBank/.venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_replay.py tests/agent/test_runtime_replay.py
73 passed in 1.12s; exit 0
```

An initial `.venv/bin/pytest` command could not run because that isolated worktree has no venv (exit127). It is not counted as a test failure or a pass; the command above uses the existing root Python3.12 environment.

- Code and existing tests support no-key replay without constructing live sync/async transports; missing/stale/profile mismatch/miss/sequence exhaustion propagate without a provider fallback. Factory configuration comes only from process environment, and B1/B2 plus case path components are validated.
- Actual schemas, prompt source hashes, system strings and model settings affect the schema fingerprint. Current bank IDs and structural provider call IDs share request-local mappings. Response-only runtime bank IDs are rejected as unbound. These good boundaries do not address R1's literal-token ambiguity.
- Per-key duplicate responses are consumed in order under a thread lock. Atomic file replacement and file locking preserve complete prior files. A failed replace can leave a private temporary file; no partial current file is exposed.
- `max_retries=0`; unsuccessful HTTP responses are not recorded; quota status402/403/429 propagate. The production respond/verify boundaries preserve those statuses. All probe transports were `httpx.MockTransport`; no provider request was made.
- No L2 approval, execution or fund-handling code is changed. Every real replay plan still requires current L2 checks, approval and L3 execution. This review does not treat synthetic HTTP tests as banking acceptance.

## Coordinator gates

Return the three findings to the implementer for narrow red/green fixes and an updated commit/report. Recheck these cases before approval. Actual production recording, new-reset/fresh-ID offline L2/L3 proof, network denial, shared AGENTS/PRD updates, final exact-head complete local CI and integration remain coordinator gates. Do not freeze `demo-v1` based on this review or the 73 passing unit tests.

---

## Re-review 1 — commit 58d2a1fe0c572279fc26ed4474953bf724526fc9

**Result: REQUEST_CHANGES (R2 remains partial).** R1 and R3 are closed by this candidate; no new authorization-source laundering was found. No implementation files were edited by this review, and no real API/DB/Docker operation was performed.

### Independent red/green evidence

- New exact commit: the same independent command above yielded **95 passed in 2.11s**, exit0.
- Old exact `be58c3a` replay module was loaded from `git show` into an isolated Python process without changing any worktree file. Current tests selected by `literal_reserved or literal_request or malformed_unquoted or unsupported_recording_format or json_escaped_literal or malformed_json_escaped_secret` yielded **22 failed, 26 deselected**, exit1. Failures are the expected behavior assertions, including the two Unicode cases; no import/setup failure was counted as red. Log: `/private/tmp/sbg-review-runtime-replay-old-red.log`.
- The implementer's initial `/tmp/sbg-replay-review-red.log` also contains the earlier 20 failing cases before the two Unicode additions. These counts describe overlapping sets and are not added together.

### Closed R1: generated references and original literal tokens are separated

Current locations: `replay.py:131–175` and `178–210`. Literal `~` and `<` are reversibly escaped before generated references are inserted. Restoration decodes only literal spans and inserts mapped current IDs opaquely. Embedded valid JSON is decoded before normalization, so Unicode-escaped `<ID1>` cannot turn into a generated reference during subsequent scrubbing. The genuine fresh source still rebinds. Literal request placeholders no longer collide with real source requests; provider call continuity and unbound bank-ID rejection remain green.

The change to the old `<ID999>` test is legitimate: original literal text should remain literal, whereas generated unbound references must fail. It is not a removal of the unbound-authority test.

### Closed R3: current format and entry checksums are mandatory

Current locations: `replay.py:281–302`. Exact integer format2 is required, excluding missing/1/3/string/bool values. Entry digests are checked unconditionally. Both record-file reopening and replay factory validation reject unsupported formats before provider IO. Legacy synthetic test fixtures were explicitly converted to the current format and checksums; production has no implicit legacy bypass.

### R2 remains partial: malformed-field matching and escaped secret values

Current locations: `replay.py:459–471`, `475–480`, `503–523`, `542–562`.

The original malformed numeric `pin`/`otp` examples are fixed, including exponent/incomplete values and Unicode-escaped ASCII field names. Two closely related synthetic cases still persist secrets:

1. Malformed arguments `{"otp_code":"276543","ok":` leave **276543** in the recording. `_is_sensitive_key("otp_code")` treats this key as sensitive for valid structured JSON, but the malformed fallback's regex only accepts a sensitive word at the end of the field name. Both quoted and unquoted fallback matchers therefore miss this common suffix form. Unicode decoding of that key does not resolve the predicate mismatch.
2. Malformed arguments `{"pin":"\u0039\u0038\u0034\u0033\u0032\u0031","ok":` plus provider content `echo 984321` remove the encoded PIN field but leave **984321** in the recording's content. In the malformed branch `_secret_values()` collects the encoded text rather than the decoded known credential, so later known-secret echo removal cannot recognize it. A valid JSON secret with the same encoding is decoded by `json.loads` and has a different result.

Both were run through `RecordTransport` using only `httpx.MockTransport`, a synthetic schema, synthetic provider payloads and temporary files. The probes printed:

```text
otp_code secret_persisted True
unicode_value_echo secret_persisted True
```

Required narrow correction: keep one sensitive-key predicate across valid and malformed inputs, decode recoverable quoted credential values when discovering known secret echoes, and fail closed when a damaged secret-bearing argument cannot be scrubbed with confidence. A conservative rejection/drop of the malformed secret-bearing recording is acceptable and avoids an open-ended list of regex exceptions; do not weaken source rebinding or make malformed output execute successfully. Add these two concrete red/green cases, retaining numeric, Unicode key, literal-token and ordinary amount-value controls. Then rerun the affected pure suite and return for the R2 recheck.

Actual new-reset production/offline proof and final exact-head full CI remain coordinator gates, even once this last review item closes.

---

## Final re-review — commit a2f9d86a3e4131d2603778f6cdfe51f15e73e9e6

**Result: APPROVE for the production record/replay component at this exact commit. R1, R2 and R3 are now closed.** This final result supersedes the earlier REQUEST_CHANGES results above, which remain as audit history. No remaining blocking finding was identified in the reviewed component. Approval does not replace the coordinator's actual recording/offline banking proof or final exact-head full local CI.

### R2 closed by rejecting damaged sensitive responses before filesystem mutation

Final code locations: `replay.py:460–528`, `532–554`, `573–595`; write ordering at `348–390`.

- Both valid structured JSON and recovered malformed field names use `_is_sensitive_key`. The field-token scanner decodes JSON-escaped quoted names and recoverable quoted values. It also recognizes ordinary unquoted/Python-style quoted keys, with `ast.literal_eval` confined to the isolated string token; it does not execute code or repair a bank tool call.
- The recorder rejects an entire malformed sensitive-bearing response with a generic `ReplayStaleError`, using `from None`. Validation of response data occurs at line368 before directory creation, lock/file opening, temporary-file creation or any header/entry write at lines373 onward. Existing recording bytes are preserved. Escaped PIN plus decoded echo cannot leak through another field because the whole candidate response is refused.
- Changing the old malformed-secret tests to require explicit refusal is the stronger approved behavior; no sensitive case was deleted. Non-sensitive malformed arguments retain their invalid-tool-call arguments and branch. Valid JSON secret/echo redaction retains unrelated integer amount values.

### Independent verification

1. Exact final candidate's full runtime + prior replay component suites:

```text
PYTHONDONTWRITEBYTECODE=1 /Users/jet/Downloads/WeBank/.venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_replay.py tests/agent/test_runtime_replay.py
101 passed in 2.18s; exit0
```

2. The immediately previous `58d2a1f` replay module was independently loaded from `git show` into an isolated Python process, with current tests selected by `malformed_secret_recording_is_rejected_atomically or malformed_known_secret_values_use_json_unicode_decoding`: **3 failed, 51 deselected**, exit1. The two concrete leak/refusal cases and known-secret Unicode decoding assertion all failed on old implementation and pass in the final suite. Log: `/private/tmp/sbg-review-runtime-replay-r2-old-red.log`. Two pytest import-rewrite warnings came from the isolated injection harness and do not affect the three behavior assertions. The implementer's earlier log independently shows the same three failures before three additional controls were added; overlapping counts are not summed.

3. Six additional narrowly scoped synthetic `RecordTransport` probes: original `otp_code`, Unicode PIN plus plain echo, Unicode-escaped `pin_code` name, unquoted `otp_code`, single-quoted Unicode `otp_code`, and an incomplete `pin_code` value. During invocation, `Path.mkdir`, `os.open` and `NamedTemporaryFile` were patched to fail if reached. **All six refused with ReplayStaleError before any of those calls**; the target parent directory remained absent. Formatted exception tracebacks omitted raw arguments and both synthetic credential values, and context suppression was true.

4. All R1 literal-token/Unicode/fresh-reference tests, R3 strict-format/checksum tests, sync+async no-key/no-live-network tests, repeated response sequence tests, original unbound reference checks and provider402/403/429 once-only tests remain included in the **101 passing** cases. No R1/R3 production code changed in this correction, and review found no regression in their boundaries.

Only the independent report was changed. Final candidate worktree was clean at review start. No real model/network request, secret-file access, DB/Docker operation, deployment, Git write, push, merge or tag was performed. Unknown unlabeled free-text secrets remain outside generic inference capability; the architecture must continue excluding real credentials from prompts.

### Remaining integration gates

Coordinator still owns AGENTS §3/§4 and PRD layout updates, final integrated schema/prompt freeze, actual production recording, new-reset/fresh-ID replay through real L2/L3 with external network denied, replay demo assertions, final exact-head complete local CI and serial merge. This component approval alone does not authorize an early `demo-v1` tag or claim six-scenario acceptance.
