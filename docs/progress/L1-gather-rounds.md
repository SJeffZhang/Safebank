# L1-gather-rounds

## What changed

`services/agent/app/llm/gather.py` — `GatherAdapter` no longer raises
`LlmToolError("gather exceeded max rounds")` when the model keeps requesting
reads at the round cap. Instead, after the cap it makes one final tools-free
call (raw `llm` without `bind_tools`) so the model must stop, and returns the
reads collected so far.

- `__init__` keeps the raw model as `_plain_model` alongside the tools-bound
  `_model`.
- The post-loop `raise` is replaced with `_finish_without_tools(...)`:
  - invokes `_plain_model` (no tools bound),
  - if the model still returns tool calls on that tools-free round, returns the
    collected reads — unless zero reads were collected, in which case it still
    raises (nothing useful to hand back),
  - otherwise returns the collected reads (applying `_supplement_reads` when
    enabled, unchanged).

The round cap is not raised, no extra reads execute beyond the cap, and the
existing invalid-call / rejected-batch behaviour is unchanged.

## Tests

`tests/agent/test_llm_adapters.py`:

- Renamed `test_gather_stops_at_round_limit` →
  `test_gather_returns_collected_reads_at_round_cap`: model wants reads on every
  round → no exception, 3 reads collected (exactly cap rounds executed), 4
  requests total, and the final request has no `tools` key.
- Added `test_gather_final_round_stops_with_text`: model complies with a text
  answer on the tools-free final round → reads returned, final request has no
  `tools`.
- Added `test_gather_zero_reads_at_cap_still_raises`: all rounds rejected
  (missing required args) and model still wants reads past the cap → still
  raises, zero reads executed.

## Red → green

- Before fix: the two new/renamed tests failed with
  `LlmToolError: gather exceeded max rounds (3)`.
- After fix: `pytest tests/agent/test_llm_adapters.py -q -k gather` →
  `17 passed`.

## Gates

- `make lint`: All checks passed; 417 files formatted.
- `.venv/bin/pytest tests/agent -q`: `522 passed, 38 skipped`.
- `make test`: `3257 passed, 879 skipped`.
