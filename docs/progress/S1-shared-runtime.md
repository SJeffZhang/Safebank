# S1 shared runtime — Codex implementation record

Scope: authorized S1 brief only; baseline `8555bf0`, branch `feat/l2-shared-runtime`. No push, PR, merge, subagents, full-stack restart, or named volume mutation. AGENTS status/log updates are owned by the coordinator per brief.

## Component 1 — clarification transitions

Added `pending_clarifications` OPEN → RESOLVED/CANCELLED through the shared state entry point. Both terminal states have no outgoing edge; illegal transitions raise ValueError. Conditional UPDATE still provides one winner and a 409 STATE_CHANGED loser.

Actual commands:

- `.venv/bin/pytest tests/gateway/shared_runtime/test_clarifications.py -q`: expected red, 2 failed (missing transition registration), 1 skipped (PG not enabled).
- `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/shared_runtime/test_clarifications.py -q`: 3 passed, including concurrent UPDATE on real PostgreSQL 16. Temporary randomly named container used the existing isolated fixture; no data volumes mounted by the test command.

Files: `services/gateway/app/state.py`; `tests/gateway/shared_runtime/{conftest.py,test_clarifications.py}`. Component commit: `d2726bb` (`feat(gateway): centralize clarification transitions`).

## Component 2 — runtime / revocation

Central `gateway_runtime` allocates one AsyncEngine and one L3Client, installs db/l3/aware wall_now/settings/JWT keys on both apps, and disposes in reverse order once after both servers stop. Secret validation runs before allocating resources. Missing/unsafe/malformed/mismatched Ed25519 keys refuse startup. Partial startup and server failures release owned resources.

Global `caller_dependency` checks F/A customer JWTs with W1's dynamically resolved `tokens.check_revocation(conn, caller, now=...)` before entering handlers. Missing DB, clock, or checker returns 503. W1 GatewayError (revoked/missing/expired) propagates; database failures return sanitized 503. Service-only F/S/D and non-customer console JWT credentials retain their matrix behavior. The checker substitute only proves wiring, not W1's real database semantics.

W1 PIN module is absent at baseline: runtime records `pin_hashes=None` and installs no success placeholder. Once W1 is integrated, startup calls its configured_hashes exactly once and injects the returned hashes. Loader failure refuses startup. Coordinator approved this phased behavior; S1 does not duplicate PIN parsing/hashing. No actual login → session → revoke API acceptance has run.

Actual commands:

- New runtime/revocation tests before implementation: 23 failed / 3 passed (missing runtime and missing revocation enforcement).
- Added settings injection assertion: expected red, 1 failed / 10 passed / 1 skipped; then green.
- Added clean-listener shutdown regression: expected red TimeoutError; then fixed peer shutdown.
- `PATH=... GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/shared_runtime -q --tb=short`: 33 passed, including two real PG tests (clarification contention and shared runtime pool); L3 transport remains controlled MockTransport.
- `make lint`: passed, 150 files formatted.
- `make test-gateway`: 95 passed / 2 skipped (isolated PG tests intentionally not enabled in that target).

Files: `services/gateway/app/{main.py,config.py,runtime.py,auth/deps.py}`, `tests/gateway/shared_runtime/{test_runtime.py,test_revocation.py}`.

W4 locks integration remains pending: its fixed `customer_lock(customer_ids)` signature needs centralized initialization once W4 defines a configuration hook, e.g. `locks.configure(db,max_executions=settings.max_executions)`. S1 has no fabricated lock implementation or speculative hook call.

## Component 3 — Linux secret permissions

Implemented gateway-only `scripts.gateway_entrypoint`: root bootstrap reads regular foreign-owned 0600 source files without following symlinks, writes bounded copies to verified private tmpfs, assigns UID/GID 10001, clears supplementary groups, permanently drops UID/GID, then execs gateway. No private material is emitted. Sources remain mounted read-only in Compose. Host files are not chmodded. Final application has no effective/permitted capabilities and keeps no-new-privileges.

