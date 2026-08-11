from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from ads_funnel.api import market_monitor_db as db
from ads_funnel.api.market_monitoring import repository


EXPECTED_TABLES = {
    "market_config",
    "seed_asin_config",
    "keyword_pool_config",
    "listing_variation_map",
    "market_run_log",
    "raw_request_log",
    "raw_response_log",
    "market_snapshot_daily",
    "asin_snapshot_daily",
    "keyword_snapshot_daily",
    "asin_keyword_snapshot_daily",
    "keyword_asin_competition_daily",
    "daily_report_result",
}


def test_market_database_initializes_schema_and_seed_config_without_mutating_keywords():
    with db.get_market_engine().connect() as conn:
        keywords_before = conn.execute(
            text("SELECT COUNT(*) FROM keyword_pool_config WHERE market_id=:market_id"),
            {"market_id": db.DEFAULT_MARKET_ID},
        ).scalar()
    db.init_market_db(seed_defaults=True)
    engine = db.get_market_engine()

    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SHOW TABLES")).fetchall()}
        own = conn.execute(
            text(
                """
                SELECT asin, is_own_product
                FROM seed_asin_config
                WHERE market_id = :market_id AND asin = 'B0D6G27DNH'
                """
            ),
            {"market_id": db.DEFAULT_MARKET_ID},
        ).mappings().first()
        keywords_after = conn.execute(
            text("SELECT COUNT(*) FROM keyword_pool_config WHERE market_id=:market_id"),
            {"market_id": db.DEFAULT_MARKET_ID},
        ).scalar()

    assert EXPECTED_TABLES.issubset(tables)
    assert own["asin"] == "B0D6G27DNH"
    assert own["is_own_product"] == 1
    assert keywords_after == keywords_before


def test_fresh_database_initialization_seeds_market_and_asins_but_never_keywords(
    monkeypatch,
):
    statements = []

    class Result:
        def scalar(self):
            return None

    class Connection:
        def execute(self, statement, _params=None):
            statements.append(str(statement))
            return Result()

    class Transaction:
        def __enter__(self):
            return Connection()

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class Engine:
        def begin(self):
            return Transaction()

    engine = Engine()
    monkeypatch.setattr(db.schema, "ensure_market_database", lambda: None)
    monkeypatch.setattr(db.schema, "get_market_engine", lambda: engine)
    monkeypatch.setattr(db.schema, "_schema_statements", lambda: [])
    monkeypatch.setattr(db.schema, "apply_migrations", lambda _conn: None)
    monkeypatch.setattr(repository, "get_market_engine", lambda: engine)

    db.init_market_db(seed_defaults=True)

    sql = "\n".join(statements)
    assert "INSERT INTO market_config" in sql
    assert "INSERT INTO seed_asin_config" in sql
    assert "keyword_pool_config" not in sql


def test_market_database_applies_keyword_catalog_migration_idempotently():
    db.init_market_db(seed_defaults=False)
    engine = db.get_market_engine()
    expected_columns = {
        "source_version_month",
        "translation",
        "keyword_rank",
        "search_volume",
        "category_search_volume",
        "cpc",
        "cpc_range",
        "click_conversion_rate",
        "competitive_difficulty",
        "organic_scroll_rate",
    }

    with engine.begin() as conn:
        db.schema.apply_migrations(conn)
        db.schema.apply_migrations(conn)
        columns = {
            row[0]
            for row in conn.execute(text("SHOW COLUMNS FROM keyword_pool_config")).fetchall()
        }

    assert expected_columns.issubset(columns)


def test_market_database_adds_nullable_asin_image_url_idempotently():
    db.init_market_db(seed_defaults=False)
    engine = db.get_market_engine()

    with engine.begin() as conn:
        db.schema.apply_migrations(conn)
        db.schema.apply_migrations(conn)
        column = conn.execute(
            text(
                """
                SELECT IS_NULLABLE, DATA_TYPE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=:schema AND TABLE_NAME='asin_snapshot_daily'
                  AND COLUMN_NAME='image_url'
                """
            ),
            {"schema": db.MARKET_DB_NAME},
        ).mappings().one()

    assert column == {"IS_NULLABLE": "YES", "DATA_TYPE": "varchar"}


