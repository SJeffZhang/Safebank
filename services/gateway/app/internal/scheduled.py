"""S1 (W5): scheduler callback, the only internal business route."""

from fastapi import APIRouter

from ..api import stub

router = APIRouter()

router.post("/internal/scheduled/authorize")(stub("S1 scheduled authorize lands in W5"))
