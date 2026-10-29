# L2 G3 Parser Coverage-A Review

Scope: `git diff origin/main..HEAD` (commits 9543f90, 77870b4, 66d950c, 013ad2b).
Diff touches 3 files: `services/gateway/app/utterance/parser.py` (+11/−2),
`tests/gateway/w1/test_parser_coverage_a.py` (new, 144 lines), and a progress doc.
No existing test or non-parser source file is modified.

## Verdict

**APPROVE** — the four new SCOPED forms are parsed conservatively and each
safety property in the review intent holds. No P0/P1/P2 findings.

## Findings

### P0

None.

### P1

None.

### P2

None blocking. Two informational observations (not defects):

1. `_payee_attr` account capture (`parser.py:420`, pre-existing) uses a raw
   `\d{8,}` with no space/hyphen tolerance, so an account typed as
   `账号6222 0200 0000 7777` falls through to INTENT_UNCLEAR rather than being
   captured verbatim. This is fail-closed and not introduced by this diff, but
   note it diverges from `numbers()` (`parser.py:35-36`), which strips `[\s-]`.
2. "我的卡丢了，帮我挂失" yields two `report_card_lost` commands (one from the
   new `lost` pattern, one from the 挂失 verb loop). Harmless: `put()` dedups
   identical `card_id`/`replace` values and `action_kinds` is de-duplicated via
   `dict.fromkeys`.

## Checks

1. **Account number captured exactly from text, never invented** — PASS.
   `_payee_attr` returns `fields={"account_no": m["acct"]}` where `acct` is the
   literal `\d{8,}` substring (`parser.py:420-422`). `add_payee` handling only
   copies `command.fields.get("account_no")` into the scope (`parser.py:800-803`);
   there is no path that synthesizes or looks up an account number from state.
   Test asserts `account_no == "6222020000007777"` and `"6222020000008888"`
   exactly as written (`test_parser_coverage_a.py:80,88`).

2. **Ambiguous/unknown entities still fail closed** — PASS. Unknown
   merchant/payee resolve through `cancel_subscription`/`cancel_scheduled` to an
   empty `ids`/`selected` list and `return Parsed()` (INTENT_UNCLEAR) at
   `parser.py:744`/`parser.py:791`. Tests cover "取消不存在的商户的订阅" and
   "取消给王二的定时转账" (both `!= SCOPED`, `test_parser_coverage_a.py:136-137`).
   The `bank` attribute only accepts the literal values 他行/本行; any other
   bank name (e.g. "开户行工商银行") fails `fullmatch` and stays INTENT_UNCLEAR.

3. **Negation / quote / question still produce no scope** — PASS. The new
   `lost` pattern requires the clause to be exactly `我的卡/我的银行卡` + one of
   `丢了/丢失了/丢失/丢` (`parser.py:320`); "我的卡没丢" does not match because
   `fullmatch` leaves `没` unconsumed. Question markers (吗/？) are not stripped,
   so `取消…订阅吗？` and `取消给…定时转账吗？` fail `fullmatch` and fall through
   to `_transfer_clause` → None. Quotes are stripped by `QUOTES` (`parser.py:549`),
   leaving "他说" which parses to nothing. Tests
   (`test_parser_coverage_a.py:127-144`) cover all three.

4. **No existing safety test loosened** — PASS. `git diff --stat` shows the only
   test change is the *addition* of `test_parser_coverage_a.py`; no existing test
   file is modified.

5. **Unrelated sentences not newly over-matched** — PASS. "我的卡" questions
   without 丢/挂失 ("我的卡能用吗", "我的卡丢了怎么办") do not match the `lost`
   pattern (trailing text fails `fullmatch`) and do not match the
   `report_card_lost` verb loop (lexicon verb is only `挂失`,
   `intent_lexicon.yaml:7`). The `bank` attribute only merges when a single
   `add_payee` main clause is present; a bare "开户行他行" without add_payee is
   rejected at `parser.py:577-578`.

6. **Test run** — PASS. `.venv/bin/pytest tests/gateway/w1 -q` →
   `1613 passed, 159 skipped in 3.65s`. New file in isolation:
   `11 passed` (5 positive + 6 parametrized attack cases).
