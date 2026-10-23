"""G4 (W3): plan evaluation."""

from fastapi import APIRouter

from . import stub

router = APIRouter()

router.post("/plans/evaluate")(stub("G4 evaluate lands in W3"))
