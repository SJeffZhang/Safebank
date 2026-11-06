"""Malformed input must never crash the gateway (real tmpfs PostgreSQL).

Every public route and the internal S1 route is driven with malformed bodies,
hostile path parameters and oversized ids under each caller kind. A response
>= 500 or any exception escaping the app is a failure: bad input is the
client's fault and must be answered with a 4xx unified error body.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from services.gateway.app.main import _collect, build_internal_app
from tests.gateway.w1.db.support import ENV, create, harness, headers, login, utter

BODIES = {
    "invalid_json": b"{not json",
    "empty": b"",
    "null": b"null",
    "array": b"[1,2]",
    "string": b'"x"',
    "number": b"42",
    "empty_obj": b"{}",
    "rid_int": json.dumps({"request_id": 123}).encode(),
    "rid_null": json.dumps({"request_id": None}).encode(),
    "rid_long": json.dumps({"request_id": "r" * 20000}).encode(),
    "rid_obj": json.dumps({"request_id": {"a": 1}}).encode(),
    "rid_nul": json.dumps({"request_id": "req_\u0000x"}).encode(),
    "extra": json.dumps({"request_id": "req_x1", "zzz": 1}).encode(),
    "text_wrong": json.dumps({"request_id": "req_x2", "kind": "typed", "text": 5}).encode(),
    "text_nul": json.dumps(
        {"request_id": "req_x4", "kind": "typed", "text": "给王五\u0000"}
    ).encode(),
    "nested": ("[" * 3000 + "]" * 3000).encode(),
    "nan": b'{"request_id": NaN}',
    "bad_utf8": b'{"request_id": "\xff\xfe"}',
    "raw_nul": b'{"request_id": "a\x00b"}',
    "lone_surrogate": b'{"request_id": "\\ud800x", "text": "\\udfff"}',
}
ID_VALUES = ["xx_doesnotexist", "a" * 4000, "%00", "..%2F..", "null"]


def _status_code(response):
    try:
        return response.json().get("error", {}).get("code")
    except Exception:
        return "NONJSON"


async def _drive(client, method, path, hdr, body, failures, tag):
    hh = dict(hdr)
    if body is not None:
        hh["Content-Type"] = "application/json"
    try:
        response = await client.request(method, path, headers=hh, content=body)
    except Exception as exc:  # escaped the app: a crash
        failures.append((tag, "EXC", type(exc).__name__, str(exc)[:120]))
        return None
    if response.status_code >= 500:
        failures.append((tag, response.status_code, _status_code(response)))
    return response


def test_no_route_crashes_on_malformed_input(gateway_db, monkeypatch):
    asyncio.run(_matrix(gateway_db, monkeypatch))


async def _matrix(gateway_db, monkeypatch):
    async with harness(gateway_db, monkeypatch) as (app, client, *_rest):
        token = await login(client)
        sid = await create(client, token)
        reg = await utter(client, token, sid, "给王五转800元")
        assert reg.status_code == 201, reg.text
        a_token = reg.json()["assistant_token"]
        callers = {
            "F": headers(token),
            "A": {"X-Service-Token": "a-test", "Authorization": "Bearer " + a_token},
            "none": {},
            "garbage": {"X-Service-Token": "f-test", "Authorization": "Bearer abc.def.ghi"},
        }
        failures: list = []
        routes = [r for r in _collect(app.routes) if r.path != "/health"]
        assert len(routes) >= 30
        for route in routes:
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                if "{" in route.path:
                    base = re.sub(r"\{sid\}|\{session_id\}", sid, route.path)
                    paths = [re.sub(r"\{[^}]+\}", v, base) for v in ID_VALUES]
                    if base != route.path:
                        paths.append(route.path.replace("{sid}", "ses_%00x"))
                else:
                    paths = [route.path]
                bodies = BODIES if method == "POST" else {"none": None}
                for path in paths:
                    for cname, hdr in callers.items():
                        for bname, body in bodies.items():
                            tag = f"{method} {route.path} {cname} {bname} {path[:24]}"
                            await _drive(client, method, path, hdr, body, failures, tag)
        assert failures == [], "\n".join(map(str, failures[:40]))


def test_internal_route_rejects_malformed_input(gateway_db, monkeypatch):
    asyncio.run(_internal(gateway_db, monkeypatch))


async def _internal(gateway_db, monkeypatch):
    async with harness(gateway_db, monkeypatch) as (app, _client, _db, _priv, public, *_r):
        internal = build_internal_app(app.state.settings, env=ENV, public_key=public)
        internal.state._state.update(app.state._state)
        failures: list = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=internal), base_url="http://gateway-internal"
        ) as ic:
            hdr = {"X-Service-Token": "s-test"}
            for bname, body in BODIES.items():
                await _drive(
                    ic, "POST", "/internal/scheduled/authorize", hdr, body, failures, bname
                )
        assert failures == [], failures


def test_targeted_inputs_get_contract_invalid(gateway_db, monkeypatch):
    asyncio.run(_targeted(gateway_db, monkeypatch))


async def _targeted(gateway_db, monkeypatch):
    async with harness(gateway_db, monkeypatch) as (_app, client, *_rest):
        token = await login(client)
        sid = await create(client, token)
        reg = await utter(client, token, sid, "给王五转800元")
        assert reg.status_code == 201, reg.text
        f_hdr = {**headers(token), "Content-Type": "application/json"}
        cases = [
            ("POST", "/plans/pln_x/execute", f_hdr, b"{}"),
            ("POST", "/plans/pln_x/execute", f_hdr, b'{"request_id": 1}'),
            ("POST", "/plans/pln_x/execute", f_hdr, b"{not json"),
            ("POST", "/plans/pln_x/execute", f_hdr, b""),
            ("GET", "/plans/" + "a" * 300, f_hdr, None),
            ("POST", "/plans/pln_x/execute", f_hdr, b'{"request_id": "\xff"}'),
            ("POST", "/plans/pln_x/execute", f_hdr, json.dumps({"request_id": "r" * 300}).encode()),
            (
                "POST",
                f"/sessions/{sid}/utterances",
                f_hdr,
                json.dumps({"request_id": "req_n", "kind": "typed", "text": "a\u0000"}).encode(),
            ),
            ("GET", "/plans/pln_%00", f_hdr, None),
            (
                "POST",
                "/notifications/ntf_x/read",
                f_hdr,
                json.dumps({"request_id": "r" * 300}).encode(),
            ),
        ]
        for method, path, hdr, body in cases:
            response = await client.request(method, path, headers=hdr, content=body)
            assert response.status_code == 422, (path, body, response.status_code, response.text)
            assert response.json()["error"]["code"] == "CONTRACT_INVALID", response.text
        # A well-formed utterance still works after the hostile ones.
        ok = await utter(client, token, sid, "给王五转800元", request_id="req_after")
        assert ok.status_code == 201, ok.text
