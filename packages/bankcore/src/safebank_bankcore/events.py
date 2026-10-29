"""Deterministic system-event execution (PRD §7.11, §8.6).

Pure functions executed by the L3 scheduler for due standing instructions and
merchant billings. No IO, database, clock or authorization: each returns a new
:class:`BankState` copy plus effects, exactly like the other bankcore appliers.
"""

from __future__ import annotations


def apply_scheduled_transfer(state, *, instruction_id, due_at, event_id=None, period_key=None):
    raise NotImplementedError


def apply_merchant_charge(state, *, billing_id, due_at, event_id=None, period_key=None):
    raise NotImplementedError
