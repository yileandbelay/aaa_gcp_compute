"""Run the whole suite on PostgreSQL by setting TEST_DATABASE_URL (default: in-memory SQLite, as before).

    TEST_DATABASE_URL=postgresql+psycopg2://academy@127.0.0.1:5433/academy_test pytest

Every test starts from an empty schema, so tests cannot leak data into each other.
"""
import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

if TEST_DATABASE_URL:
    from academy.config import TestConfig

    # Every test's own config class inherits from TestConfig, so this reaches all of them.
    TestConfig.SQLALCHEMY_DATABASE_URI = TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def _empty_database():
    if TEST_DATABASE_URL:
        from sqlalchemy import create_engine, text

        engine = create_engine(TEST_DATABASE_URL)
        with engine.begin() as conn:
            # Also remove any extra schemas an earlier test created (some tests build several sites).
            extra = conn.execute(text(
                "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN ('public', 'information_schema')"
            )).scalars().all()
            for name in extra:
                conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()
    yield
