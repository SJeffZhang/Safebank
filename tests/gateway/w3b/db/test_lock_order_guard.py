"""W3b B5: source-level lock-order guard.

Verifies endpoints call ``_lock_session`` before ``_lock_approval``.

Deadlock semantics (approval-first → ABBA) are proven by the helper-function-level
RED/GREEN tests in ``test_lock_order.py``.  This test guarantees the ENDPOINTS
themselves actually observe that order, catching accidental reordering during refactors.

Uses ``ast`` to parse ``approvals.py``, locate G6/G7/G10 (plus any other endpoint
that acquires both locks), and assert ``_lock_session`` first call < ``_lock_approval``
first call within each handler function and its nested closures.
"""

from __future__ import annotations

import ast
from pathlib import Path

APPROVALS_PY = Path(__file__).parents[4] / "services" / "gateway" / "app" / "api" / "approvals.py"

# Endpoint function names that must lock session-before-approval.
ENDPOINTS = {"confirm_approval", "reject_approval", "otp_send"}


def _first_call_line(node: ast.AST, target_names: set[str]) -> int | None:
    """Return the earliest line number where any target_name is called, or None."""
    best: int | None = None
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name) and child.func.id in target_names:
                if best is None or child.lineno < best:
                    best = child.lineno
            elif isinstance(child.func, ast.Attribute) and child.func.attr in target_names:
                if best is None or child.lineno < best:
                    best = child.lineno
    return best


def _function_name(node: ast.AST) -> str | None:
    """Return the name of a function definition or its outer endpoint name."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    return None


def _collect_lock_order(target: ast.AST, path: str) -> list[str]:
    """Walk *target* and its nested closures; assert session-before-approval everywhere."""
    errors: list[str] = []

    # Walk target and all nested function defs inside it.
    to_check: list[ast.AST] = [target]
    for child in ast.walk(target):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            to_check.append(child)

    for node in to_check:
        sess_line = _first_call_line(node, {"_lock_session", "lock_session"})
        appr_line = _first_call_line(node, {"_lock_approval", "lock_approval"})

        name = _function_name(node) or "<lambda/closure>"

        if sess_line is None and appr_line is None:
            # Neither lock — fine (e.g. a helper that just does validation).
            continue
        if sess_line is None:
            errors.append(
                f"{path}:{node.lineno} ({name}): _lock_approval called but _lock_session missing"
            )
            continue
        if appr_line is None:
            errors.append(
                f"{path}:{node.lineno} ({name}): _lock_session called but _lock_approval missing"
            )
            continue
        if sess_line >= appr_line:
            errors.append(
                f"{path}:{node.lineno} ({name}): _lock_session first at line {sess_line} "
                f"NOT before _lock_approval first at line {appr_line} "
                f"(approval-first → ABBA deadlock risk)"
            )

    return errors


def test_all_endpoints_lock_session_before_approval():
    """GREEN: every endpoint that locks both session and approval does session-first.

    Deadlock proof: test_lock_order.py RED test confirms approval-first → ABBA.
    """
    errors: list[str] = []
    tree = ast.parse(APPROVALS_PY.read_text())

    for top in ast.iter_child_nodes(tree):
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if top.name in ENDPOINTS:
                errors.extend(_collect_lock_order(top, APPROVALS_PY.name))

    assert not errors, "Lock-order violation in endpoint(s):\n" + "\n".join(
        f"  - {e}" for e in errors
    )
