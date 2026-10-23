"""Execute-time re-evaluation entry (W3 implements, W4 calls).

W4 must re-run ①–⑧ on a fresh snapshot before execute (PRD §7.10.4); this is the
single entry point for that replay. It reuses ``pipeline.evaluate`` with
``mode=EXECUTE_REPLAY``; W3 documents the per-stage differences in that mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..schemas.decision import Decision

if TYPE_CHECKING:  # pragma: no cover
    from ..pipeline.context import EvalContext


async def replay(ctx: EvalContext) -> Decision:
    """Re-run the evaluation pipeline in EXECUTE_REPLAY mode and return the Decision."""
    raise NotImplementedError("replay is W3 (PRD §7.10.4-5), called by W4")