Only gateway starts bootstrap as root. Its four temporary capabilities are CHOWN (assign private copies), DAC_OVERRIDE (read foreign-owner 0600 source), SETUID/SETGID (drop privileges). cap_drop ALL, read-only root filesystem, no-new-privileges, and both network bindings remain. Other services are unchanged. The Dockerfile remains unchanged: its existing non-root default remains for other services.

Actual acceptance:

- Expected red: opt-in permissions test initially failed because acceptance module/bootstrap did not exist.
- Independent Linux container reproduced original uid10001 PermissionError reading uid20002/0600 source.
- First negative-test fixture run failed at preparing unsafe_mode: root without FOWNER cannot chmod a foreign-owned file. A narrow independent container confirmed PermissionError. Fixture now changes ownership back to root only while preparing that intentionally unsafe file, then restores uid20002. No production capability was added for this fixture issue.
- `GATEWAY_SECRET_TESTS=1 ...pytest .../test_permissions.py -q -s`: passed, seven container scenarios: valid copy/drop, missing key, missing PINs, unsafe source mode, symlink source, non-root bootstrap, missing tmpfs. Valid scenario asserts UID/GID 10001, empty supplementary groups, 0700 directory/0600 files owned by 10001, original source mode/owner unchanged, copied contents correct, original source still unreadable, CapEff/CapPrm zero, NoNewPrivs=1, and no secret values in logs.
- Containers use --network none, read-only filesystem, synthetic sources in tmpfs with simulated foreign UID 20002; this tests real Linux permissions, not a full deployment against actual host secrets. Builds use unique `safebank-s1-permissions:<random>` tags and remove only their own tag; existing safebank-p0:local is not replaced.
- `docker compose config --quiet` initially refused because this worktree has no .secrets/gateway.env (expected local deployment input missing). `docker compose config --no-env-resolution --quiet` subsequently passed without copying/creating secrets or starting the stack.

Files: `scripts/{gateway_entrypoint.py,gateway_permissions_acceptance.py}`, `tests/gateway/shared_runtime/test_permissions.py`, gateway-only section of `docker-compose.yml`, Makefile appended target, gateway README.

## Final exit verification (2026-10-23)

Actual sequential command, Docker CLI PATH set to the installed application CLI:

`make lint && make test-gateway && make test-gateway-db && make test && make test-shared-runtime`

- make lint: passed; 153 files already formatted.
- make test-gateway: 95 passed / 3 skipped (2 PG, 1 Linux explicit opt-ins).
- make test-gateway-db: 17 passed on real isolated PostgreSQL, PGDATA explicitly tmpfs.
- make test: 214 passed / 51 skipped (integration/PG/Linux opt-in tests skipped in this target).
- make test-shared-runtime: 34 passed, zero skipped; real PG and seven independent Linux permission scenarios enabled.

No make up/down/test-p1, no push/PR/merge, no named volume operations, and no full-stack restart ran. S1 is implemented and component-verified; full W1 business integration is not complete or claimed. Coordinator must merge W1, supply actual deployment secrets, rerun real login → session → revoke API checks and deployment acceptance, and define/configure W4 lock lifecycle. The private-key and PIN routes have no permissive production fallback.

## Local commits

- `d2726bb`: clarification transitions.
- `8e1874a`: shared runtime and global fail-closed revocation wiring.
- `5b67a76`: explicit PGDATA tmpfs fixture correction (17 real PG tests passed).
- `03f66e8`: final Linux/bootstrap implementation and complete exit evidence. Subsequent documentation-only commit records this stable implementation SHA.

## Isolated PG fixture correction