def test_migration_recovers_when_a_concurrent_worker_adds_the_column(monkeypatch):
    checks = 0

    def column_exists(_conn, _table, _column):
        nonlocal checks
        checks += 1
        return checks > 1

    class ConcurrentMigrationConnection:
        def execute(self, statement, _params=None):
            if "ALTER TABLE" in str(statement):
                raise OperationalError(
                    str(statement),
                    {},
                    Exception(1060, "Duplicate column name 'source_version_month'"),
                )
            raise AssertionError(f"unexpected SQL: {statement}")

    monkeypatch.setattr(db.schema, "_column_exists", column_exists)

    db.schema.apply_migrations(ConcurrentMigrationConnection())

    assert checks == 12


def test_market_query_helpers_return_seed_data():
    db.init_market_db(seed_defaults=True)
    markets = db.list_markets()
    asins = db.list_asins()
    keywords = db.list_keywords()
    dates = db.get_available_dates()

    assert any(row["market_id"] == db.DEFAULT_MARKET_ID for row in markets)
    assert any(row["asin"] == "B0D6G27DNH" and row["is_own_product"] == 1 for row in asins)
    assert all(row["relevance_level"] in {"high", "mid", "low"} for row in keywords)
    assert {"min_date", "max_date", "day_count"}.issubset(dates)
    assert dates["day_count"] >= 0


def test_monitoring_objects_keep_only_parent_representatives_then_traffic_fallback():
    selector = getattr(repository, "_select_monitoring_objects", None)
    assert callable(selector)
    rows = [
        {"parent_asin": "P1", "asin": "OWN", "is_own_product": 1,
         "is_representative_child": 1, "latest_total_traffic": 10},
        {"parent_asin": "P1", "asin": "REP", "is_own_product": 0,
         "is_representative_child": 1, "latest_total_traffic": 30},
        {"parent_asin": "P1", "asin": "NOISE", "is_own_product": 0,
         "is_representative_child": 0, "latest_total_traffic": 999},
        {"parent_asin": "P2", "asin": "TOP", "is_own_product": 0,
         "is_representative_child": 0, "latest_total_traffic": 40},
        {"parent_asin": "P2", "asin": "LOW", "is_own_product": 0,
         "is_representative_child": 0, "latest_total_traffic": 5},
    ]

    selected = selector(rows)

    assert [row["asin"] for row in selected] == ["OWN", "REP", "TOP"]
    assert all(row["parent_asin"] in {"P1", "P2"} for row in selected)


def test_monitoring_objects_keep_own_parent_contiguous():
    rows = [
        {"parent_asin": "P2", "asin": "OWN", "is_own_product": 1,
         "is_representative_child": 1, "latest_total_traffic": 10},
        {"parent_asin": "P2", "asin": "REP", "is_own_product": 0,
         "is_representative_child": 1, "latest_total_traffic": 30},
        {"parent_asin": "P1", "asin": "OTHER", "is_own_product": 0,
         "is_representative_child": 1, "latest_total_traffic": 50},
    ]

    selected = repository._select_monitoring_objects(rows)

    assert [(row["parent_asin"], row["asin"]) for row in selected] == [
        ("P2", "OWN"), ("P2", "REP"), ("P1", "OTHER"),
    ]


def test_monitoring_query_uses_one_canonical_complete_day_source():
    source = Path(repository.__file__).read_text(encoding="utf-8")
    function = source.split("def list_monitoring_asins", 1)[1].split(
        "def _select_monitoring_objects", 1
    )[0]

    assert "partial_success" in function
    assert "effective_complete_date" in function
    assert function.count("MAX(latest.date)") <= 1
