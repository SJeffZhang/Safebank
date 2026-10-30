"""Re-export the W4b2b real-PG app fixture for the W5 notify/audit tests."""

from tests.gateway.db.conftest import gateway_db  # noqa: F401
from tests.gateway.w4b2b.db.conftest import app_config  # noqa: F401