The inherited fixture supplied no explicit -v, but the official PostgreSQL image declares a VOLUME, so earlier runs may have allocated anonymous volumes automatically removed by Docker --rm. This is not evidence of deleting or recreating the existing safebank persistent named volumes, which remain untouched. No existing anonymous volume was manually cleaned. The fixture now explicitly mounts PGDATA as tmpfs so subsequent runs do not allocate a Docker data volume. Coordinator approved the narrowly scoped existing-test adaptation and will distribute it to other workers.

## Review repair round 1 — R1

Read full `docs/reviews/L2-S1-runtime.md` and installed Uvicorn 0.54.0 sources before changing code. Confirmed `_serve` has no finally and its timeout path cancels requests without awaiting their cancellation.

New regression uses real installed Uvicorn serve/startup/main_loop, ephemeral localhost sockets, FastAPI lifespan, and an in-flight httpx request. Before fix: 3 failed / 1 passed; runtime failure, startup boundary failure and outer cancellation disposed resources with live listeners/lifespans/requests. Removed the two fake-Server tests whose automatic cleanup hid this issue.

`GatewayServer` now owns idempotent shutdown on every `_serve` exit. It stops accepting connections, asks existing connections to close, drains or times out/cancels and awaits requests, awaits closed listeners, then completes ASGI lifespan. The supervisor sets should_exit and joins both tasks through cancellation protection before runtime disposal. Uvicorn startup SystemExit is converted to a task exception so sibling cleanup still runs. Grace period is explicitly 15 seconds.

Actual targeted command: `.venv/bin/pytest tests/gateway/shared_runtime/test_servers.py tests/gateway/shared_runtime/test_runtime.py -q --tb=short`: 16 passed / 1 skipped (real PG opt-in). Added normal-shutdown cancellation case and reran real listener suite: 7 passed. Scenarios cover clean exit, runtime failure, partial startup boundary failure after real listeners start, outer cancellation, repeated cancellation, graceful timeout, and cancellation during shutdown. No fake automatic finally is used to establish cleanup.

### R2 — healthcheck privilege boundary

The exact original Compose healthcheck CMD, executed in an isolated Linux container, failed the privilege observer with uid=0, gid=0, groups=[0], CapEff/CapPrm=195 and NoNewPrivs=1. This confirms PID1's setuid does not alter the user of subsequent healthcheck processes.

Compose now invokes `python -m scripts.gateway_healthcheck`. That fresh process clears supplementary groups and permanently drops GID/UID to 10001 before issuing HTTP; it rejects any other runtime identity. An independent container runs the exact CMD read from Compose. A test-only Python audit hook checks the actual UID/GID/groups/CapEff/CapPrm/NoNewPrivs at urllib's HTTP boundary, followed by a real localhost /health response. No observer or test bypass is added to production.

Actual `GATEWAY_SECRET_TESTS=1 ...pytest tests/gateway/shared_runtime/test_permissions.py -q -s`: 2 passed (all seven original secret scenarios plus actual Compose healthcheck privileges and HTTP). Independent random image tags only, network none, no data volumes or shared-stack operations.

### R3 — pool capacity and confirmed W4 initialization

Actual production-factory tests first failed: pool_size was 5 (not required 18/42), missing lock initialization, and lock-hook failure did not fail startup (5 failed / 10 passed / 1 skipped). The centralized production factory now sets pool_size=max(18,2*max_executions+2), max_overflow=0: one dedicated lock slot plus one short-transaction slot per executor and two additional slots, with PRD's minimum eighteen. Existing explicitly injected single-argument test factories remain compatible.

When W4 `locks.configure(db,max_executions=...)` exists, runtime calls it with the very same engine and configured execution limit before L3 construction. Hook failure disposes the pool and refuses startup. If absent, the existing NotImplemented lock remains unchanged; no lock implementation or success substitute was introduced. Actual SQLAlchemy pool objects (not mock parameter assertions) verify capacities 18,18,42 for limits 1,8,20. Runtime suite after fix: 15 passed / 1 skipped (real PG opt-in).

### R4 — actual single-column primary keys and claim consumption

