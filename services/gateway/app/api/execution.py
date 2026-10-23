"""G5 / G8 (W4): plan status and execution."""

from fastapi import APIRouter

from . import stub

router = APIRouter()

router.get("/plans/{plan_id}")(stub("G5 plan status lands in W4"))
router.post("/plans/{plan_id}/execute")(stub("G8 execute lands in W4"))
