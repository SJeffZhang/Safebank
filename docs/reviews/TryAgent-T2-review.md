# TryAgent T2 --confirm Review

**Reviewer**: R-TryT2 (Claude, read-only, static)
**Date**: 2026-10-28
**Range**: `5e11bb5..432fee1` (single commit: `feat(try-agent): T2 --confirm flag with explicit human confirmation flow`)
**Files**: `scripts/try_agent.py` (+468/−2), `tests/try_agent/test_confirm.py` (+1105)

## Conclusion

**APPROVE** — no P0 or P1 issues. Five P2 observations below, none blocking.

---

## P0 / P1

None found.

---

## P2

### P2-1: EXIT_CONFIRMED = 9 (non-zero success code)

`scripts/try_agent.py:42`: `EXIT_CONFIRMED = 9`. In T1, `EXIT_PENDING_APPROVAL = 0` was the "normal" exit (work reached the approval panel). With `--confirm`, the only true success is G5=EXECUTED, which returns exit code 9.

This is a **reasonable design**: one can distinguish via exit code whether the script stopped waiting at the panel (0) vs completed execution (9). However, in shell scripting `if ./try_agent --confirm ...` will treat 9 as false, which is unintuitive. Not blocking — just document the exit codes in the module docstring or README.

### P2-2: `test_resume_sse_deny_returns_deny` assertion is imprecise

`tests/try_agent/test_confirm.py:618`: `assert exit_code != mod.EXIT_CONFIRMED`. This test injects DENY in the resume SSE and a G5 response with status `"DENIED"`. The actual exit code is `EXIT_EXECUTION_UNKNOWN` (fall-through at `try_agent.py:1014` — "Any other status"), not `EXIT_DENY`. The assertion `!= EXIT_CONFIRMED` is correct but too loose — it would pass even if the code returned EXIT_CONFIRMED for a completely wrong reason on a different path. Consider asserting `exit_code == mod.EXIT_EXECUTION_UNKNOWN`.

### P2-3: No test for empty OTP input

`try_agent.py:778-784`: when `_getpass("OTP: ")` returns an empty string, the code returns `EXIT_REJECTED_BY_USER`. There is no test case covering this path. The semantic is reasonable (empty OTP = user backed out), but the path should be tested.

### P2-4: G10 response is not checked before prompting for OTP

`try_agent.py:768-770`: G6 non-200 returns HTTP_FAILURE, but if G10 returns 200 with `{"sent": false}` or any other unexpected body, the code proceeds to prompt for OTP and includes it in G6 regardless. In practice G10 returning 200 but `sent != true` would be a protocol anomaly, but the defensive check is missing. G10 is a "fire and forget" fire SMS — if it fails silently, the user's typed OTP will be rejected by G6 which has no challenge to match. This isn't a security issue (G6 still validates), but the UX error message would be confusing.

### P2-5: `test_no_confirm` could assert G9 was called

`tests/try_agent/test_confirm.py:344-349`: the test asserts no G6/G7 calls, which proves the confirm flag is off. Adding an assertion that G9 WAS called would also prove that the T1 flow still runs the approval panel display step. The mock handler (`_FullHandler`) does serve G9, so the call happens implicitly — the test just doesn't verify it.

---

## Items Verified

### 1. Without --confirm, behaviour identical to T1 ✓

- `try_agent.py:591`: the entire `--confirm` block is gated by `if config.confirm:`. When `False`, execution falls through to `return EXIT_PENDING_APPROVAL` at line 1022 — exactly T1's flow.
- `try_agent.py:998-1002` (G1–G5), `1006-1021` (L1 thread/SSE), `1030-1078` (G9 panel) — all unchanged from T1.
- `test_confirm.py:314-349` (`test_no_confirm_returns_pending_approval`): exit code == EXIT_PENDING_APPROVAL, and no `/confirm` or `/reject` URLs appear in the request log.
- The `run()` signature added `input_fn`/`getpass_fn` parameters with defaults — backwards-compatible, T1 callers don't need to pass them.

### 2. User input ≠ "yes" → no G6; risk_ack only on explicit confirmation ✓

- `try_agent.py:609-610`: `answer = _input(...).strip()` → `if answer != "yes":` → G7 reject → `return EXIT_REJECTED_BY_USER`. G6 is never called on non-"yes" answers.
- `try_agent.py:666-672`: `risk_ack_val` starts as `None`. Only set to `True` at line 723 when user explicitly types `"yes"` to the risk acknowledge prompt. If they type anything else → G7 reject, no G6.
- `try_agent.py:793-794`: `if risk_ack_val is not None: confirm_req["risk_ack"] = risk_ack_val` — only added to the body when the user explicitly confirmed. Cannot be auto-set to `True`.
- `test_confirm.py:358-394` (`test_user_types_no_calls_g7_reject`): "no" → G7 called (1), G6 not called (0).
- `test_confirm.py:396-427` (`test_user_types_empty_calls_g7_reject`): empty input → EXIT_REJECTED_BY_USER.
- `test_confirm.py:693-754` (`test_risk_ack_not_sent_without_user_input`): user says "yes" to confirm but "no" to risk ack → G7 only, no G6.

### 3. G6 view_hash from G9; approval_id consistent ✓

