# try-agent T1 — CLI Protocol Driver (2026-10-26)

## Status: DONE (local proto tests only — no Docker/live stack)

Head: (see commit)

## Scope

`scripts/try_agent.py` — CLI tool that drives the full three-layer flow:
G1 login → G15 session → G3 utterance → L1 SSE → G9 approval panel.

Stops at PENDING_APPROVAL; does not confirm, execute, or call G6/G8 (T2).

## Exit codes
- 0: PENDING_APPROVAL (G9 panel fetched and returned)
- 1: COMPLETED (L1 returned ALLOW/COMPLETED)
- 2: CLARIFY
- 3: REPLAN
- 4: DENY
- 5: UNKNOWN
- 6: HTTP_FAILURE
- 7: CONFIG_ERROR

## Makefile
- `make try-agent ARGS="--customer-id cus_1 --text 'hi'"`: runs inside frontend container
- `make test-try-agent`: pytest tests/try_agent/

## Tests (28 passed, MockTransport only)
- SSE parsing: updates/values/messages/custom/error/comment/multi-line/empty
- Full flow with approval: G1→G15→G3→L1 SSE→G9, exits PENDING_APPROVAL
- Token isolation: app_token never sent to agent
- Terminal decisions: ALLOW/COMPLETED/CLARIFY/REPLAN/DENY
- No G6/G8 called for non-approval paths
- Error handling: 401 login/SSE error/404 stream
- No secrets in output
- CLI: --help, required args, pin from arg, default/custom URLs
- Sanitisation: JWT/service-token blocks, header masking

## Not covered (MockTransport only, no real stack)
- Real Docker containers (gateway/agent not running)
- Real L1 DeepSeek LLM (no API calls)
- Live SSE stream (MockTransport returns static bytes)
- G6/G8 followup (T2)
- --out file writing (uses real IO in live mode; MockTransport tests don't exercise it)
- getpass interaction (MockTransport tests use --pin flag)

## File whitelist compliance
- Created: scripts/try_agent.py, tests/try_agent/__init__.py, tests/try_agent/test_driver.py
- Modified: Makefile (try-agent + test-try-agent targets)
- No changes to packages/ services/ production/ CI/ AGENTS/ PRD/ evaluation/