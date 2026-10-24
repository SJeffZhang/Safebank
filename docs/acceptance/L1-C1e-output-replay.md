# C1e Acceptance Report — Output Verification, Cards & Replay

**Date**: 2026-10-25
**Branch**: `codex/c1e-output-replay`
**Base**: `main` (0472689 at task start, later merged)

## Tasks Completed

| Task | Description | Tests | Status |
|------|-------------|-------|--------|
| 0 | ADR-004 (streaming/replay) + ADR-005 (frontend placeholder) | — | Done |
| 1 | `llm/replay.py` — recording/replay infrastructure | 46 | Done |
| 2 | `llm/fixed_templates.py` — centralised template store | — | Done |
| 3 | `llm/respond.py` — RespondAdapter (LLM-driven responses) | 31 | Done |
| 4 | `llm/verify.py` — OutputVerifier (fact-checking + masking) | 48 | Done |
| 5 | `llm/cards.py` — deterministic UICard generator | 18 | Done |
| 6 | SSE card push integration in `main.py` | 3 | Done |
| 7 | Final gates | — | Done |

## Final Gate Results

```bash
make test           2647 passed, 646 skipped   (12.36s)
make test-agent-db    34 passed, 430 deselected (2.81s)
make test-replay      46 passed                  (0.64s)
make test-verify      48 passed                  (0.62s)
make test-cards       18 passed                  (0.61s)
make lint             All checks passed
make demo             exit 2 (not implemented, expected)
```

All exit 0.

## New Files

- `services/agent/app/llm/replay.py` — Recording/replay with SHA256-based replay keys
- `services/agent/app/llm/fixed_templates.py` — 22 fixed + 3 LLM-driven templates
- `services/agent/app/llm/respond.py` — RespondAdapter (state-aware, routes DENY/UNKNOWN/EXECUTED/FAILED/REPLAN_USER/analysis)
- `services/agent/app/llm/verify.py` — OutputVerifier (extraction, fact-checking, number masking, LLM repair)
- `services/agent/app/llm/cards.py` — Deterministic UICard generator (no LLM)
- `tests/agent/test_replay.py` — 46 replay tests
- `tests/agent/test_respond.py` — 31 respond tests
- `tests/agent/test_verify.py` — 48 verify tests
- `tests/agent/test_cards.py` — 18 cards tests
- `docs/decisions/ADR-004-streaming-replay.md`
- `docs/decisions/ADR-005-frontend-placeholder.md`

## Modified Files

- `services/agent/app/main.py` — Ports changed to RespondAdapter + OutputVerifier; cards pushed as SSE custom event
- `services/agent/app/graph/routing.py` — Import from fixed_templates
- `tests/agent/test_service.py` — 3 new SSE card push tests

## Architecture Notes

- **Ports pattern**: `respond` is `RespondAdapter(llm)`, `verify_output` is `OutputVerifier(llm)`. Both are injected into the graph via `Ports`.
- **Cards are NOT in AgentState**: Pushed via SSE `custom` event, generated from state deterministically.
- **Number masking**: Always runs last — masks ≥8 digit sequences, preserves dates and amounts.
- **Fact checking**: Extracts amounts (¥/元), percentages, card last4s from reply text; compares against state facts; ±1 cent tolerance.
- **UICard models**: Frozen Pydantic Contract instances; safe_data() strips untrusted fields (name, memo, alias, etc.).

## Known Limitations

- `make demo` not yet implemented (expected exit 2)
- Cards are generated during SSE streaming, not persisted to AgentState
- No real LLM used in tests (fake ports throughout)
- Replay infrastructure defined but not wired into the graph yet