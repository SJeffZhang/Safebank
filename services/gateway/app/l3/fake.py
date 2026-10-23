"""Stateful fake L3 test double (PRD §8.5, §7.10.7 recovery).

Wraps a validating ASGI app with a custom ``httpx.AsyncBaseTransport`` so that
``timeout`` / ``drop`` / ``commit_then_drop`` / ``prepare_drop`` surface as real
``httpx.ReadTimeout`` / ``httpx.RemoteProtocolError`` through :class:`L3Client`,
instead of being swallowed by ASGI transport. It keeps a per-
``(customer_id, plan_id)`` intent state machine (PREPARED / COMMITTED /
CANCELLED tombstone), validates ``X-Service-Token`` and request shapes against
``docs/contracts/v1.json``, and logs every call for assertions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate as jsonschema_validate
from safebank_contracts.codes import Code

TS = "2026-10-15T09:00:00+08:00"

PREPARED = "PREPARED"
COMMITTED = "COMMITTED"
CANCELLED = "CANCELLED"


def _op_for(method: str, path: str) -> str:
    if path.startswith("/internal/snapshot"):
        return "snapshot"
    if path.startswith("/internal/read/"):
        return "read"
    if path.endswith("/internal/plans/prepare"):
        return "prepare"
    if path.endswith("/internal/plans/execute"):
        return "execute"
    if "/internal/executions/" in path and path.endswith("/reconcile"):
        return "reconcile"
    if "/internal/executions/" in path:
        return "execution"
    if path == "/internal/outbox":
        return "outbox"
    if path == "/internal/outbox/ack":
        return "ack"
    return "other"


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, fake: FakeL3) -> None:
        self._fake = fake
        self._asgi = httpx.ASGITransport(app=fake.app())

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        op = _op_for(request.method, request.url.path)
        behavior = self._fake._resolve(op)
        self._fake._current_behavior = behavior
        if behavior == "timeout":
            raise httpx.ReadTimeout("fake L3 read timeout")
        if behavior == "drop":
            raise httpx.RemoteProtocolError("fake L3 dropped the connection")
        response = await self._asgi.handle_async_request(request)
        if self._fake._pending_drop:
            self._fake._pending_drop = False
            raise httpx.RemoteProtocolError("fake L3 dropped after processing")
        return response


class FakeL3:
    def __init__(
        self, token: str = "gateway-token", schemas_path: str | Path | None = None
    ) -> None:
        self.token = token
        self._schemas_path = (
            Path(schemas_path)
            if schemas_path
            else Path(__file__).resolve().parents[4] / "docs" / "contracts" / "v1.json"
        )
        self._schemas: dict[str, Any] | None = None
        self.behaviors: dict[str, str | list[str]] = {}
        self._counters: dict[str, int] = {}
        self._current_behavior = "ok"
        self._pending_drop = False
        self.calls: list[dict[str, Any]] = []
        self.intents: dict[tuple[str, str], dict[str, Any]] = {}
        self._snapshot: dict[str, Any] | None = None
        self._execute_result: dict[str, Any] | None = None

    # ---- programmable state -------------------------------------------------
    def set_snapshot(self, state: Any) -> None:
        self._snapshot = state.model_dump(mode="json")

    def set_result(self, result: Any) -> None:
        self._execute_result = result.model_dump(mode="json")

    def _resolve(self, op: str) -> str:
        spec = self.behaviors.get(op, "ok")
        if isinstance(spec, str):
            return spec
        index = self._counters.get(op, 0)
        self._counters[op] = index + 1
        if not spec:
            return "ok"
        return spec[min(index, len(spec) - 1)]

    # ---- schema helpers -----------------------------------------------------
    @property
    def schemas(self) -> dict[str, Any]:
        if self._schemas is None:
            self._schemas = json.loads(self._schemas_path.read_text())
        return self._schemas

    def _validate(self, instance: Any, schema: dict[str, Any]) -> None:
        jsonschema_validate(instance, schema)

    def _model(self, name: str) -> dict[str, Any]:
        return self.schemas["models"][name]

    def _error(self, code: str, status: int, details: Any = None) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": code,
                    "details": details if details is not None else {},
                    "trace_id": "fake-l3",
                }
            },
        )

    def transport(self) -> httpx.AsyncBaseTransport:
        return _FakeTransport(self)

    # ---- app ---------------------------------------------------------------
    def app(self) -> FastAPI:
        app = FastAPI(title="SafeBank fake L3", docs_url=None, redoc_url=None, openapi_url=None)

        async def _require_token(request: Request) -> bool:
            if request.headers.get("X-Service-Token") != self.token:
                return False
            return True

        async def _gate(request: Request, op: str) -> JSONResponse | None:
            behavior = self._current_behavior
            if behavior == "unavailable_503":
                return self._error(Code.DEPENDENCY_UNAVAILABLE.value, 503)
            if behavior == "state_changed" and op == "execute":
                return self._error(
                    Code.STATE_CHANGED.value,
                    409,
                    {"decision": {"decision": "REQUIRE_APPROVAL"}},
                )
            return None

        def _reject(request: Request) -> JSONResponse:
            return self._error(Code.CONTRACT_INVALID.value, 422)

        async def _body(request: Request) -> dict[str, Any]:
            return await request.json()

        @app.post("/internal/snapshot")
        async def snapshot(request: Request):
            self.calls.append({"op": "snapshot"})
            if not await _require_token(request):
                return self._error(Code.UNAUTHENTICATED.value, 401)
            data = await _body(request)
            try:
                self._validate(data, self._model("SnapshotRequest"))
            except SchemaValidationError:
                return _reject(request)
            gated = await _gate(request, "snapshot")
            if gated is not None:
                return gated
            result = self._snapshot or self._minimal_bank_state()
            self._validate(result, self._model("BankState"))
            return result

        @app.post("/internal/read/{tool}")
        async def read(tool: str, request: Request):
            self.calls.append({"op": "read", "tool": tool})
            if not await _require_token(request):
                return self._error(Code.UNAUTHENTICATED.value, 401)
            data = await _body(request)
            try:
                self._validate(data, self._model("InternalRead"))
                self._validate(data.get("args", {}), self.schemas["read_tools"][tool]["args"])
            except (SchemaValidationError, KeyError):
                return _reject(request)
            gated = await _gate(request, "read")
            if gated is not None:
                return gated
            result = self._minimal_read_result(tool)
            self._validate(result, self.schemas["read_tools"][tool]["result"])
            return result

        @app.post("/internal/plans/prepare")
        async def prepare(request: Request):
            self.calls.append({"op": "prepare"})
            if not await _require_token(request):
                return self._error(Code.UNAUTHENTICATED.value, 401)
            data = await _body(request)
            try:
                self._validate(data, self._model("Prepare"))
            except SchemaValidationError:
                return _reject(request)
            gated = await _gate(request, "prepare")
            if gated is not None:
                return gated
            key = (data["customer_id"], data["plan_id"])
            existing = self.intents.get(key)
            if existing is not None and existing["status"] == CANCELLED:
                return self._error(Code.EXECUTION_CANCELLED.value, 409)
            if existing is not None and existing["plan_hash"] != data["plan_hash"]:
                return self._error(Code.IDEMPOTENCY_CONFLICT.value, 409)
            if existing is not None:
                return {"status": PREPARED, "attempt": existing["attempt"]}
            self.intents[key] = {
                "status": PREPARED,
                "result": None,
                "plan_hash": data["plan_hash"],
                "attempt": data.get("attempt", 1),
            }
            if self._current_behavior == "prepare_drop":
                self._pending_drop = True
            return {"status": PREPARED, "attempt": data.get("attempt", 1)}

        @app.post("/internal/plans/execute")
        async def execute(request: Request):
            self.calls.append({"op": "execute"})
            if not await _require_token(request):
                return self._error(Code.UNAUTHENTICATED.value, 401)
            data = await _body(request)
            try:
                self._validate(data, self._model("Execute"))
            except SchemaValidationError:
                return _reject(request)
            gated = await _gate(request, "execute")
            if gated is not None:
                return gated
            key = (data["customer_id"], data["plan_id"])
            existing = self.intents.get(key)
            if existing is None:
                return self._error(Code.STATE_CHANGED.value, 409)
            if existing["status"] == CANCELLED:
                return self._error(Code.EXECUTION_CANCELLED.value, 409)
            if existing["plan_hash"] != data["plan_hash"]:
                return self._error(Code.IDEMPOTENCY_CONFLICT.value, 409)
            if existing["status"] == COMMITTED:
                result = dict(existing["result"])
                result["replayed"] = True
                return result
            result = self._execute_result or self._minimal_execution_result(data["plan_id"])
            if self._current_behavior == "business_fail":
                # §7.10.6: a business failure rolls back the ledger; the intent
                # stays PREPARED so a retry can still execute.
                result = self._failed_result(data["plan_id"])
                self.intents[key] = {**existing, "result": None}
            else:
                self.intents[key] = {**existing, "status": COMMITTED, "result": result}
            if self._current_behavior == "commit_then_drop":
                self._pending_drop = True
            return result

        @app.get("/internal/executions/{plan_id}")
        async def execution(plan_id: str, request: Request):
            self.calls.append({"op": "execution", "plan_id": plan_id})
            if not await _require_token(request):
                return self._error(Code.UNAUTHENTICATED.value, 401)
            customer_id = request.query_params.get("customer_id")
            if not customer_id:
                return self._error(Code.CONTRACT_INVALID.value, 422)
            gated = await _gate(request, "execution")
            if gated is not None:
                return gated
            intent = self.intents.get((customer_id, plan_id))
            if intent is None:
                return {"status": PREPARED, "result": None}
            return {"status": intent["status"], "result": intent["result"]}

        @app.post("/internal/executions/{plan_id}/reconcile")
        async def reconcile(plan_id: str, request: Request):
            self.calls.append({"op": "reconcile", "plan_id": plan_id})
            if not await _require_token(request):
                return self._error(Code.UNAUTHENTICATED.value, 401)
            data = await _body(request)
            try:
                self._validate(data, self._model("Reconcile"))
            except SchemaValidationError:
                return _reject(request)
            gated = await _gate(request, "reconcile")
            if gated is not None:
                return gated
            key = (data["customer_id"], plan_id)
            intent = self.intents.get(key)
            if intent is not None and intent["status"] == COMMITTED:
                return {"status": COMMITTED, "result": intent["result"]}
            self.intents[key] = {"status": CANCELLED, "result": None}
            return {"status": CANCELLED, "result": None}

        @app.get("/internal/outbox")
        async def outbox(request: Request):
            self.calls.append({"op": "outbox"})
            if not await _require_token(request):
                return self._error(Code.UNAUTHENTICATED.value, 401)
            return {"items": [], "next_cursor": None}

        @app.post("/internal/outbox/ack")
        async def ack(request: Request):
            self.calls.append({"op": "ack"})
            if not await _require_token(request):
                return self._error(Code.UNAUTHENTICATED.value, 401)
            data = await _body(request)
            try:
                self._validate(data, self._model("Ack"))
            except SchemaValidationError:
                return _reject(request)
            return {"acknowledged": len(data.get("event_ids", []))}

        return app

    # ---- canned results -----------------------------------------------------
    @staticmethod
    def _minimal_bank_state() -> dict[str, Any]:
        return {
            "sim_now": TS,
            "clock_version": 0,
            "versions": {},
            "accounts": {},
            "customers": {},
            "cards": {},
            "payees": {},
        }

    @staticmethod
    def _minimal_execution_result(plan_id: str) -> dict[str, Any]:
        return {
            "plan_id": plan_id,
            "status": "EXECUTED",
            "steps": [],
            "versions": {},
            "committed_at": TS,
            "error_code": None,
            "replayed": False,
        }

    @staticmethod
    def _failed_result(plan_id: str) -> dict[str, Any]:
        return {
            "plan_id": plan_id,
            "status": "FAILED",
            "steps": [],
            "versions": {},
            "committed_at": TS,
            "error_code": Code.INSUFFICIENT_FUNDS.value,
            "replayed": False,
        }

    @staticmethod
    def _minimal_read_result(tool: str) -> dict[str, Any]:
        base = {
            "get_accounts": {
                "accounts": [],
                "daily_outflow_cents": 0,
                "daily_outflow_limit_cents": 0,
                "remaining_daily_outflow_cents": 0,
                "balance_history": [],
            },
            "list_payees": {"items": [], "next_cursor": None},
            "get_transactions": {
                "items": [],
                "next_cursor": None,
                "income_cents": 0,
                "expense_cents": 0,
                "totals_scope": "filtered_all",
            },
            "get_spending_summary": {
                "month": "2026-10",
                "total_cents": 0,
                "previous_total_cents": 0,
                "mom_change_cents": 0,
                "mom_change_bp": None,
                "categories": [],
                "daily": [],
                "transfer_out_cents": 0,
            },
            "detect_anomalies": {"items": []},
            "get_cards": {"items": []},
            "list_scheduled": {"items": [], "next_cursor": None},
            "get_split_status": {"items": [], "next_cursor": None},
            "detect_subscriptions": {"items": []},
            "list_subscriptions": {
                "items": [],
                "next_cursor": None,
                "total_monthly_cents": 0,
                "total_annual_cents": 0,
                "normalization_basis": {
                    "weekly_to_annual_factor": 52,
                    "monthly_to_annual_factor": 12,
                    "annual_to_monthly": "floor_div_12",
                },
            },
            "upcoming_charges": {
                "items": [],
                "scheduled_items": [],
                "charges_total_cents": 0,
                "scheduled_transfers_total_cents": 0,
                "reserve_total_cents": 0,
                "by_account": [],
            },
            "get_risk_questionnaire": {
                "questionnaire_id": "rq_v1",
                "questions": [],
                "validity_months": 12,
            },
            "get_risk_profile": {
                "assessment_id": None,
                "level": None,
                "assessed_at": None,
                "valid_until": None,
                "is_valid": False,
            },
            "list_products": {"items": [], "next_cursor": None},
            "compare_products": {"items": [], "comparison_fields": []},
            "get_holdings": {"holdings": [], "orders": []},
            "get_product": {
                "id": "prd_x",
                "name": "合成产品",
                "product_type": "MMF",
                "risk_level": 1,
                "risk_label": "R1",
                "nav": "1.0000",
                "nav_date": "2026-10-15",
                "recent_return_bp": 0,
                "confirm_days": 0,
                "settle_days": 0,
                "min_amount_cents": 1,
                "risk_disclosure": "合成历史表现，非保本承诺",
                "disclosure_version": "v1",
                "nav_history": [],
            },
            "get_profile": {
                "customer_id": "cus_x",
                "display_name": "合成客户",
                "phone_masked": "138****0000",
                "risk_profile": {"level": None, "is_valid": False, "valid_until": None},
                "sim_now": TS,
            },
        }
        if tool not in base:
            raise KeyError(f"no minimal read result for {tool}")
        return base[tool]
