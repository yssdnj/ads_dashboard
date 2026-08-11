"""Database lifecycle and DDL for market monitoring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from .constants import MARKET_DB_NAME

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "db_config.json"
_market_engine: Engine | None = None
def _load_config() -> dict[str, Any]:
    with open(_CONFIG_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _server_url(config: dict[str, Any], database: str | None = None) -> str:
    db_part = f"/{database}" if database else ""
    return (
        f"mysql+pymysql://{config['user']}:{config['password']}"
        f"@{config['host']}:{config['port']}{db_part}?charset=utf8mb4"
    )


def get_market_engine() -> Engine:
    global _market_engine
    if _market_engine is None:
        config = _load_config()
        _market_engine = create_engine(
            _server_url(config, MARKET_DB_NAME),
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _market_engine


def ensure_market_database() -> None:
    config = _load_config()
    engine = create_engine(_server_url(config), pool_pre_ping=True, pool_recycle=3600)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{MARKET_DB_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    engine.dispose()


def init_market_db(seed_defaults: bool = True) -> None:
    ensure_market_database()
    engine = get_market_engine()
    with engine.begin() as conn:
        for statement in _schema_statements():
            conn.execute(text(statement))
        apply_migrations(conn)
    if seed_defaults:
        from . import repository

        repository.seed_default_market_config()


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = :schema
                  AND TABLE_NAME = :table
                  AND COLUMN_NAME = :column
                """
            ),
            {"schema": MARKET_DB_NAME, "table": table, "column": column},
        ).scalar()
    )


def apply_migrations(conn) -> None:
    """Apply additive, repeatable schema upgrades for existing market databases."""
    additions = {
        "source_version_month": "VARCHAR(7) NULL",
        "translation": "VARCHAR(512) NULL",
        "keyword_rank": "INT NULL",
        "search_volume": "DOUBLE NULL",
        "category_search_volume": "DOUBLE NULL",
        "cpc": "DOUBLE NULL",
        "cpc_range": "VARCHAR(64) NULL",
        "click_conversion_rate": "DOUBLE NULL",
        "competitive_difficulty": "DOUBLE NULL",
        "organic_scroll_rate": "DOUBLE NULL",
    }
    for column, ddl in additions.items():
        _add_column_if_missing(conn, "keyword_pool_config", column, ddl)
    _add_column_if_missing(
        conn, "asin_snapshot_daily", "image_url", "VARCHAR(2048) NULL"
    )


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    if _column_exists(conn, table, column):
        return
    try:
        conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}"))
    except DBAPIError as exc:
        if _is_duplicate_column_error(exc) and _column_exists(conn, table, column):
            return
        raise


def _is_duplicate_column_error(exc: DBAPIError) -> bool:
    error_args = getattr(exc.orig, "args", ())
    return bool(error_args and error_args[0] == 1060)