New tests reproduced all four failures: missing authorization_claims registration, absent explicit rejection for zero/composite primary keys, and execution_jobs raising AttributeError on hardcoded .id. The unchanged `transition(conn,table,*,id_,from_statuses,to,code)` signature now obtains the sole declared primary-key column and uses it for conditional UPDATE and RETURNING; zero/composite keys raise ValueError before DML. Authorization claims use a private state.py StrEnum: CLAIMED → CONSUMED, terminal has no outgoing edges. No schemas/tables/migrations were changed.

Actual real-PG state regression command covering new key tests, clarifications and existing state tests: 11 passed. Query assertions independently check execution_jobs.plan_id → UNKNOWN and authorization_claims.scope_id → CONSUMED; stale competing-source update returns 409 STATE_CHANGED and reclaim attempts raise ValueError.

Additional R3 real-PG verification holds eighteen simultaneously checked-out connections from the actual production factory and checks eighteen distinct pg_backend_pid values. Combined runtime/key suite: 21 passed, zero skipped with GATEWAY_DB_TESTS=1.

### CI verification wiring (coordinator-authorized addition)

`.github/workflows/checks.yml` p1-postgres now runs `make test-gateway-db` and `make test-shared-runtime` immediately after `make up`, which supplies the local runtime image needed by independent Linux acceptance. The job sets `UV_CACHE_DIR: ${{ runner.temp }}/sbg-uv-cache`, avoiding the Makefile's macOS default /private path on Ubuntu. YAML parsing and step-order/env checks passed. A real local Make→uv probe confirmed an externally supplied writable temporary UV_CACHE_DIR is preserved and exported by Make's `?=` configuration. No remote CI execution is claimed: no push/PR was performed.

### Repair-round complete exit verification

Actual `make lint && make test-gateway && make test-gateway-db && make test && make test-shared-runtime`, with installed Docker CLI PATH:

- lint: passed, 156 files formatted.
- gateway: 108 passed / 6 skipped (4 real PG + 2 Linux explicit opt-ins).
- gateway-db: 17 passed, real isolated PGDATA tmpfs.
- full suite: 227 passed / 54 skipped (opt-in/integration tests are not implied passing here).
- shared-runtime: 50 passed / zero skipped; real PG, eighteen concurrent backend connections, seven real-Uvicorn socket lifecycle cases, seven secret permission scenarios, and actual Compose healthcheck privilege/HTTP test enabled. The intentional graceful-timeout case emits Uvicorn's expected CancelledError log for its cancelled synthetic request; it is not a test failure.
- `docker compose config --no-env-resolution --quiet` and git diff --check passed.

No shared-stack commands, Docker data-volume operations, push/PR/merge, schemas/tables/migrations/business changes, or additional agents. W1 live login/revoke and full deployed-stack acceptance remain for integration, and real W4 configure behavior remains for W4 integration; S1 verifies its confirmed interface with a signature-compatible hook only.

Repair commits so far: `02062b7` R1; `2822027` R2; `ade0cee` R3/hook; `23c4070` R4 and real eighteen-connection proof. CI wiring/final record committed separately; branch head is supplied in final handoff.

Cache-probe execution detail: the first local probe used GNU Make --eval, unsupported by the installed macOS Make, and exited 2; this was a probe-command portability error. Replaced that invocation with a temporary makefile supplied via `make -s -f - s1-cache-probe` including the real Makefile. The corrected actual Make→uv cache-dir probe exited 0 and matched the writable temporary UV_CACHE_DIR. The stated successful cache verification refers to this corrected command. `80fc3bf` records the CI wiring; this final documentation update records the probe failure and correction explicitly.

## Review repair round 2 — N1 only

Read the appended N1 review and confirmed that the installed Uvicorn _wait_tasks_to_complete skips waiting when force_exit=True. The existing code then joined uncancelled requests indefinitely.

