# Reuse the established unique-container/random-port PostgreSQL fixture.
from tests.gateway.db.conftest import gateway_db  # noqa: F401
from tests.gateway.test_auth import keys  # noqa: F401