- `try_agent.py:664`: `view_hash: str = panel.get("view_hash", "")` — panel is the response from the G9 GET `/approvals/{approval_id}` call (line 557–585).
- The `approval_id` used throughout is the same one extracted from the L1 SSE interrupt at line 508–510, used for G9 (line 549–558), G6 (line 799–806), G7, G10, and L1 resume (line 843).
- `test_confirm.py:436-471` (`test_confirm_carries_view_hash_from_g9`): wrapper captures the G6 request body, asserts `body["view_hash"] == "abcdef01"` — exact match with the panel fixture's `view_hash`.
- The view_hash is also forwarded into G10 (line 730) and G7 (line 614, line 673).

### 4. OTP via getpass only, never leaked ✓

- `try_agent.py:777`: `otp_val = _getpass("OTP: ").strip()` — `_getpass` defaults to `getpass.getpass` (no echo), injectable via `getpass_fn` for testing.
- OTP value goes only into G6 confirm body (line 792). It is never written into events (G6 request event at lines 795–802 only records URL, no body).
- `test_confirm.py:838-899` (`test_otp_not_in_output`): verifies that `"s3cret-otp"` (the getpass return value) does not appear in any serialised SanitisedEvent.
- `test_confirm.py:761-836` (`test_red_sends_otp_and_reads_via_getpass`): verifies getpass was called (via side-effect counter), G10 was called, and G6 received the correct OTP.
- `try_agent.py:93-98`: `_ensure_no_secrets` scans serialised output for `"Bearer eyJ"`, `"FRONTEND_SERVICE_TOKEN"`, `"AGENT_SERVICE_TOKEN"`.
- `try_agent.py:101-108`: `_sanitise_headers` masks `Authorization` and `X-Service-Token` headers in all event detail.
- `try_agent.py:260`: PIN in login event is replaced with `"***"`.
- `try_agent.py:1140-1143`: out_path JSON metadata only includes `customer_id`, `text`, `request_id_prefix` — no PIN, token, or OTP.
- `test_confirm.py:1065-1105` (`test_otp_not_in_events`): verifies no token leaks in serialised events.
- `test_confirm.py:901-972` (`test_red_with_risk_ack_and_otp`): verifies both risk_ack and OTP arrive in G6 body for the RED + risk_ack combined flow.

### 5. Success only on G5=EXECUTED; non-success exit codes ✓

- `try_agent.py:1005`: `EXECUTED` → `EXIT_CONFIRMED` (the only success path).
- `try_agent.py:1009`: `UNKNOWN` / `EXECUTING` → `EXIT_EXECUTION_UNKNOWN`.
- `try_agent.py:1011`: `FAILED` → `EXIT_EXECUTION_FAILED`.
- `try_agent.py:1014`: any other status → `EXIT_EXECUTION_UNKNOWN`.
- `try_agent.py:968-988`: G5 returns 409 STATE_CHANGED → extract `details.decision` → `EXIT_STATE_CHANGED`.
- `try_agent.py:1016-1020`: no plan_id → checks resume SSE for DENY → `EXIT_DENY`; else `EXIT_UNKNOWN`.
- Network/HTTP errors → `EXIT_HTTP_FAILURE` (consistent with T1, no new path).
- `test_confirm.py:509-541` (`test_g5_failed_returns_exit_execution_failed`): FAILED status → correct exit code.
- `test_confirm.py:543-575` (`test_g5_unknown_returns_exit_execution_unknown`): UNKNOWN status → correct exit code.
- `test_confirm.py:978-1024` (`test_state_changed_from_g8_via_g5`): 409 → EXIT_STATE_CHANGED.
- `test_confirm.py:577-618` (`test_resume_sse_deny_returns_deny`): resume with DENY → not EXIT_CONFIRMED.

### 6. Assertions check actual requests, not just exit codes ✓

- `test_confirm.py:346-349`: asserts `confirm_urls == []`, `reject_urls == []` on no-confirm.
- `test_confirm.py:390-394`: asserts `len(reject_calls) == 1`, `confirm_calls == []` on user reject (checks actual HTTP calls to specific paths).
- `test_confirm.py:458-471`: wraps handler to capture G6 request body bytes, asserts `view_hash` value.
- `test_confirm.py:504-507`: asserts L1 stream was called twice (initial + resume).
- `test_confirm.py:689-691`: captures G6 body and asserts `body.get("risk_ack") is True`.
- `test_confirm.py:749-753`: asserts `confirm_calls == []`, `len(reject_calls) == 1` on risk-ack decline.
- `test_confirm.py:828-834`: captures G6 body `"otp"` value, asserts G10 `/otp` was called, asserts getpass was invoked.
- `test_confirm.py:968-972`: asserts both `risk_ack` and `otp` in G6 body for combined flow.
- All tests use mock transport — no real backend calls.

---

## Additional Notes

- **Flow completeness**: The confirm flow covers all four paths from the G9 panel: reject (G7), confirm without extras (G6), risk_ack (G6 with risk_ack:true), RED+OTP (G10 → getpass → G6 with OTP), and RED+risk_ack+OTP (combined). ✓
- **L1 resume**: After G6 confirm, the script resumes L1 with `{"command": {"resume": approval_id}}` (line 842). This matches the expected protocol — the same approval_id extracted from the initial SSE interrupt is fed back for resume. ✓
- **G5 as ground truth**: The script does NOT trust the resume SSE's execution dict alone. It independently calls G5 (line 926–969) to get the authoritative plan status, which is the correct approach — L1's reply is not a reliable indicator of L3 execution outcome. ✓
- **Thread safety**: The `_FullHandler._stream_count` counter (line 262) differentiates the initial stream from the resume stream. This is a test-only concern and handled correctly. ✓
- **Immutable Config**: The `Config` dataclass with `confirm: bool = False` is backwards-compatible — existing T1 callers don't need to change. ✓