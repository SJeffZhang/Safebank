# D3 clock and event identity acceptance (2026-10-31)

Input: authenticated demo-console request {request_id,target_at with offset}.
Output: advance_job_id; query returns PENDING/RUNNING/RETRYING/DONE/FAILED, sim_now and counters.
S1 remains the execution authority; no gateway/direct-L3 bypass or weaker approval rule.

## Red/green

- Old L3:4 new clock tests failed. Old gateway:3 failed/1 passed. New initial gateway+L3:23 passed.
- Independent findings: old timezone/dispatch2 tests failed; payload/instant identity2 failed.
- Fixed full obligations+gateway clock225 passed (one existing SQLAlchemy metadata-cycle warning).
- Upgrade from0009 preserves existing records; duplicated old history rejects migration and retains both rows/version.
- Lifecycle15 passed including previous image identity absence before build, and old demo-up after rollback.
- Frontend3 new tests fail on old implementation, pass on new; typecheck passes.
- Browser plugin unavailable; used bundled Playwright on isolated local component harness with mocked D3.
  Mobile375x812 + desktop1280x900: correct page/title, meaningful dialog, no framework overlay,
  zero console/page errors; original job queried, RETRYING resumed with original request identity
  (one POST),64-character job ID wraps. Screenshots in /private/tmp/sbg-clock-{mobile,desktop}-*.png.
  Initial harness used an unsupported inline HTML proxy under node_modules (500); changed ignored QA
  harness to external JS, no application workaround. Modal now uses its existing padded body class.

## Gates still pending

Final-head full local CI, real console-to-L3 clock advance, scheduled-transfer/merchant/fund state
assertions, and cross-schema image rollback smoke must pass before merging. No demo-v1 tag yet.

First exact head e3ead3d full local CI passed: unit3523/1012 skips, frontend400/BFF/build,
all configured PG, ledger concurrency/consistency and network/restart checks. Subsequent real
cross-schema fault injection caught a rollback failure: the replaced untagged image ID no longer
existed. All database generations/volumes remained. Coordinator rebuilt accepted source2390001,
restored original generation/runtime and retained explicit image tags. No L3 data/history deleted.
Fix now explicitly tags each previous image before build; independent review APPROVE/lifecycle16.
Because code changed, prior CI is insufficient for merging: new final-head CI/live required.


## S1 contract closure

- Real bank HTTP/PG + gateway HTTP/PG covers scheduled execution, merchant charge,
  cancelled mandate refusal, and committed source-plan grant recovery (four cases).
  Old production d4a6ecd fails the two merchant cases; new candidate passes.
- Seed consents install through host maintenance CLIs into empty generation databases.
  Original terms and first due date remain independently bound; runtime recovery requires
  real COMMITTED/EXECUTED source, matching created instruction and recorded approval risk.
- Independent seed/source review APPROVE: 14 real-PG cases and 12 pure term cases.
- Missing grant refusal regression fails before removing premature claim release: no terminal
  event_decisions row. Corrected S1 suite: 29 passed; refusal audit persists exactly once on replay.
- Combined bankcore, obligations, lifecycle and S1 suite before final audit fix: 718 passed.
  Final live deployment and final-head full CI remain required.


Live candidate ab0d61b: fresh generation installed7 distinct grants; actual L1 cancelled music
and subscribed2000 yuan for Li Si. Advances to Oct15/Oct23/Dec6 returned DONE; order settled,
cancelled music charge blocked and ledger balanced. SQL nonetheless exposed stale durable
source cursors: later monthly charges were never scanned. Three real-PG cursor assertions fail
on this candidate. Pure applier returns correct obligation state but EventService persisted only
ledger effects. Acceptance remains blocked pending atomic source-cursor persistence and rerun.

Cursor correction: three regressions fail on ab0d61b,36 event/scheduler tests pass after correction.
Extended real contracts4 passed, covering two periods and full rollback before commit. Independent
review APPROVE,14 real-PG tests personally run. New live generation and full CI remain pending.
