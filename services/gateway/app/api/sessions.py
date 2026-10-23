"""G3 / G15 / G18–G22a (W1): sessions, utterances, actions, hints."""

from fastapi import APIRouter

from . import stub

router = APIRouter()

router.post("/sessions")(stub("G15 create session lands in W1"))
router.post("/sessions/{sid}/utterances")(stub("G3 register utterance lands in W1"))
router.post("/sessions/{sid}/actions")(stub("G19 create form action lands in W1"))
router.get("/sessions/{sid}/utterances")(stub("G20 list utterances lands in W1"))
router.get("/sessions/{sid}/clarifications/current")(stub("G21 clarification lands in W1"))
router.post("/sessions/{sid}/assistant-token")(stub("G18 assistant token lands in W1"))
router.get("/sessions/{sid}/hints/{hint_id}")(stub("G22a hint label lands in W1"))
