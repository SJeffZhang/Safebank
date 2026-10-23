"""Fake L3 (contract test double) validating requests/responses against v1.json.

Behaviors are programmable per operation: ``"ok"`` (default), ``"state_changed"``
(409 STATE_CHANGED), ``"timeout"`` (sleep past the client timeout), ``"drop"``
(raise so the transport reports a dropped connection). W1–W5 use this double
for L3 capabilities that are not delivered yet; real L3 keeps its own tests.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate as jsonschema_validate
from safebank_contracts.codes import Code

TS = "2026-10-15T09:00:00+08:00"

BEHAVIORS = ("ok", "state_changed", "timeout", "drop")


class DropConnection(BaseException):
    """Simulate a connection that drops before any response is produced."""


class FakeL3:
    def __init__(self, *, schemas_path: str | Path | None = None) -> None:
        self._schemas_path = (
            Path(schemas_path)
            if schemas_path
            else Path(__file__).resolve().parents[4] / "docs" / "contracts" / "v1.json"
        )
        self._schemas: dict[str, Any] | None = None
        self.behaviors: dict[str, str] = {}
        self.timeout_delay = 1.0

    @property
    def schemas(self) -> dict[str, Any]:
        if self._schemas is None:
            self._schemas = json.loads(self._schemas_path.read_text())
        return self._schemas

    def behavior(self, operation: str) -> str:
        return self.behaviors.get(operation, "ok")

    def _validate(self, instance: Any, schema: dict[str, Any]) -> None:
        jsonschema_validate(instance, schema)

    def _schema(self, *keys: str) -> dict[str, Any]:
        node: Any = self.schemas
        for key in keys:
            node = node[key]
        return node

    def _error(self, code: str, status: int, trace_id: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": code, "details": {}, "trace_id": trace_id}},
        )

    async def _gate(self, operation: str, trace_id: str) -> JSONResponse | None:
        behavior = self.behavior(operation)
        if behavior == "state_changed":
            return self._error(Code.STATE_CHANGED.value, 409, trace_id)
        if behavior == "timeout":
            await asyncio.sleep(self.timeout_delay)
            return None
        if behavior == "drop":
            raise DropConnection("simulated dropped L3 response")
        return None

    def app(self) -> FastAPI:
        app = FastAPI(title="SafeBank fake L3", docs_url=None, redoc_url=None, openapi_url=None)

        def request_schema(*keys: str) -> dict[str, Any]:
            return self._schema("models", *keys)

        def reject(request: Request) -> JSONResponse:
            return self._error(
                Code.CONTRACT_INVALID.value, 422, getattr(request.state, "trace_id", "")
            )

        async def body(request: Request) -> dict[str, Any]:
            return await request.json()

        @app.post("/internal/snapshot")
        async def snapshot(request: Request):
            data = await body(request)
            try:
                self._validate(data, request_schema("SnapshotRequest"))
            except SchemaValidationError:
                return reject(request)
            gated = await self._gate("snapshot", getattr(request.state, "trace_id", ""))
            if gated is not None:
                return gated
            result = self._minimal_bank_state()
            self._validate(result, self._schema("models", "BankState"))
            return result

        @app.post("/internal/read/{tool}")
        async def read(tool: str, request: Request):
            data = await body(request)
            try:
                self._validate(data, request_schema("InternalRead"))
                self._validate(data.get("args", {}), self._schema("read_tools", tool, "args"))
            except (SchemaValidationError, KeyError):
                return reject(request)
            gated = await self._gate("read", getattr(request.state, "trace_id", ""))
            if gated is not None:
                return gated
            result = self._minimal_read_result(tool)
            self._validate(result, self._schema("read_tools", tool, "result"))
            return result

        @app.post("/internal/plans/prepare")
        async def prepare(request: Request):
            data = await body(request)
            try:
                self._validate(data, request_schema("Prepare"))
            except SchemaValidationError:
                return reject(request)
            gated = await self._gate("prepare", getattr(request.state, "trace_id", ""))
            if gated is not None:
                return gated
            result = {"status": "PREPARED", "attempt": data.get("attempt", 1)}
            self._validate(result, self._schema("models", "PrepareResult"))
            return result

        @app.post("/internal/plans/execute")
        async def execute(request: Request):
            data = await body(request)
            try:
                self._validate(data, request_schema("Execute"))
            except SchemaValidationError:
                return reject(request)
            gated = await self._gate("execute", getattr(request.state, "trace_id", ""))
            if gated is not None:
                return gated
            result = self._minimal_execution_result(data.get("plan_id", "pln_x"))
            self._validate(result, self._schema("models", "ExecutionResult"))
            return result

        @app.get("/internal/executions/{plan_id}")
        async def execution(plan_id: str, request: Request):
            gated = await self._gate("execution", getattr(request.state, "trace_id", ""))
            if gated is not None:
                return gated
            result = {"status": "COMMITTED", "result": self._minimal_execution_result(plan_id)}
            self._validate(result, self._schema("models", "ExecutionStatus"))
            return result

        @app.post("/internal/executions/{plan_id}/reconcile")
        async def reconcile(plan_id: str, request: Request):
            data = await body(request)
            try:
                self._validate(data, request_schema("Reconcile"))
            except SchemaValidationError:
                return reject(request)
            gated = await self._gate("reconcile", getattr(request.state, "trace_id", ""))
            if gated is not None:
                return gated
            result = {"status": "CANCELLED", "result": None}
            self._validate(result, self._schema("models", "ExecutionStatus"))
            return result

        @app.get("/internal/outbox")
        async def outbox(request: Request):
            gated = await self._gate("outbox", getattr(request.state, "trace_id", ""))
            if gated is not None:
                return gated
            result = {"items": [], "next_cursor": None}
            self._validate(result, self._schema("models", "OutboxPage"))
            return result

        @app.post("/internal/outbox/ack")
        async def ack(request: Request):
            data = await body(request)
            try:
                self._validate(data, request_schema("Ack"))
            except SchemaValidationError:
                return reject(request)
            gated = await self._gate("ack", getattr(request.state, "trace_id", ""))
            if gated is not None:
                return gated
            result = {"acknowledged": len(data.get("event_ids", []))}
            self._validate(result, self._schema("models", "AckResult"))
            return result

        return app

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
