"""PlanView and view_hash interface (W4 implements).

W3/W4 share the wire shape (``schemas.PlanView``); the view_hash covers targets,
amounts, frequency, expiry, risk, step-by-step balances, irreversible flags and
the disclosure text version (PRD §7.10.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..schemas.planview import PlanView

if TYPE_CHECKING:  # pragma: no cover
    from ..pipeline.context import EvalContext


def build_view(ctx: EvalContext) -> tuple[PlanView, str]:
    """Build the approval panel PlanView and its ``view_hash`` from ``ctx``."""
    raise NotImplementedError("build_view is W4 (PRD §9.5 PlanView)")
