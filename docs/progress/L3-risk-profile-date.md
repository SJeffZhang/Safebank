# L3-risk-profile-date

Fix the live 500 in L3 `get_risk_profile` (and the identical latent bug in `get_profile`)
where `customers.risk_valid_until` holds a timezone-aware datetime string that
`date.fromisoformat` cannot parse.

## What changed

`services/bank-backend/app/reads.py` (commit `2d8fcb6`):

- Added `datetime` to the stdlib import and `BUSINESS_TIMEZONE` to the
  `safebank_bankcore.reserve` import.
- Added module helper `_risk_valid_until(value: str) -> datetime`:
  `datetime.fromisoformat`, then localizes naive values with
  `dt.replace(tzinfo=BUSINESS_TIMEZONE)` (so date-only strings keep the old
  midnight-in-business-tz semantics for the normal `record.valid_until.isoformat()`
  writer path).
- Replaced `date.fromisoformat(customer.risk_valid_until) >= state.sim_now.date()` in
  both `get_risk_profile` and `get_profile` with
  `_risk_valid_until(customer.risk_valid_until) >= state.sim_now`.

Output shape unchanged. `Timestamp` is always timezone-aware
(`packages/contracts/src/safebank_contracts/base.py:72`), so the comparison is safe.

## Tests

Red tests committed first as `beef1e2` in
`tests/obligations_data/test_risk_profile.py` (real-PG tmpfs, style copied from
`test_reads.py`):

- future `+08:00` datetime string → 200, `is_valid` true
- expired `+08:00` datetime string → 200, `is_valid` false
- no assessment → 200, `is_valid` false

### Red run (before fix)

`make test-obligations` → `2 failed, 132 passed, 1 warning` — both failures were
HTTP 500 with `ValueError: Invalid isoformat string: '...T...:00+08:00'` at
`reads.py:388`, confirming the live bug.

### Green run

Not completed in this session. `make lint test-obligations` was run after the fix
but `lint` failed first on the now-unused `date` import (`F401`); that import was
removed and the fix committed, but the obligations suite was not re-run to green
before the turn budget ended. The coordinator must re-run the gates:

```bash
make lint
make test
make test-obligations
```

Expected: the 2 red tests turn green, the no-assessment test stays green, and
`make test` (unit) is unaffected because it does not exercise HTTP reads.
