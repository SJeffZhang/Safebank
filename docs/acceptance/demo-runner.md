# Portable demo orchestration acceptance (2026-10-31)

This component supplies the portable `make demo` entrypoint and observation / SQL-receipt
workflow. It does not by itself complete the demo-v1 freeze. The implementer ran no real
Docker, model API, database, deployment or credential-reading operation.

## Operator contract

Select the existing isolated checkout with `--deploy /path/to/deploy` or
`SAFEBANK_DEMO_DEPLOY=/path/to/deploy`. An explicit argument wins, then the environment,
then the current directory if it contains `e2e.override.yml`. Otherwise selection fails
with instructions. It never guesses a legacy machine path, creates a second stack or
resets data. `make demo-up ARGS='--deploy /path/to/deploy'` and `make demo-reset` accept the
same path contract. Reset/up internals retain their existing generation semantics.

`make demo` defaults to replay. Explicit `LLM_MODE=live` / `LLM_MODE=record` (or `--mode`)
select the runtime mode. The agent is recreated for each case with B2, that case ID and
`LLM_REPLAY_ROOT=/run/safebank-replay`. The selected recording directory is bound at that
path writable only for record, read-only for replay. Live needs no recording directory
and mounts none. Files follow `<recordings>/B2/<case_id>/replay.jsonl`. Runtime overlays
contain no copied credentials and are created exclusively with mode0600 beside the run
manifest. The generation overlay remains a separate Compose input. Replay explicitly
sets the agent's API key empty; a private deployment override must also allow empty-key
Compose interpolation. The runtime factory, not this script, requires a key for live/record.

Examples (the operator prepares the authorized isolated generation separately):

```sh
make demo LLM_MODE=record ARGS='--deploy /path/to/deploy --recordings /path/to/recordings --observe-only'
make demo ARGS='--deploy /path/to/deploy --recordings /path/to/recordings --observe-only'
make demo LLM_MODE=live ARGS='--deploy /path/to/deploy --cases /path/to/subset.tsv --observe-only'
make demo ARGS='--verify-run /path/to/run --proofs /path/to/sql-receipts'
```

With no explicit cases argument, the representative sequence is:

| Case | Purpose |
|---|---|
| 01 | Zhang San transfer100 yuan to Wang Wu |
| 16 | September bill report |
| 27 | Identify idle subscriptions |
| 34 | Li Si redeem1000 MMF units |
| 36 | Cancel remaining idle subscriptions and schedule the derived monthly savings |
| 23 | Li Si report lost card, including actual red-level OTP when requested |

This order avoids a preceding28 cancellation duplicating the36 idle targets and puts the
lost card last. It is a six-scene representative sequence, not every required operation.
The two independent reset rounds, all card controls, real clock events, complete43-case
status table and rollback / no-duplicate SQL assertions remain coordinator gates.
`--case-ids 09,16` chooses canonical IDs in that exact order. Explicit `--cases file.tsv`
runs all rows in file order unless `--case-ids` further selects them. The canonical43-row
TSV remains unchanged. Duplicate/missing/unsafe IDs and malformed rows fail before a
banking request.

## Offline and stop gates

Replay inspects the actual agent container and every attached Docker network. Every
network must have `Internal=true`; missing inventory, host networking, or any external
network is refused before agent reconfiguration. The runner does not change networks or
host firewall rules. The coordinator must arrange a serial network switch preserving
all databases and volumes. After every case-specific agent recreation, the network
inventory is rechecked, the agent must reach gateway health and Postgres-agent TCP,
and direct TCP connections to both1.1.1.1:443 and api.deepseek.com:443 must fail. These
actual probe results are recorded. A missing key or an environment flag alone is not
accepted as offline proof. These paths have only synthetic boundary tests here; real
Docker blocking still requires coordinator execution.

All selected replay files are checked before the first case and rechecked at use. There
is no live fallback. The existing trusted `drive_try_agent.py` drives the real CLI,
approval/risk-ack/OTP path; no authorization is bypassed by this component. Quota/access
exit75 and timeout124 stop the entire batch immediately. Unknown/transport/configuration
results and missing, empty or mismatched current traces also stop the batch. A timeout
at the outer subprocess boundary terminates its process group. No retry/model-switch is
introduced. The runner never echoes raw subprocess stderr or driver output, which could
contain secrets. Structured trace credentials are additionally scrubbed before writing.

