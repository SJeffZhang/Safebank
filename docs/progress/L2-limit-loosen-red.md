# L2-limit-loosen-red

Security bug fix: raising a card single limit (e.g. 1000 → 8000 元) was rated
YELLOW (risk ack only, no OTP). PRD requires C-3 (raising a card limit) and C-4
(turning any card channel on) to be RED; lowering a limit / turning channels off
stay YELLOW.

## Change

- `services/gateway/app/policy/registry.py`: added `step_base_risk(tool, args,
  shadow_state)` — returns `"RED"` when `action_kind(...)` is
  `CARD_LIMIT_LOOSEN` or `CARD_CONTROL_LOOSEN`, otherwise the static
  `tools.yaml` `base_risk`. `action_kind()` already resolves direction against
  the shadow state, so this is a pure, direction-aware upgrade; nothing else is
  weakened.
- `services/gateway/app/pipeline/entity.py`: entity stage now sets
  `ResolvedStep.base_risk` from `step_base_risk(...)` instead of the raw
  `rule.base_risk`. The decision stage already maxes step base risks, so a
  LOOSEN step now makes the whole plan RED (and thus OTP-required) the same way
  other RED plans are.

## Tests (red → green)

Added to `tests/gateway/test_policy.py`:

- limit raise (500k → 800k 分) → RED
- limit lower (800k → 100k 分) → YELLOW
- channel on (online off → on) → RED
- channel off (online on → off) → YELLOW
- mixed plan (transfer YELLOW + limit-raise LOOSEN step) aggregates to RED via
  `DecisionStage._aggregate`

Red: `ImportError: cannot import name 'step_base_risk'` (committed 152228d).
Green after fix: `tests/gateway/test_policy.py -q` = 12 passed.

## Suites

- `make lint` — pass (417 files formatted)
- `make test` — 3260 passed, 879 skipped
- `make test-w3a` — 45 passed
- `make test-w3b` — 41 passed
- `make test-w2` — 256 passed

No existing tests asserted the old YELLOW behavior for card limit/control
loosening, so no other tests needed updating.
