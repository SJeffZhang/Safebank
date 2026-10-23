"""G2 (W2): read proxy over L3."""

from fastapi import APIRouter

from . import stub

router = APIRouter()

router.post("/tools/read/{tool}")(stub("G2 read proxy lands in W2"))
