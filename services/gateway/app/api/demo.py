"""D0–D12 (W5): demo / evaluation console, mounted only in DEMO/EVAL mode."""

from fastapi import APIRouter

from . import stub

router = APIRouter()

router.post("/demo/auth/login")(stub("D0 console login lands in W5"))
router.post("/demo/reset")(stub("D1 reset lands in W5"))
router.post("/demo/scene/prepare")(stub("D2 scene prepare lands in W5"))
router.post("/demo/clock/advance")(stub("D3 clock advance lands in W5"))
router.get("/demo/clock/advances/{job_id}")(stub("D3 clock advance status lands in W5"))
router.post("/demo/events")(stub("D4 inject event lands in W5"))
router.get("/demo/sms")(stub("D5 sms lands in W5"))
router.post("/demo/attack/cases/{case_id}/start")(stub("D6a attack start lands in W5"))
router.post("/demo/attack/submit_plan")(stub("D6 attack submit lands in W5"))
router.post("/demo/attack/plans/{plan_id}/execute")(stub("D6 attack execute lands in W5"))
router.get("/demo/attack/plans/{plan_id}")(stub("D6 attack plan lands in W5"))
router.post("/demo/eval/assert")(stub("D7 eval assert lands in W5"))
router.get("/demo/plans/{plan_id}/comparison")(stub("D8 comparison lands in W5"))
router.get("/demo/reports/latest")(stub("D9 reports lands in W5"))
router.post("/demo/auth/logout")(stub("D10 console logout lands in W5"))
router.get("/demo/plans")(stub("D11 plans list lands in W5"))
router.get("/demo/audit")(stub("D12 demo audit lands in W5"))
