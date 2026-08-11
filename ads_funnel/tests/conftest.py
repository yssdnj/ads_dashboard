"""Physically isolate Market Monitor integration tests from operator data."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from ads_funnel.api.market_monitoring import repository, schema
from ads_funnel.api import market_monitor_db as compatibility_db
from ads_funnel.tests.market_test_database import validate_test_database_name


@pytest.fixture(scope="session", autouse=True)
def isolated_market_monitor_database():
    """Create a guarded disposable schema and route all market DB calls to it."""
    database_name = validate_test_database_name(
        f"ads_funnel_market_test_{uuid4().hex[:12]}"
    )
    config = schema._load_config()
    server_engine = create_engine(schema._server_url(config), pool_pre_ping=True)
    test_engine = None
    original_database = schema.MARKET_DB_NAME
    original_engine = schema._market_engine
    original_repository_database = repository.MARKET_DB_NAME
    original_compatibility_database = compatibility_db.MARKET_DB_NAME
    try:
        with server_engine.begin() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE `{database_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
        test_engine = create_engine(
            schema._server_url(config, database_name), pool_pre_ping=True
        )
        schema.MARKET_DB_NAME = database_name
        schema._market_engine = test_engine
        repository.MARKET_DB_NAME = database_name
        compatibility_db.MARKET_DB_NAME = database_name
        schema.init_market_db(seed_defaults=False)
        yield database_name
    finally:
        schema._market_engine = original_engine
        schema.MARKET_DB_NAME = original_database
        repository.MARKET_DB_NAME = original_repository_database
        compatibility_db.MARKET_DB_NAME = original_compatibility_database
        if test_engine is not None:
            test_engine.dispose()
        validate_test_database_name(database_name)
        with server_engine.begin() as conn:
            conn.execute(text(f"DROP DATABASE `{database_name}`"))
        server_engine.dispose()
