# Idle subscriptions and scheduled savings (2026-10-31)

Baseline 84aeb90, fresh isolated dataset, live case36: the model read gym is_idle=true
and three other subscriptions is_idle=false, but proposed cancelling all four because
the displayed usage counts were zero. L2 correctly returned INTENT_SCOPE_EXCEEDED /
REPLAN without business changes. Plan pln_01M66YVTRE9YJ47FR7CF6JHKYH; logs
full-e2e-idle-84aeb90-36. This baseline is a failed business case, not a gateway bypass.

The planner now rejects and retries such proposals before submitting them. Only explicit
boolean idle flags guide the proposal; missing flags cannot be inferred from usage.
Duplicate cancellations and incomplete idle sets also reject. The gateway still derives
the authoritative predicate and validates amount provenance, approvals and execution.

Three new regressions failed on the old planner (overbroad first proposal, exhausted
retry, missing idle flag). After the correction, targeted tests10 passed and all agent
tests598 passed /39 conditional skips. Existing fixture corrected to actual items /
instruction_id response shape rather than a synthetic subscriptions shape.

Pending independent review, clean-stack live state verification and exact-head local CI.
This does not claim the six-scenario freeze.

Independent read-only review APPROVE: no new authorization path; reviewer personally
reran6 related tests. L1 rejection only, existing L2 predicate/derived source checks retained.
