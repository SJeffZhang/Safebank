"""Cross-session workflow analysis (PRD §7.8).

The sequence matcher lives in :mod:`.sequence`; it is pure and only reasons
over committed operation history plus the current plan's proposed operations.
The workflow stage (``pipeline.workflow``) performs the database reads/writes
and translates matcher results into findings.
"""
