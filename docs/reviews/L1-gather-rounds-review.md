# L1 Gather Rounds — Independent Review

Scope: `git diff origin/main..HEAD` (270c608 test, 9690240 fix, 413c3ff docs).
Files changed: `services/agent/app/llm/gather.py`, `tests/agent/test_llm_adapters.py`, `docs/progress/L1-gather-rounds.md`.

## Verdict

APPROVE (one non-blocking P2 noted).

## Findings (P0, P1, P2)

- **P2-1** — `_finish_without_tools` returns an empty `reads` list without raising when the round cap is hit with **zero** collected reads and the final tools-free call produces plain text (no `tool_calls`). The "zero reads still raises" guarantee is only enforced when the final call still emits tool calls (`gather.py:148-150`); a text final answer with zero reads silently returns `[]`. Not a security hole — the final message content is discarded, never turned into `ReadRecord`s, so the model cannot invent read data — but it is inconsistent with the stated intent and is not covered by any test. For transfer intents with `supplement_transfer_reads=True` the deterministic supplement masks it; without supplement (e.g. the `live.py` path) a plan can proceed with no reads and rely on L2 to reject.

## Checks

- **Cap → tools-free final call returns collected reads instead of raising.** Verified in `gather.py:125-128,130-154`: loop exhausts after `max_rounds` tool rounds, then `_finish_without_tools` invokes `self._plain_model` (unbound `llm`, no tools) once and returns the collected `reads`. Test `test_gather_returns_collected_reads_at_round_cap` asserts 3 reads returned, 4 total requests, and `"tools" not in body_of(scripted, 3)`. ✓
- **Final round runs without tools.** `self._plain_model = llm` (gather.py:67) is the raw model; `bind_tools(read_tools())` only applies to `self._model`. Both new tests assert the final request body has no `tools` key. ✓
- **Zero reads still raises (model still wants reads).** `gather.py:148-150`: `if message.tool_calls and not reads: raise LlmToolError`. `test_gather_zero_reads_at_cap_still_raises` (all batches rejected → 0 reads, final response is a `tool_response`) asserts raise. Passes. ✓
- **No reads beyond the cap.** The final call never executes reads: `_finish_without_tools` does not call `_execute`/`_read_executor`; hallucinated `tool_calls` in the final response are ignored (only used to decide raise-vs-return). `_supplement_reads` can append deterministic supplemental reads, but that is pre-existing behavior also applied on early-stop, not model-driven. ✓
- **Invalid-call and rejected-batch handling unchanged.** `gather.py:89-124` (loop body, `invalid_tool_calls` check, `_validate_calls`, rejected-batch feed-back) is untouched by the diff. `_finish_without_tools` mirrors the loop's `invalid_tool_calls → LlmToolError` and has no tool execution. Existing `test_gather_rejects_invalid_tool_calls` / `test_gather_rejects_write_and_unknown_tools` still pass. ✓
- **Final tools-free call cannot let the model invent read data.** Confirmed: `_finish_without_tools` returns only the collected `ReadRecord` list (or `_supplement_reads(...)`); the final message's text/tool_calls are discarded and never appended to `reads`. The graph's gather node (`graph.py:670-690`, `832-839`) consumes only the return value as `reads`, and each `ReadRecord` is produced exclusively by `_execute` (wrapped via `apply_untrusted_wrapping`) or the deterministic `_execute_supplemental`. ✓
- **Test command.** `.venv/bin/pytest tests/agent/test_llm_adapters.py -q` → `56 passed in 1.77s`. ✓

Max 8 file reads: used 4 (gather.py, test top, llm_mock.py, graph.py).