## Evidence and verification

Each run has an exclusive directory under `.demo-state/runs/` by default. Existing run
directories and runtime/trace files are never overwritten. The manifest records the
exact full checkout SHA, current running bank generation, seed, mode, profile, selected
cases, sanitized trace SHA256, driver exits and network/health evidence. Runner and
source checkout commits must match, tracked deployment changes are refused, and the
running bank database must match the selected generation. This records checkout identity;
final image-to-code identity remains the coordinator's clean-build/precise-SHA gate.

`--observe-only` can exit0 with `status=OBSERVED, business_verified=false`; this is never
business acceptance. Without that flag, `--proofs` is mandatory before any runtime action.
The coordinator can first observe, independently query SQL, then use `--verify-run` to
attach receipts without making any banking calls. A receipt `<case_id>.json` must contain:

```json
{
  "run_id": "the-current-run-directory-name",
  "checkout_sha": "the-exact-40-character-commit",
  "generation": "demo_20261031000000_1234abcd",
  "case_id": "16",
  "trace_sha256": "the-current-sanitized-trace-sha256",
  "verifier": "SQL",
  "verified": true,
  "checks": {"coordinator_independent_sql_assertions": true}
}
```

Every identity field must match and checks must be nonempty, strictly Boolean true.
Missing/stale/non-SQL/false receipts fail. Only a complete OBSERVED manifest with every selected case exactly once in order is eligible; RUNNING/FAILED/UNKNOWN and partial lists fail. Attachment also rehashes the trace files.
Only then can the separate `verification.json` say `business_verified=true`. Receipt
contents are a trusted coordinator attestation, not a built-in SQL validator or a
cryptographic signature; the coordinator must implement and run each scenario's real
SQL checks, not hand-write success from an exit label. The implementer created no real
receipt. Failed runs remain failed, including partial observations and their evidence.

## Measured local checks

- Portable path tests: original4 failures /1 control pass; corrected5 pass.
- Old hardcoded path fixture changed only to the authorized explicit-env/cwd contract;
  all initial-state-corruption, rollback and generation-preservation fixtures retained.
- Runner Makefile red:1 failed before implementation; initially missing-module fixture
  produced30 setup errors (not counted as30 valid behavioral red tests).
- New behavior red/green: incomplete trace acceptance1 failed/38 passed; live-directory
  and late-missing-recording2 failed/40 passed; explicit custom-TSV selection1 failed/
  42 passed; partial/unknown-run receipt acceptance3 failed/45 passed. All corrected without dropping negative checks.
- Final affected suite (runner, path, existing lifecycle and existing driver guard):
  **76 passed**. No real network/Docker/database flags enabled.
- Final default pytest: **3629 passed /1071 conditional skips** in30.62s, exit0.
- Full ruff check passed; format505 files already formatted; git diff --check passed.
- `make demo PYTHON=<existing-venv-python> ARGS=--help` exercised the real Makefile entrypoint successfully, without lifecycle or network activity.
- Actual live/record/replay, SQL, two reset six-scene rounds,43 table, network isolation,
  exact-head full workflow CI and independent review: **not run by this implementer**.

The coordinator should update AGENTS§3/§4 and PRD§6.6/§11.5 for the portable commands,
recording layout and explicit observed-vs-SQL-verified result contract. Shared AGENTS/PRD
were not edited in this worktree.

## Independent review R1 correction

Review reproduced stale observation reuse across different parent directories with the
same run-directory basename after a driver startup exception returned1. New run IDs are
random128-bit nonces independent of directory names; every case also gets a fresh UUID.
The trusted driver forwards that UUID through the new optional `try_agent --request-id`
parameter, validated as UUID before the PIN prompt. Omitted parameter retains the existing
UUID default. The resulting trace must contain exactly this request_id_prefix before any
artifact is accepted. Operator-container trace paths contain both fresh nonce and UUID.
No old trace is deleted or overwritten; same-name run directories remain independent.

Four old-code failures /53 controls passed; corrected runner+entire try_agent suite:
127 passed. This parameter is correlation only and changes no authorization or terminal
state logic. The existing bill branch's separate terminal-state fix is intentionally not
copied here; coordinator must preserve both during serial integration. R2 remains pending.
