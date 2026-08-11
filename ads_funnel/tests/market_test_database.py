"""Safety helpers for the disposable Market Monitor test database."""

import re

TEST_DATABASE_PREFIX = "ads_funnel_market_test_"
_SAFE_DATABASE = re.compile(r"^ads_funnel_market_test_[0-9a-f]{8,32}$")


def validate_test_database_name(name: str) -> str:
    if not isinstance(name, str) or not _SAFE_DATABASE.fullmatch(name):
        raise ValueError(
            "refusing database operation: expected a disposable Market Monitor test database"
        )
    return name
