# November 1 independent parser / guidance security review

**APPROVE** for scoped product changes at exact head `4f2240362a699eec5f9e61a8aeaad8ab99afa057`. Documentation parent is32ce709; product baseline is `aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1`. No new actionable finding in the reviewed parser/guidance unit.

## Exact archive and verification

The exact target was exported with git archive to `/private/tmp/sbg-nov1-repairs/review-parser`. Review used only that archive, not root WIP. Product scope: `services/gateway/app/utterance/parser.py`, the prompt-template selection line in `service.py`, and the two NOV1 query/cancel test files. No product or shared-document edits were made.

Using the existing local Python environment with `PYTHONDONTWRITEBYTECODE=1`:

- `python -m pytest tests/gateway/w1 --ignore=tests/gateway/w1/db -q -p no:cacheprovider`: **1705 passed in2.67s**, log `/private/tmp/sbg-nov1-repairs/review-parser-w1.log`.
- `python -m pytest -c pyproject.toml /private/tmp/sbg-nov1-repairs/test_review_parser.py -q -p no:cacheprovider`: **46 passed in0.54s**, log `/private/tmp/sbg-nov1-repairs/review-parser-independent.log`.

No DB/API/provider/live model/browser/Docker operation was performed. Root's reported four real-PG G3/JWT/current-clarification/action cases were read as root-owned evidence, not rerun or claimed as this reviewer's independent result. The G19 test status200 matches current production endpoint; this review does not resurrect the earlier201 harness error as a product defect.

## Read-only clauses and mixed writes

`parser.py:280–324` matches entire known read clauses, including the15 formal read-only questions. It creates only `Command("read")`, without inferring amounts or targets from months, product names or numeric filters. `parse():688–703` parses each punctuation-separated clause and rejects the entire unknown mixture.

Independent tests ran every formal query with a following explicit “给王五转100元”: all15 produced only TRANSFER_EXTERNAL with the real owned payee fingerprint and10000 cents. The same15 followed by an unknown write clause all stayed INTENT_UNCLEAR with empty write scope. Existing tests also cover unpunctuated mixed suffixes and consultation/question-mark restrictions. Thus the new read alternatives do not consume an arbitrary trailing operation or silently discard a recognized comma-separated write. This is conservative fixed grammar, not a claim to classify every possible Chinese query.

## Payee precedence / customer ownership

`parse():704–705` builds owned account/payee sets before matching. `_payees():561–580` preserves exact name/registered alias/phone priority, then existing near-name matching, then restricted Hanzi prefixes as candidates. Transfer authorization still requires exactly one `_exact_payee`; a unique prefix is not auto-selected.

Independent controls passed for foreign exact names and prefixes being excluded, even when a foreign exact name would otherwise outrank owned prefixes; a single owned prefix remaining CLARIFYING; registered exact alias winning; duplicate exact owned names requiring a choice; exact owned phone winning despite a foreign customer using the same phone; and unknown phones producing no transfer or guessed candidates. No foreign fingerprint appeared in the resulting scope/candidates.

## Cancellation target identity

`parser.py:991–1028` first filters instructions by a source account owned by the current customer. A typed exact instruction ID may identify only that owned-source instruction. Human-name matching applies to ACTIVE instructions through the current customer's actual payee mapping or own destination account; target_display is not an authorization identity.

Independent controls passed for foreign-source explicit IDs being rejected, misleading free display text not selecting the wrong payee, own SELF destination matching through an owned account, and multiple matching owned instructions yielding SLOTS with only real instruction candidates. No instruction_id is preselected in the ambiguous partial scope. Exact-ID cancellation of an already inactive owned instruction remains an idempotent/protective backend concern, not authorization of another customer's schedule.

## Clarification merge remains constrained

The service change only uses the parser-selected fixed template when persisting FORM_REQUIRED. Existing `clarification_fields` and `merge_scope` validation is unchanged: candidate membership and exact required field-set equality are checked before merging; no extra amount/source can be appended.

Independent pure merge tests showed:

- “给王转200块” + selected owned payee restores exactly TRANSFER_EXTERNAL,20000 cents, original acc_zs source and selected actual fingerprint.
- Foreign candidate IDs, an added amount field, and an added foreign source all raise GatewayError.
- Multiple schedule candidates + selected instruction restores only SCHEDULE_CANCEL with that instruction_id, no transfer scope; foreign instruction IDs or appended money are rejected.

The archive's real-PG test code additionally binds G19 to the actual parent and uses G3 action_id-only registration, but its execution remains root-owned. This review does not equate pure merge tests with parent/JWT/epoch/database acceptance.

## Fixed guidance grants no scope

The three service-selected templates correspond only to parser outputs with empty write authority:

- Direct risk-level assignment is intercepted before numeric shorthand, and produces risk_assessment_form without turning R5 into money or FUND/RISK action scope.
- Unknown long target numbers produce register_payee_form, without PAYEE_ADD or TRANSFER scope.
- Supported consultation phrasing produces transfer_consultation, without treating the question as an imperative.

Independent controls assert not just an empty action list but empty transfers, fields, source account, required_fields and candidates for the three exact formal phrases. The parser chooses a fixed internal ID; no user text is promoted into template IDs, form answers or authorization. Mixed unknown instructions remain safely unscoped. Frontend navigation is a separate reviewed unit and does not prefill banking operations.

## Limits and handoff

No live formal case was rerun; no full-head CI or bank execution evidence is claimed. Service-side persistence, ownership, epoch, source consumption and PG concurrency still require root's exact integrated-head gates. The original reports/CSV and their failures remain unchanged. Within this parser/guidance diff and the pure boundaries reviewed, there is no remaining request for changes.
