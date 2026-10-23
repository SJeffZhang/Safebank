"""G1 / G14 / G22 (W1): login, logout, demo customer list."""

from fastapi import APIRouter

from . import stub

router = APIRouter()

router.post("/auth/login")(stub("G1 login lands in W1"))
router.post("/auth/logout")(stub("G14 logout lands in W1"))
router.get("/auth/demo-customers")(stub("G22 demo customers lands in W1"))
