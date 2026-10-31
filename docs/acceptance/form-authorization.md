# FORM authorization lifecycle (2026-10-31, in progress)

G4 previously returned503 for every F request. Seven new real-PG tests failed
on a387c8e. F now accepts only AppFormPlanRequest IDs, builds steps from persisted
FORM fields and action provenance, and uses the full existing pipeline.

Session/action ownership, expiry, kind, epoch, field stability and used_by are
checked again under session/action locks before atomic binding. Completed request
retries resolve before the already-bound action check; another request cannot reuse
it. No action is inserted into utterances. G8 uses FORM deadlines, requires permanent
plan binding, and consumes generic claims without rewriting nonexistent utterances.

Independent review found two consistency defects in the first candidate. New
cross-session contamination and lost-binding regressions each failed, then passed.
A changed-snapshot G4 test failed with YELLOW instead of RED, then passed after FORM
proof was rebuilt from the pipeline's actual snapshot. A further G8 test showed that
shared replay drops its caller's cache: old proof stayed untainted while the replay
snapshot changed. That regression failed and now passes; FORM proof is rebuilt only
after replay creates its fresh context. Final G4 submission compares full authorization
views, including scope/echo/time/epoch; new pollution cannot preserve an old approval.

Current evidence:16 focused real-PG G4/loader/race/snapshot tests pass. Existing full
G4/approval/execution combination270 passed before final shared-replay increment.
Real G19/G4/G8 green freeze, yellow20000-cent transfer and RED loss with real OTP pass.
UNKNOWN lost-reply and STATE_CHANGED/PAUSED recovery exercised; final combined rerun,
independent review conclusion and clean-deployment live still pending. This component
is not merged and demo-v1 is not frozen.
