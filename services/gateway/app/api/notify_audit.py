"""G11 / G12 / G13 / G16 (W5): audit and notification read endpoints."""

from fastapi import APIRouter

from . import stub

router = APIRouter()

router.get("/plans/{plan_id}/audit")(stub("G11 plan audit lands in W5"))
router.get("/notifications")(stub("G12 notifications lands in W5"))
router.post("/notifications/{notification_id}/read")(stub("G13 read notification lands in W5"))
router.get("/audit")(stub("G16 customer audit lands in W5"))
