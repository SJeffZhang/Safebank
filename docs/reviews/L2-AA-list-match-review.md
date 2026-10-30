# Review: AA participant list matching (gateway part of a53842a)

- Reviewer: read-only Claude subagent (DS workers paused by the user on 2026-10-30), detached worktree at a8934d8.
- Scope: `services/gateway/app/taint/text.py` list branch of `matches()`; call site in `pipeline/provenance.py`; independent check in `pipeline/intent.py` `_fields_match`; parser AA ids in `utterance/parser.py`.

## Conclusions
1. Extra, missing, or duplicate ids, another customer's payee, and a name shared by two payees are all rejected. A raw id typed into the span matches through the existing scalar rule; this is not new behaviour, and ownership and intent still run afterwards.
2. `normalize()` applies NFKC and strips whitespace and Cf characters before the split; empty parts are filtered out. A payee name containing 和/与/及/跟 cannot match from a list span. That is a false negative, so it fails closed.
3. Empty-list guard present; tuples work. Non-string elements were reachable only outside the contract (`tuple[Id, ...]`, 1–20 items).
4. The recursive call only reaches the str branch for contract-valid input.
5. Tests catch removal of the dedupe, customer, and empty guards. The length check and `return not remaining` overlap, so removing either alone fails no test.
6. Intent stage independently requires exact list equality with the ids the G3 parser resolved from the utterance, so provenance is not the only guard.

## Findings
- P0/P1: none.
- P2-1: a nested or unhashable element raised TypeError instead of returning False. **Fixed in this branch:** non-str elements now return False; the test fails on the old code and passes with the fix.
- P2-2: names containing separator words cannot be authorized from a list span (usability).
- P2-3: order-sensitive intent comparison vs order-insensitive provenance (fails closed).
- P2-4: missing tests for the shared-name, separator-in-name, mixed id/name, and provenance-level integration cases.

## Verdict
APPROVE
