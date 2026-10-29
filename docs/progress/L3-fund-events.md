# L3 fund system events — real PostgreSQL tests

Branch `feat/l3-fund-events` (from main aebfbea). Test-only; no production change.

`tests/obligations_data/test_fund_events.py` (3 tests, real tmpfs PG + real L3 HTTP for the subscribe/redeem leg, `EventService.execute_event` for the event leg):

- **FUND_CONFIRM**: subscribe `prd_r3_gain`, execute the generated FUND_CONFIRM job → order confirmed, holding created, pending freeze posted, `event_executions` SUCCESS; replay returns the stored result with no second posting.
- **FUND_EXPIRY**: subscribe, execute FUND_EXPIRY → order failed, freeze released, no holding.
- **FUND_SETTLE**: redeem a non-MMF holding, execute FUND_SETTLE → order SETTLED, checking credited by the redeem amount, units and reserved units reduced, job DONE; replay idempotent.
- Every test ends with a whole-ledger conservation check (posted and pending debits = credits).

Worker L3-fund-events-r1 wrote confirm/expiry/settle (ran out of turns before committing settle); coordinator added the ledger check and committed.
Red-green (coordinator): making `apply_fund_confirm` / `apply_fund_settle` reject → 2 failed; restored → 3 passed.
