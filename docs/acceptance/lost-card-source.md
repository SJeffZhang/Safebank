# Lost card source acceptance (2026-10-31)

Historical live case23 (Li Si) returned REPLAN / UNKNOWN_SENSITIVE_ARG. The planner
could omit card_id provenance and gather had no lost-card read guarantee.

Four new tests fail on b644569: missing card source retry, exhausted missing source,
missing get_cards read and ambiguous multiple-card selection. The new L1-only guard
requires a get_cards read source and asks for a card tail when multiple current cards
lack a unique explicit four-digit tail. It never fabricates a source or approves a
plan. Existing L2 source pointer/value/customer/session validation remains authoritative.
A read-only get_cards supplement runs if the model omits that read for lost-card text;
attempted reads are not repeated. Other reads and write permissions stay unchanged.

Two further tests cover cross-tool source laundering rejection and explicit unique-tail
selection. Six targeted tests pass. Clean live, independent review and full final-head
local CI remain pending; no six-scenario freeze claimed.

All agent tests606 passed /39 conditional skips. Independent read-only security review
APPROVE; reviewer personally reran6 focused tests and diff checks. No runtime API used
by reviewer. Final live and full CI remain pending.

Candidate2684788 live read the correct owned card but omitted replace=false provenance;
L2 returned UNKNOWN_SENSITIVE_ARG / REPLAN, no card writes. New real G3/JWT/PostgreSQL
regression reproduced the fixed no-replacement default as unknown (1 failed /4 negative
cases passed). Added a narrow proof only for report_card_lost.replace=false, persisted
typed CARD_LOST scope with a card and exact loss-action span. Scope, lineage, customer,
session and expiry checks still apply; true is never supplied by this rule. New tests
5 pass, including true tampering, unrelated span, expired and other-session references.
Planner missing-replace-source regression also failed before local retry correction.

Combined real-PG W2 + agent881 passed /39 conditional skips. Incremental security review
APPROVE, reviewer personally12 targeted tests. Card target equality remains mandatory in
IntentStage; this default proof supplies neither a card ID nor replacement permission.

Candidatea86e015 live stopped during gather on gateway404 NOT_FOUND, before a plan;
no business writes. The read collector now gives fixed, sanitized missing-entity feedback
within its existing round cap; unsuccessful reads never produce source records. All other
errors, including403/429/503, immediately propagate. Also corrected the supplemental
get_cards call to the frozen required-nullable account_id=null contract (not omitted).
Both new regressions fail on a86e015. New agent611 passed /39 conditional skips;
independent read-only review APPROVE, personally11 focused tests. Actual failing live
read args were not persisted, so this does not claim the precise model filter value.

Candidatea01611d, fresh generation demo_20261031012557_9dcbf820: real L1 case23
completed RED approval, OTP send/read and confirmation. SQL plan EXECUTED; approval
USED/RED/confirmed; bank COMMITTED/EXECUTED; crd_ls_1 LOST, only one owned card and no
replacement. Exactly one CARD_LOST operation, zero transfers, balanced ledger. Successful
OTP challenge was consumed; evidence uses audit event counts and HTTP200 confirmation,
never OTP bytes. Machine evidence lost-card-source-live.json. Final full CI pending.