Added one regression to test_servers.py using the real GatewayServer and installed Uvicorn handle_exit(SIGINT) twice, a real FastAPI/ASGI lifespan, and a pending request task. It invokes shutdown directly so the synthetic signals are never replayed into pytest. The probe first failed: with 0.01-second grace and a 0.1-second observation window, shutdown did not finish without test cleanup. The failing probe cleans up only its own task after capturing that failure.

Minimal production fix: force_exit explicitly cancels remaining request tasks before the existing awaited gather. The request → lifespan → resource order remains unchanged and is asserted by the regression. No other production behavior or frozen file was changed.

Actual commands in this round:

- `.venv/bin/pytest tests/gateway/shared_runtime/test_servers.py::test_second_sigint_cancels_remaining_requests_before_lifespan_and_resources -q --tb=short`: expected red, 1 failed (finished_without_help=False).
- `.venv/bin/pytest tests/gateway/shared_runtime/test_servers.py -q --tb=short`: 8 passed, including seven existing real localhost lifecycle cases and the new no-socket real-Uvicorn second-SIGINT regression.
- `make lint && make test-gateway`: exit 0; lint passed (156 formatted files), gateway 109 passed / 6 skipped (unchanged explicit PG/Linux opt-ins).

This round did not rerun unrelated PG/Linux/full-stack checks; the coordinator independently ran all five previous exit commands and make up/test-integration on the prior code. No deployment, stack, volume, push/PR/merge operations were performed by this repair. Existing untracked .secrets in the worktree belongs to coordinator deployment setup and was neither read, modified nor staged. Awaiting original reviewer's N1-only recheck after local commit.

N1 implementation/test/evidence commit: `e99406d055823268b63bc3d6b7d66d946f8bd1b6`. The subsequent documentation-only commit records this stable implementation SHA; branch remains unchanged after handoff for review.

## PR #4 CI workflow-context repair

Baseline retained: `6c59ac16c0b8a937e7115fbd887478f1ee37a05e`, including coordinator documentation commits. Independently read run 39964404373 metadata with gh API: completed/failure at that SHA; jobs total_count=0. No job logs or passing CI are claimed.

Verified root cause: the added job-level `env.UV_CACHE_DIR: ${{ runner.temp }}/sbg-uv-cache` references an unavailable context. GitHub's official [context availability table](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability) permits github/needs/strategy/matrix/vars/secrets/inputs for jobs.<job_id>.env, but not runner. Earlier YAML parsing and Make→uv tests did not validate Actions expression context availability; their passing results did not prove the workflow was accepted.

Trusted validator: downloaded actionlint v1.7.12 darwin_arm64 from the maintainer's [official release](https://github.com/rhysd/actionlint/releases/tag/v1.7.12) into `/private/tmp/sbg-actionlint-iysg7h9r/`, without installing a project dependency. Verified the archive against the release's checksums file: SHA256 `aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f`.

Actual targeted validation:

- Before change, actionlint .github/workflows/checks.yml exited 1: line 18:25, context runner is not allowed here; validator listed the same allowed contexts as GitHub docs.
- Minimal fix: replace only that value with the literal `/tmp/sbg-uv-cache` in the ubuntu-latest p1-postgres job. No step ordering, PG/Linux gates, workflow triggers, or existing coordinator documentation were reverted.
- After change, the same actionlint command exited 0 with no diagnostics.
- Read the corrected value from YAML, verified it is writable locally, and ran a temporary stdin Makefile including the actual Makefile: Make preserved/exported the exact literal UV_CACHE_DIR to uv cache dir (exit 0).
- git diff --check passed. Product tests, Docker stacks/volumes and deployments were not rerun for this one-line workflow-only fix.

Scope is exactly checks.yml cache value and this report. No push, CI dispatch, PR change or merge occurred. Awaiting original reviewer's targeted workflow recheck and coordinator push to obtain fresh real CI results; prior local green tests are not a claim that CI passed. Local repair SHA recorded below after commit.
