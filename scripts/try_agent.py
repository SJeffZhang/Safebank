"""T1: CLI driver for the full three-layer flow: login → session → utterance →
L1 SSE → approval panel display.  Stops at PENDING_APPROVAL; does not
confirm, execute, or call G6/G8 (T2 will add explicit human confirmation).

Usage:
    FRONTEND_SERVICE_TOKEN=... \\
    python -m scripts.try_agent --customer-id cus_zs_1 --text "转800元给李四" --pin 123456

    # With getpass (no echo):
    python -m scripts.try_agent --customer-id cus_zs_1 --text "查询余额"

    # Inside docker compose:
    docker compose exec frontend python -m scripts.try_agent ...

Can also write sanitised event log to --out path.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import uuid
from dataclasses import dataclass, field

import httpx

# ── Exit codes ────────────────────────────────────────────────────────────

EXIT_PENDING_APPROVAL = 0
EXIT_COMPLETED = 1
EXIT_CLARIFY = 2
EXIT_REPLAN = 3
EXIT_DENY = 4
EXIT_UNKNOWN = 5
EXIT_HTTP_FAILURE = 6
EXIT_CONFIG_ERROR = 7

EXIT_LABELS: dict[int, str] = {
    EXIT_PENDING_APPROVAL: "PENDING_APPROVAL",
    EXIT_COMPLETED: "COMPLETED",
    EXIT_CLARIFY: "CLARIFY",
    EXIT_REPLAN: "REPLAN",
    EXIT_DENY: "DENY",
    EXIT_UNKNOWN: "UNKNOWN",
    EXIT_HTTP_FAILURE: "HTTP_FAILURE",
    EXIT_CONFIG_ERROR: "CONFIG_ERROR",
}


# ── Models ────────────────────────────────────────────────────────────────


@dataclass
class Config:
    gateway_url: str
    agent_url: str
    customer_id: str
    text: str
    pin: str
    out_path: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SanitisedEvent:
    kind: str  # http_request | http_response | sse_event | result | error
    detail: dict | str


# ── Helpers ───────────────────────────────────────────────────────────────


def _ensure_no_secrets(data: str) -> str:
    """Assert no synthetic secrets leak into plaintext output (defence-in-depth)."""
    for sentinel in ("Bearer eyJ", "FRONTEND_SERVICE_TOKEN", "AGENT_SERVICE_TOKEN"):
        if sentinel in data:
            raise RuntimeError(f"SECRET_LEAK: output contains '{sentinel}'")
    return data


def _sanitise_headers(headers: dict) -> dict:
    """Mask known auth values in displayed headers."""
    masked = {}
    for k, v in headers.items():
        if k.lower() in ("authorization", "x-service-token"):
            masked[k] = "***REDACTED***"
        else:
            masked[k] = v
    return masked


# ── SSE parser ─────────────────────────────────────────────────────────────


def parse_sse(
    response: httpx.Response,
) -> list[dict]:
    """Parse a Server-Sent Events stream into a list of event dicts.

    Each event dict has keys matching the SSE event type (updates, values,
    custom, messages, error).

    SSE wire format:
        event: updates
        data: {"__interrupt__": [...]}

        event: values
        data: {...}

    Raises RuntimeError on error events.
    """
    events: list[dict] = []
    current_event: str | None = None
    # Accumulate data lines for the current event (may be multi-line).
    current_data: list[str] = []

    def _flush() -> None:
        nonlocal current_event, current_data
        if current_data:
            evt_type = current_event or "unknown"
            payload = _ensure_no_secrets("\n".join(current_data))
            try:
                body = json.loads(payload)
            except json.JSONDecodeError:
                body = {"raw": payload}
            events.append({"type": evt_type, "body": body})
        current_event = None
        current_data = []

    for raw_line in response.iter_lines():
        line = raw_line.strip("\r\n")
        if line == "":
            # Empty line = event separator
            _flush()
            continue
        if line.startswith(":"):
            # Comment — skip.
            continue
        if ":" in line:
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                _flush()
                current_event = value
            elif field == "data":
                current_data.append(value)
        # Lines without a colon are ignored per SSE spec.

    _flush()  # trailing event
    return events


# ── Flow ───────────────────────────────────────────────────────────────────


def run(config: Config, client: httpx.Client, events: list[SanitisedEvent]) -> int:
    """Execute the full try-agent flow.  Returns an exit code."""

    # ── 1. G1 Login ───────────────────────────────────────────────────
    login_req = {"customer_id": config.customer_id, "pin": config.pin}
    events.append(
        SanitisedEvent(
            kind="http_request",
            detail={
                "to": "G1",
                "url": f"{config.gateway_url}/auth/login",
                "customer_id": config.customer_id,
                "pin": "***",
            },
        )
    )
    login_resp = client.post(
        f"{config.gateway_url}/auth/login",
        headers={"X-Service-Token": os.environ["FRONTEND_SERVICE_TOKEN"]},
        json=login_req,
    )
    events.append(
        SanitisedEvent(
            kind="http_response", detail={"from": "G1", "status": login_resp.status_code}
        )
    )
    if login_resp.status_code != 200:
        events.append(
            SanitisedEvent(
                kind="error",
                detail=(f"G1 login failed HTTP {login_resp.status_code}: {login_resp.text[:500]}"),
            )
        )
        return EXIT_HTTP_FAILURE
    login_body = login_resp.json()
    app_token = login_body["app_token"]
    events.append(
        SanitisedEvent(
            kind="result", detail={"event": "logged_in", "customer": login_body.get("customer", {})}
        )
    )

    # ── 2. G15 Create session ─────────────────────────────────────────
    sid_req = {"request_id": f"{config.request_id}-sid"}
    events.append(
        SanitisedEvent(
            kind="http_request", detail={"to": "G15", "url": f"{config.gateway_url}/sessions"}
        )
    )
    sid_resp = client.post(
        f"{config.gateway_url}/sessions",
        headers={
            "X-Service-Token": os.environ["FRONTEND_SERVICE_TOKEN"],
            "Authorization": f"Bearer {app_token}",
        },
        json=sid_req,
    )
    events.append(
        SanitisedEvent(kind="http_response", detail={"from": "G15", "status": sid_resp.status_code})
    )
    if sid_resp.status_code not in (200, 201):
        events.append(
            SanitisedEvent(
                kind="error",
                detail=(
                    f"G15 session creation failed HTTP {sid_resp.status_code}: "
                    f"{sid_resp.text[:500]}"
                ),
            )
        )
        return EXIT_HTTP_FAILURE
    session_id = sid_resp.json()["session_id"]
    events.append(
        SanitisedEvent(kind="result", detail={"event": "session_created", "session_id": session_id})
    )

    # ── 3. G3 Register utterance ──────────────────────────────────────
    utt_req = {
        "request_id": f"{config.request_id}-utt",
        "kind": "typed",
        "text": config.text,
    }
    events.append(
        SanitisedEvent(
            kind="http_request",
            detail={
                "to": "G3",
                "url": f"{config.gateway_url}/sessions/{session_id}/utterances",
                "text": config.text,
            },
        )
    )
    utt_resp = client.post(
        f"{config.gateway_url}/sessions/{session_id}/utterances",
        headers={
            "X-Service-Token": os.environ["FRONTEND_SERVICE_TOKEN"],
            "Authorization": f"Bearer {app_token}",
        },
        json=utt_req,
    )
    events.append(
        SanitisedEvent(kind="http_response", detail={"from": "G3", "status": utt_resp.status_code})
    )
    if utt_resp.status_code not in (200, 201):
        events.append(
            SanitisedEvent(
                kind="error",
                detail=(
                    f"G3 utterance registration failed HTTP {utt_resp.status_code}: "
                    f"{utt_resp.text[:500]}"
                ),
            )
        )
        return EXIT_HTTP_FAILURE
    utt_body = utt_resp.json()
    assistant_token = utt_body["assistant_token"]
    utterance_id = utt_body["utterance"]["id"]
    events.append(
        SanitisedEvent(
            kind="result", detail={"event": "utterance_registered", "utterance_id": utterance_id}
        )
    )

    # ── 4. L1 Create thread ───────────────────────────────────────────
    thread_req = {"thread_id": session_id}
    events.append(
        SanitisedEvent(
            kind="http_request", detail={"to": "L1", "url": f"{config.agent_url}/threads"}
        )
    )
    thread_resp = client.post(
        f"{config.agent_url}/threads",
        headers={"Authorization": f"Bearer {assistant_token}"},
        json=thread_req,
    )
    events.append(
        SanitisedEvent(
            kind="http_response", detail={"from": "L1", "status": thread_resp.status_code}
        )
    )
    if thread_resp.status_code != 200:
        events.append(
            SanitisedEvent(
                kind="error",
                detail=(
                    f"L1 thread creation failed HTTP {thread_resp.status_code}: "
                    f"{thread_resp.text[:500]}"
                ),
            )
        )
        return EXIT_HTTP_FAILURE
    events.append(
        SanitisedEvent(kind="result", detail={"event": "thread_created", "thread_id": session_id})
    )

    # ── 5. L1 SSE stream ──────────────────────────────────────────────
    stream_req = {
        "input": {
            "utterances": [{"id": utterance_id, "kind": "typed", "text": config.text}],
        }
    }
    events.append(
        SanitisedEvent(
            kind="http_request",
            detail={"to": "L1", "url": f"{config.agent_url}/threads/{session_id}/runs/stream"},
        )
    )
    stream_resp = client.send(
        client.build_request(
            "POST",
            f"{config.agent_url}/threads/{session_id}/runs/stream",
            headers={
                "Authorization": f"Bearer {assistant_token}",
                "Accept": "text/event-stream",
            },
            json=stream_req,
        ),
        stream=True,
    )
    events.append(
        SanitisedEvent(
            kind="http_response", detail={"from": "L1", "status": stream_resp.status_code}
        )
    )
    if stream_resp.status_code != 200:
        events.append(
            SanitisedEvent(
                kind="error",
                detail=(
                    f"L1 stream failed HTTP {stream_resp.status_code}: {stream_resp.text[:500]}"
                ),
            )
        )
        return EXIT_HTTP_FAILURE

    sse_events = parse_sse(stream_resp)

    approval_id: str | None = None
    l1_reply: str = ""
    l1_decision: str = "UNKNOWN"
    l1_cards: list = []

    for evt in sse_events:
        evt_type = evt["type"]
        body = evt["body"]
        events.append(
            SanitisedEvent(
                kind="sse_event",
                detail={
                    "type": evt_type,
                    "body_keys": list(body.keys()) if isinstance(body, dict) else None,
                },
            )
        )

        if evt_type == "error":
            msg = body.get("message", body.get("raw", str(body)))
            events.append(SanitisedEvent(kind="error", detail=f"L1 error event: {msg}"))
            return EXIT_UNKNOWN

        if evt_type == "updates":
            interrupts = body.get("__interrupt__", [])
            for intr in interrupts:
                intr_value = intr.get("value", {}) if isinstance(intr, dict) else {}
                aid = intr_value.get("approval_id", "") if isinstance(intr_value, dict) else ""
                if aid:
                    approval_id = aid

        if evt_type == "values":
            # Extract decision, reply_draft, cards
            l1_decision = body.get("decision", "UNKNOWN")
            l1_reply = body.get("reply_draft", "")
            # Cards may be in values too (pre-C1e)
            if body.get("cards"):
                l1_cards = body["cards"]

        if evt_type == "custom":
            custom_body = body if isinstance(body, dict) else {}
            if custom_body.get("type") == "cards":
                l1_cards = custom_body.get("cards", [])

        if evt_type == "messages":
            msgs = body if isinstance(body, list) else []
            for m in msgs:
                if isinstance(m, dict) and m.get("type") == "ai":
                    l1_reply = m.get("content", l1_reply)

    events.append(
        SanitisedEvent(
            kind="result",
            detail={
                "event": "l1_complete",
                "decision": l1_decision,
                "reply": l1_reply,
                "cards_count": len(l1_cards),
            },
        )
    )

    # ── Route based on L1 result ──────────────────────────────────────
    if approval_id:
        # ── 6. G9 Approval panel ──────────────────────────────────
        events.append(
            SanitisedEvent(
                kind="http_request",
                detail={"to": "G9", "url": f"{config.gateway_url}/approvals/{approval_id}"},
            )
        )
        g9_resp = client.get(
            f"{config.gateway_url}/approvals/{approval_id}",
            headers={
                "X-Service-Token": os.environ["FRONTEND_SERVICE_TOKEN"],
                "Authorization": f"Bearer {app_token}",
            },
        )
        events.append(
            SanitisedEvent(
                kind="http_response", detail={"from": "G9", "status": g9_resp.status_code}
            )
        )
        if g9_resp.status_code != 200:
            events.append(
                SanitisedEvent(
                    kind="error",
                    detail=(
                        f"G9 approval panel failed HTTP {g9_resp.status_code}: {g9_resp.text[:500]}"
                    ),
                )
            )
            return EXIT_HTTP_FAILURE

        panel = g9_resp.json()
        events.append(
            SanitisedEvent(kind="result", detail={"event": "approval_panel", "panel": panel})
        )
        return EXIT_PENDING_APPROVAL

    # No approval needed — L1 returned a terminal decision.
    if l1_decision in ("ALLOW", "ALLOWED", "COMPLETED"):
        return EXIT_COMPLETED
    if l1_decision in ("CLARIFY", "CLARIFICATION"):
        return EXIT_CLARIFY
    if l1_decision in ("REPLAN",):
        return EXIT_REPLAN
    if l1_decision in ("DENY", "DENIED"):
        return EXIT_DENY
    return EXIT_UNKNOWN


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(
        description="SafeBank try-agent: drive the full three-layer L1 flow",
    )
    parser.add_argument(
        "--gateway-url", default=os.environ.get("GATEWAY_URL", "http://gateway:8200")
    )
    parser.add_argument("--agent-url", default=os.environ.get("AGENT_URL", "http://agent:8100"))
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument(
        "--pin", default=None, help="PIN for login (if omitted, prompts via getpass)"
    )
    parser.add_argument(
        "--out", default=None, help="Optional JSON file to write sanitised event trace"
    )
    args = parser.parse_args(argv)

    pin = args.pin
    if pin is None:
        pin = getpass.getpass(f"PIN for {args.customer_id}: ")
    if not pin:
        sys.stderr.write("ERROR: PIN is required\n")
        sys.exit(EXIT_CONFIG_ERROR)

    return Config(
        gateway_url=args.gateway_url.rstrip("/"),
        agent_url=args.agent_url.rstrip("/"),
        customer_id=args.customer_id,
        text=args.text,
        out_path=args.out,
        pin=pin,
    )


def main(argv: list[str] | None = None) -> None:
    config = _parse_args(argv)

    token = os.environ.get("FRONTEND_SERVICE_TOKEN", "")
    if not token:
        sys.stderr.write("ERROR: FRONTEND_SERVICE_TOKEN is required\n")
        sys.exit(EXIT_CONFIG_ERROR)

    events: list[SanitisedEvent] = []

    with httpx.Client(timeout=httpx.Timeout(120)) as client:
        exit_code = run(config, client, events)

    # ── Write trace ───────────────────────────────────────────────────
    if config.out_path:
        trace = {
            "config": {
                "customer_id": config.customer_id,
                "text": config.text,
                "request_id_prefix": config.request_id,
            },
            "exit_code": exit_code,
            "exit_label": EXIT_LABELS.get(exit_code, "UNKNOWN"),
            "events": [],
        }
        trace["events"] = [{"kind": e.kind, "detail": e.detail} for e in events]
        with open(config.out_path, "w", encoding="utf-8") as f:
            json.dump(trace, f, indent=2, ensure_ascii=False, default=str)
        sys.stderr.write(f"Event trace written to {config.out_path}\n")

    sys.stderr.write(f"EXIT: {EXIT_LABELS.get(exit_code, 'UNKNOWN')} (code={exit_code})\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
