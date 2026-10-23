"""G6 / G7 / G9 / G10 / G17 (W4): approvals, OTP, panel."""

from fastapi import APIRouter

from . import stub

router = APIRouter()

router.get("/approvals")(stub("G17 list approvals lands in W4"))
router.get("/approvals/{approval_id}")(stub("G9 approval panel lands in W4"))
router.post("/approvals/{approval_id}/confirm")(stub("G6 confirm lands in W4"))
router.post("/approvals/{approval_id}/reject")(stub("G7 reject lands in W4"))
router.post("/approvals/{approval_id}/otp")(stub("G10 otp lands in W4"))
