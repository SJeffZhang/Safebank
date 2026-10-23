import os

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "/integration/" in str(item.path) and not os.getenv("P1_TEST_DATABASE_URL"):
            item.add_marker(
                pytest.mark.skip(reason="requires isolated real PostgreSQL; run make test-p1-db")
            )


def test_url(admin=False):
    url = os.environ["BANK_MIGRATION_DATABASE_URL" if admin else "P1_TEST_DATABASE_URL"]
    if not url.endswith("/bank_p1_test"):
        raise RuntimeError("Destructive tests must target bank_p1_test exclusively")
    return url
