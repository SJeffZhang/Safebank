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

Candidate3f16ba6 live correctly proposed only gym cancellation and19900-cent savings,
but omitted period/first_run_at source declarations. L2 UNKNOWN_SENSITIVE_ARG correctly
returned REPLAN without writes (pln_01M66ZE4QC5JP2BSGJQD9V9JY8). Two new missing-calendar
source tests fail on that candidate; local combo retry now requires individual utterance
sources, never inventing provenance. Agent suite600 passed /39 conditional skips.

Candidate d463d4f, generation demo_20261030180030_10e93fb3: real L1 case36 passed
through bound approval, plan pln_01M66ZJ5HSWS1R45P36NAVHPHF EXECUTED. Only gym
cancelled; cloud/music/video remain ACTIVE. New savings instruction19900 cents,
MONTHLY, acc_zs_chk -> acc_zs_sav. D3 advanced to Dec6: Nov1/Dec1 at08:00 Beijing
each executed once, corresponding real ledger transfers total39800 cents; gym billing
BLOCKED twice. Next execution Jan1; zero duplicate events; posted/pending ledger balanced.
Machine evidence: idle-subscription-savings-live.json. Follow-up independent review
APPROVE, reviewer reran8 tests. Final exact-head full local CI remains pending.
