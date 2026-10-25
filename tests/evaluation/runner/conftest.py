# Reuse the established isolated temporary-PostgreSQL fixtures (tmpfs, zero skip).
from tests.gateway.db.conftest import gateway_db  # noqa: F401
from tests.obligations_data.conftest import obligations_db  # noqa: F401