def _schema_statements() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS market_config (
          market_id VARCHAR(100) PRIMARY KEY,
          market_name VARCHAR(255) NOT NULL,
          market_display_name VARCHAR(255) NOT NULL,
          country VARCHAR(10) NOT NULL,
          timezone VARCHAR(64) NOT NULL,
          default_collect_time VARCHAR(64) NOT NULL,
          description TEXT,
          status VARCHAR(32) NOT NULL DEFAULT 'active',
          source_channel VARCHAR(32) NOT NULL DEFAULT 'manual',
          source_provider VARCHAR(64) NOT NULL DEFAULT 'user',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS seed_asin_config (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          market_id VARCHAR(100) NOT NULL,
          asin VARCHAR(32) NOT NULL,
          source_category VARCHAR(255) NOT NULL,
          source_category_path TEXT NOT NULL,
          is_seed_asin TINYINT NOT NULL DEFAULT 1,
          is_own_product TINYINT NOT NULL DEFAULT 0,
          notes TEXT,
          first_seen_date DATE NOT NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'active',
          source_channel VARCHAR(32) NOT NULL DEFAULT 'manual',
          source_provider VARCHAR(64) NOT NULL DEFAULT 'user',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_seed_market_asin (market_id, asin),
          KEY idx_seed_market_status (market_id, status)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS keyword_pool_config (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          market_id VARCHAR(100) NOT NULL,
          keyword VARCHAR(255) NOT NULL,
          relevance_level VARCHAR(32) NOT NULL,
          relevance_weight DOUBLE NOT NULL,
          source VARCHAR(255) NOT NULL,
          is_core_keyword TINYINT NOT NULL DEFAULT 0,
          status VARCHAR(32) NOT NULL DEFAULT 'active',
          source_channel VARCHAR(32) NOT NULL DEFAULT 'manual',
          source_provider VARCHAR(64) NOT NULL DEFAULT 'user',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_keyword_market_keyword (market_id, keyword),
          KEY idx_keyword_market_level (market_id, relevance_level)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS listing_variation_map (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          market_id VARCHAR(100) NOT NULL,
          parent_asin VARCHAR(32) NOT NULL,
          child_asin VARCHAR(32) NOT NULL,
          is_seed_child TINYINT NOT NULL DEFAULT 0,
          is_representative_child TINYINT NOT NULL DEFAULT 0,
          first_seen_date DATE NOT NULL,
          last_seen_date DATE NOT NULL,
          source_channel VARCHAR(32) NOT NULL,
          source_provider VARCHAR(64) NOT NULL,
          source_tool VARCHAR(128) NOT NULL,
          source_request_id VARCHAR(64),
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_listing_child (market_id, parent_asin, child_asin),
          KEY idx_listing_parent (market_id, parent_asin),
          KEY idx_listing_child (market_id, child_asin)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS market_run_log (
          run_id VARCHAR(64) PRIMARY KEY,
          market_id VARCHAR(100) NOT NULL,
          run_date DATE NOT NULL,
          target_data_date DATE,
          data_available_through DATE,
          collect_time_bj DATETIME,
          timezone_basis VARCHAR(64) NOT NULL DEFAULT 'America/New_York',
          status VARCHAR(32) NOT NULL,
          source_summary_json JSON,
          error_message TEXT,
          started_at DATETIME,
          finished_at DATETIME,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_run_market_date (market_id, run_date),
          KEY idx_run_status (status)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS raw_request_log (
          request_id VARCHAR(64) PRIMARY KEY,
          run_id VARCHAR(64) NOT NULL,
          market_id VARCHAR(100) NOT NULL,
          source_channel VARCHAR(32) NOT NULL,
          source_provider VARCHAR(64) NOT NULL,
          source_tool VARCHAR(128) NOT NULL,
          request_params_json JSON NOT NULL,
          purpose TEXT,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_raw_request_run (run_id),
          KEY idx_raw_request_source (source_channel, source_provider, source_tool)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS raw_response_log (
          response_id VARCHAR(64) PRIMARY KEY,
          request_id VARCHAR(64) NOT NULL,
          run_id VARCHAR(64) NOT NULL,
          market_id VARCHAR(100) NOT NULL,
          source_channel VARCHAR(32) NOT NULL,
          source_provider VARCHAR(64) NOT NULL,
          source_tool VARCHAR(128) NOT NULL,
          response_status VARCHAR(32) NOT NULL,
          response_json JSON,
          cost_credits DOUBLE,
          error_message TEXT,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_raw_response_request (request_id),
          KEY idx_raw_response_run (run_id),
          KEY idx_raw_response_source (source_channel, source_provider, source_tool)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS market_snapshot_daily (
          date DATE NOT NULL,
          market_id VARCHAR(100) NOT NULL,
          total_traffic DOUBLE NOT NULL DEFAULT 0,
          weighted_traffic DOUBLE NOT NULL DEFAULT 0,
          organic_traffic DOUBLE NOT NULL DEFAULT 0,
          ad_traffic DOUBLE NOT NULL DEFAULT 0,
          strong_relevance_traffic DOUBLE NOT NULL DEFAULT 0,
          high_relevance_traffic DOUBLE NOT NULL DEFAULT 0,
          mid_relevance_traffic DOUBLE NOT NULL DEFAULT 0,
          low_relevance_traffic DOUBLE NOT NULL DEFAULT 0,
          top_asin_share DOUBLE NOT NULL DEFAULT 0,
          top_keyword_share DOUBLE NOT NULL DEFAULT 0,
          listing_count INT NOT NULL DEFAULT 0,
          asin_count INT NOT NULL DEFAULT 0,
          keyword_count INT NOT NULL DEFAULT 0,
          source_channel VARCHAR(32) NOT NULL,
          source_provider VARCHAR(64) NOT NULL,
          source_tool VARCHAR(128) NOT NULL,
          source_request_id VARCHAR(64),
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (date, market_id),
          KEY idx_market_snapshot_market_date (market_id, date)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS asin_snapshot_daily (
          date DATE NOT NULL,
          market_id VARCHAR(100) NOT NULL,
          parent_asin VARCHAR(32),
          child_asin VARCHAR(32) NOT NULL,
          brand VARCHAR(128),
          title TEXT,
          image_url VARCHAR(2048) NULL,
          price DOUBLE,
          rating DOUBLE,
          reviews INT,
          total_traffic DOUBLE NOT NULL DEFAULT 0,
          organic_traffic DOUBLE NOT NULL DEFAULT 0,
          ad_traffic DOUBLE NOT NULL DEFAULT 0,
          weighted_traffic DOUBLE NOT NULL DEFAULT 0,
          strong_keyword_share DOUBLE NOT NULL DEFAULT 0,
          high_keyword_share DOUBLE NOT NULL DEFAULT 0,
          mid_keyword_share DOUBLE NOT NULL DEFAULT 0,
          low_keyword_share DOUBLE NOT NULL DEFAULT 0,
          rank_in_market INT,
          is_own_product TINYINT NOT NULL DEFAULT 0,
          source_channel VARCHAR(32) NOT NULL,
          source_provider VARCHAR(64) NOT NULL,
          source_tool VARCHAR(128) NOT NULL,
          source_request_id VARCHAR(64),
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (date, market_id, child_asin),
          KEY idx_asin_snapshot_child (market_id, child_asin, date),
          KEY idx_asin_snapshot_parent (market_id, parent_asin, date)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS keyword_snapshot_daily (
          date DATE NOT NULL,
          market_id VARCHAR(100) NOT NULL,
          keyword VARCHAR(255) NOT NULL,
          relevance_level VARCHAR(32) NOT NULL,
          relevance_weight DOUBLE NOT NULL,
          search_volume DOUBLE,
          aba_rank INT,
          cpc DOUBLE,
          competitive_difficulty DOUBLE,
          click_conversion_rate DOUBLE,
          total_market_traffic DOUBLE NOT NULL DEFAULT 0,
          market_traffic_share DOUBLE NOT NULL DEFAULT 0,
          source_channel VARCHAR(32) NOT NULL,
          source_provider VARCHAR(64) NOT NULL,
          source_tool VARCHAR(128) NOT NULL,
          source_request_id VARCHAR(64),
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (date, market_id, keyword),
          KEY idx_keyword_snapshot_level (market_id, relevance_level, date)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS asin_keyword_snapshot_daily (
          date DATE NOT NULL,
          market_id VARCHAR(100) NOT NULL,
          parent_asin VARCHAR(32),
          child_asin VARCHAR(32) NOT NULL,
          keyword VARCHAR(255) NOT NULL,
          relevance_level VARCHAR(32) NOT NULL,
          relevance_weight DOUBLE NOT NULL,
          keyword_traffic DOUBLE NOT NULL DEFAULT 0,
          asin_keyword_share DOUBLE NOT NULL DEFAULT 0,
          organic_rank INT,
          ad_rank INT,
          organic_traffic DOUBLE NOT NULL DEFAULT 0,
          ad_traffic DOUBLE NOT NULL DEFAULT 0,
          source_channel VARCHAR(32) NOT NULL,
          source_provider VARCHAR(64) NOT NULL,
          source_tool VARCHAR(128) NOT NULL,
          source_request_id VARCHAR(64),
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (date, market_id, child_asin, keyword),
          KEY idx_asin_keyword_child (market_id, child_asin, date),
          KEY idx_asin_keyword_keyword (market_id, keyword, date)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS keyword_asin_competition_daily (
          date DATE NOT NULL,
          market_id VARCHAR(100) NOT NULL,
          keyword VARCHAR(255) NOT NULL,
          relevance_level VARCHAR(32) NOT NULL,
          relevance_weight DOUBLE NOT NULL,
          parent_asin VARCHAR(32),
          child_asin VARCHAR(32) NOT NULL,
          brand VARCHAR(128),
          title TEXT,
          competition_rank INT,
          keyword_traffic DOUBLE NOT NULL DEFAULT 0,
          keyword_traffic_share DOUBLE NOT NULL DEFAULT 0,
          organic_rank INT,
          ad_rank INT,
          organic_traffic DOUBLE NOT NULL DEFAULT 0,
          ad_traffic DOUBLE NOT NULL DEFAULT 0,
          is_seed_asin TINYINT NOT NULL DEFAULT 0,
          is_own_product TINYINT NOT NULL DEFAULT 0,
          source_channel VARCHAR(32) NOT NULL,
          source_provider VARCHAR(64) NOT NULL,
          source_tool VARCHAR(128) NOT NULL,
          source_request_id VARCHAR(64),
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (date, market_id, keyword, child_asin),
          KEY idx_keyword_competition_keyword (market_id, keyword, date),
          KEY idx_keyword_competition_child (market_id, child_asin, date),
          KEY idx_keyword_competition_rank (market_id, keyword, date, competition_rank)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS daily_report_result (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          run_id VARCHAR(64) NOT NULL,
          market_id VARCHAR(100) NOT NULL,
          report_date DATE NOT NULL,
          target_data_date DATE,
          report_type VARCHAR(64) NOT NULL,
          title VARCHAR(255) NOT NULL,
          summary_md MEDIUMTEXT,
          report_md LONGTEXT,
          report_html LONGTEXT,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_report_market_date (market_id, report_date),
          KEY idx_report_run (run_id)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """,
    ]

