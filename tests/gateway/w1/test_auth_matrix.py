"""Every frozen route × credential class; ownership is separately exercised on PG."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.requests import Request

from services.gateway.app.auth.deps import resolve_caller
from services.gateway.app.auth.matrix import MATRIX
from services.gateway.app.auth.tokens import sign_jwt
from services.gateway.app.errors import GatewayError

ENV = {
    "FRONTEND_SERVICE_TOKEN": "f",
    "AGENT_SERVICE_TOKEN": "a",
    "SCHEDULER_SERVICE_TOKEN": "s",
    "DEMO_CONSOLE_TOKEN": "d",
}
DUAL = {
    "/sessions/{sid}/utterances",
    "/sessions/{sid}/clarifications/current",
    "/tools/read/{tool}",
    "/plans/evaluate",
    "/plans/{plan_id}",
    "/plans/{plan_id}/execute",
}
APPROVE = {
    "/approvals/{approval_id}",
    "/approvals/{approval_id}/confirm",
    "/approvals/{approval_id}/reject",
    "/approvals/{approval_id}/otp",
}


@pytest.fixture(scope="module")
def keypair():
    key = Ed25519PrivateKey.generate()
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


CASES = [
    "none",
    "F",
    "A",
    "S",
    "D",
    "jwt_only",
    "F_app",
    "F_app_only",
    "F_approve_only",
    "A_assistant",
    "A_app",
    "F_assistant",
    "D_console",
    "A_no_sid",
    "A_bad_scope",
    "F_bad_scope",
]


def expectation(method, path, case):
    if path == "/health":
        return 200
    if path.startswith("/internal/"):
        return 200 if case == "S" else 401
    if path in ("/auth/login", "/auth/demo-customers"):
        return 200 if case.startswith("F") else 401
    if path == "/demo/auth/login":
        return 200 if case in ("D", "D_console") else 401
    if path.startswith("/demo/"):
        return 200 if case == "D_console" else 401
    if path in APPROVE:
        if case in ("F_app", "F_approve_only"):
            return 200
        return 403 if case in ("F_app_only", "F_bad_scope") else 401
    # Only the GET utterance endpoint allows A, POST is F-only.
    dual = path in DUAL and not (path == "/sessions/{sid}/utterances" and method == "POST")
    if case in ("F_app", "F_app_only") or (dual and case == "A_assistant"):
        return 200
    return 403 if case in ("F_approve_only", "F_bad_scope") else 401


@pytest.mark.parametrize("method,path", MATRIX)
@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("customer", ["cus_zs", "cus_other"])
def test_every_route_credential_and_customer(method, path, case, customer, keypair):
    private, public = keypair
    service = case.split("_", 1)[0]
    headers = []
    if service in ("F", "A", "S", "D"):
        headers.append((b"x-service-token", service.lower().encode()))
    if "_" in case or case == "jwt_only":
        aud, scopes, sid = "gateway-app", ["app", "approve"], None
        if case in ("F_assistant", "A_assistant", "A_no_sid", "A_bad_scope"):
            aud, scopes, sid = "safebank-assistant", ["assistant"], "ses_test"
        if case == "A_no_sid":
            sid = None
        if case == "F_app_only":
            scopes = ["app"]
        if case == "F_approve_only":
            scopes = ["approve"]
        if case in ("F_bad_scope", "A_bad_scope"):
            scopes = ["other"]
        if case == "D_console":
            aud, scopes = "demo-console", ["console"]
        token = sign_jwt(private, sub=customer, aud=aud, scope=scopes, sid=sid, utts=["utt_test"])
        headers.append((b"authorization", ("Bearer " + token).encode()))
    real_path = path
    for name in (
        "sid",
        "tool",
        "plan_id",
        "approval_id",
        "notification_id",
        "job_id",
        "case_id",
        "hint_id",
    ):
        real_path = real_path.replace("{" + name + "}", "target")
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": real_path,
            "headers": headers,
            "query_string": b"",
        }
    )
    try:
        caller = resolve_caller(request, env=ENV, public_key=public)
        actual = 200
        if caller is not None and caller.customer_id is not None:
            # A customer in a path/body cannot replace the signed subject.
            assert caller.customer_id == customer
    except GatewayError as exc:
        actual = exc.status
    assert actual == expectation(method, path, case), (method, path, case, actual)
