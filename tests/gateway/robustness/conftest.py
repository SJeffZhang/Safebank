"""Reuse the isolated, ephemeral PostgreSQL fixtures (gateway and real L3) unmodified."""

from tests.gateway.db.conftest import gateway_db  # noqa: F401
from tests.obligations_data.conftest import obligations_db  # noqa: F401
