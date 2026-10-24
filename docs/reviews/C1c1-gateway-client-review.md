# C1c1 Gateway Protocol Client — Independent Review

Reviewer: DeepSeek V4 Pro (read-only, headless CLI). Scope: 6921f69..7d807bf.
Verdict: **REQUEST_CHANGES**

## Method

Read AGENTS.md, PRD §3.2/6.1/6.1.1/9.1/9.3/9.5, C1c1-gateway-client.md, and the actual
gateway wire (schemas/decision.py, scope.py, read.py, enums.py, api/sessions.py,
api/tools.py, errors.py, api/plans.py, api/execution.py, schemas/execution.py). Ran
`tests/agent/gateway` (53 passed). Ran counterexamples via `.venv/bin/python -c` with
`packages/contracts/src` on sys.path (no files written).

## Findings

### P1-1 — Injected `http_client` silently disables `follow_redirects=False` / timeout; forwards `X-Service-Token` to a cross-host redirect

- Evidence: `services/agent/app/gateway/client.py:55-57` builds the default client with
  `follow_redirects=False` and a finite timeout, but when `http_client` is supplied the
  constructor's `timeout` argument is ignored and the injected client's
  `follow_redirects`/`timeout` are trusted verbatim. `_call` (client.py:186-188) issues
  `self._client.request(...)` with no per-request `follow_redirects`/timeout override.
- Reproduced: injecting `httpx.Client(transport=MockTransport(...), follow_redirects=True)`
  and returning a 302 `location: http://evil.example/x` produced a second request to
  `http://evil.example/x` carrying `X-Service-Token: SVC` (httpx strips `Authorization` on
  cross-host redirects but forwards custom headers).
- Impact: the A service token is leaked to an arbitrary redirect target whenever a caller
  constructs the client with a misconfigured shared `httpx.Client`.
- Fix: enforce `follow_redirects=False` and a finite timeout at send time regardless of the
  injected client.

### P2-1 — Wire models looser than the real wire; accept unknown enums / float amounts

- `GatewayDecision.shadow_b1: dict[str, Any] | None` (wire.py:152) vs gateway
  `Decision.shadow_b1: Decision | None` (schemas/decision.py:96); accepts
  `{"decision":"MAYBE","risk":"ORANGE"}`.
- `GatewayUtterance.scope` / `GatewayClarification.partial_scope` / `GatewayReplanHint.scope`
  free dicts accept `transfers[].amount_cents = 1.5` (float).
- `parse_status: str` vs closed `ParseStatus`; `clarification.kind: str` vs
  `ClarificationKind`; `finding.code: str` vs `Code`. All reproduced as accepted.
- `Utterance.kind` is genuinely open `str` on the gateway — correctly kept open.

### P2-2 — Chained `ValidationError` retains raw input on the exception object

- `client.py:91-92` / `112-113` `raise ValueError(...) from exc`; `str()` clean but
  `repr(e.__cause__)` contained the raw input (reproduced).

### P2-3 — No regression exercises the full 18-tool matrix

- Only `get_accounts` round-tripped; Task 1's "18 工具 schema 校验" not implemented as a loop.

## Observations (not blocking)

- `_error_from` drops `message` (secret-safe) but retained the raw `details` dict verbatim
  alongside the validated `decision`.
- G4/G8 are stubs (`api/plans.py:9`, `api/execution.py:10`) returning
  `GatewayError(DEPENDENCY_UNAVAILABLE, 503)`; the client maps these correctly given the
  "G4/G8 尚未真联调" boundary. `ExecutionResult.status` is `Literal["EXECUTED","FAILED"]`
  (plan.py:216); the 202 shape is `ExecutionAccepted` — the client's 200/202 split matches.
- G20 candidate always carries `source_ref` (service.py:484), so `GatewayCandidate.source_ref`
  required is correct.

---

# Fix-round-1 re-review (7d807bf..1f51275)

Reviewer: same read-only DeepSeek worker. Candidate `1f51275d5eb664913abaf8de188b2a5765afd517`.
Verdict: **REQUEST_CHANGES**

## P0 — per-request timeout extension is a `Timeout` object, not a dict; every real (non-Mock) request raises AttributeError

- Evidence: `services/agent/app/gateway/client.py:202`
  `request.extensions["timeout"] = httpx.Timeout(self._timeout)`. httpcore
  `ConnectionPool.handle_request` reads `timeouts = request.extensions.get("timeout", {})`
  then `timeouts.get("pool", None)`; `httpx.Timeout` has no `.get`, so httpcore raises
  `AttributeError: 'Timeout' object has no attribute 'get'`. `_call` catches only
  `httpx.HTTPError`, so the AttributeError escapes and crashes the caller on every real request.
- Reproduced with the default client (no injection, real transport) against `127.0.0.1:1`.
- Fix: write the dict form (`Timeout.as_dict()`) and add a real-transport regression.

## Verified fixed in round 1 (redirect / strict wire / chain / 18 tools)

Redirect, NaN/Inf, strict wire, `from None` chain, and the 18-tool parametrization were all
independently re-verified in round 1.

---

# Fix-round-2 re-review (1f51275..9ebfa12)

Reviewer: same read-only DeepSeek worker. Candidate `9ebfa127cc25a26f7d910c857355c1c8a50a03da`.
Verdict: **APPROVE**

## P0 fixed and verified with a real default httpcore transport over loopback

- `client.py:202` now writes `request.extensions["timeout"] = httpx.Timeout(self._timeout).as_dict()`
  (a dict of four finite floats), matching httpx's own `_set_timeout` and httpcore's
  `.get("pool")` consumer.
- `tests/agent/gateway/test_real_transport.py` (3 tests, all PASSED, not skipped) exercises a
  default `httpx.Client` (real HTTPTransport) over loopback only:
  - refused connection `127.0.0.1:1` → `GatewayTransportError` (independently re-probed:
    `GatewayClientError: GatewayTransportError category=TRANSPORT`, no AttributeError);
  - real `http.server` 200 → `read_tool("get_accounts", ...)` parses, `X-Service-Token` and
    `Authorization` present on the single request;
  - two-loopback cross-host 302 → `GatewayProtocolError(302)`, source server exactly 1 request,
    target server **zero** requests (A token never forwarded).
- Mock assertion (`test_injected_client_timeout_is_enforced_via_request_extensions`) now checks
  the extension is a `dict` with keys `{connect,read,write,pool}`, each a finite float == 5.0 —
  not merely Mock-green.

## Credential-exclusion scope is correctly narrow (not generalized sanitization)

- `_error_from` (client.py:247-254) drops `trace_id` only when it fails the safe charset, or
  when it *is/embeds* the configured `service_token` or the current `context.assistant_token`
  (substring of the exact known values). Ordinary safe trace ids (`trc_01JABC`) are preserved;
  a normal `Decision` in `details` is still kept as its validated `model_dump` unchanged.
- `_error_from` now takes `context` (sole caller `client.py:215` updated); no other callers.
- Tests `test_trace_id_equal_to_service_token_is_omitted` /
  `test_trace_id_embedding_credentials_is_omitted` cover equal-value and embedded forms; the
  pre-existing safe-shape-preserved test still passes — no over-dropping of benign trace ids.

## Regression

- `tests/agent/gateway -q`: 129 passed (124 + 3 real transport + 2 trace credential).
- `tests/agent -q`: 212 passed, zero skip/xfail.
- All prior fixes (redirect, strict wire, exception chain, 18 tools) remain green.

## Residual findings

None. P0/P1/P2 from both prior rounds are closed; the new real-transport suite closes the
Mock-only coverage gap that let the P0 through.
