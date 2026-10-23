"""C1a graph errors.  C1d maps these to HTTP responses (PRD §6.1.1)."""

from __future__ import annotations


class AgentGraphError(Exception):
    """Base error for L1 graph control flow; carries a stable code."""

    code = "AGENT_ERROR"


class CrossCustomerError(AgentGraphError):
    code = "CROSS_CUSTOMER"


class SessionMismatchError(AgentGraphError):
    code = "SESSION_MISMATCH"


class MissingPortError(AgentGraphError):
    code = "MISSING_PORT"


class EmptyUtterancesError(AgentGraphError):
    code = "EMPTY_UTTERANCES"


class PortFailure(AgentGraphError):
    code = "PORT_FAILURE"
