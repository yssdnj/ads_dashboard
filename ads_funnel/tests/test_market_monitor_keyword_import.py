from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from ads_funnel.api.market_monitoring import repository, schema
from ads_funnel.api.market_monitoring.constants import KEYWORD_WORKBOOK_PATH
from ads_funnel.api.market_monitoring.keyword_catalog import load_keyword_catalog


@pytest.fixture
def test_market_id() -> str:
    return f"task3_keyword_import_{uuid4().hex}"


@pytest.fixture(autouse=True)
def isolated_keyword_catalog_market(test_market_id):
    schema.init_market_db(seed_defaults=False)
    with schema.get_market_engine().begin() as conn:
        _clear_test_market(conn, test_market_id)
    yield
    with schema.get_market_engine().begin() as conn:
        _clear_test_market(conn, test_market_id)


def _clear_test_market(conn, market_id: str) -> None:
    for table in (
        "keyword_snapshot_daily",
        "keyword_pool_config",
        "seed_asin_config",
        "market_config",
    ):
        conn.execute(
            text(f"DELETE FROM {table} WHERE market_id = :market_id"),
            {"market_id": market_id},
        )


def test_sync_keyword_catalog_upserts_three_tiers_and_deactivates_legacy(test_market_id):
    with schema.get_market_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO keyword_pool_config
                  (market_id, keyword, relevance_level, relevance_weight, source,
                   is_core_keyword, status, source_channel, source_provider)
                VALUES
                  (:market_id, 'legacy keyword', 'high', 0.75, 'test', 0, 'active',
                   'test', 'test')
                """
            ),
            {"market_id": test_market_id},
        )

    result = repository.sync_keyword_catalog(
        test_market_id, load_keyword_catalog(KEYWORD_WORKBOOK_PATH), "2026-06"
    )

    assert result == {"active": 256, "high": 77, "mid": 40, "low": 139, "deactivated": 1}
    active = repository.list_keywords(test_market_id)
    assert len(active) == 256
    assert {row["relevance_level"] for row in active} == {"high", "mid", "low"}
    assert next(row for row in active if row["keyword"] == "slip lead")[
        "relevance_weight"
    ] == pytest.approx(0.925)


def test_catalog_sync_does_not_update_historical_snapshot_relevance(test_market_id):
    with schema.get_market_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO keyword_snapshot_daily
                  (date, market_id, keyword, relevance_level, relevance_weight,
                   source_channel, source_provider, source_tool)
                VALUES
                  ('2026-07-20', :market_id, 'slip lead', 'high', 0.42,
                   'test', 'test', 'test')
                """
            ),
            {"market_id": test_market_id},
        )

    before = repository.get_keyword_trend(
        test_market_id, "slip lead", "2026-07-20", "2026-07-20"
    )
    repository.sync_keyword_catalog(
        test_market_id, load_keyword_catalog(KEYWORD_WORKBOOK_PATH), "2026-06"
    )
    after = repository.get_keyword_trend(
        test_market_id, "slip lead", "2026-07-20", "2026-07-20"
    )

    assert after == before


def test_default_seed_does_not_reactivate_legacy_keywords_after_catalog_sync(
    monkeypatch, test_market_id
):
    monkeypatch.setattr(repository, "DEFAULT_MARKET_ID", test_market_id)
    repository.seed_default_market_config()
    repository.sync_keyword_catalog(
        test_market_id, load_keyword_catalog(KEYWORD_WORKBOOK_PATH), "2026-06"
    )

    repository.seed_default_market_config()

    with schema.get_market_engine().connect() as conn:
        active_count = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM keyword_pool_config
                WHERE market_id = :market_id AND status = 'active'
                """
            ),
            {"market_id": test_market_id},
        ).scalar()
    assert active_count == 256


def test_keyword_import_cli_dry_run_reports_catalog_counts(monkeypatch, capsys):
    script = Path(__file__).resolve().parents[1] / "scripts" / "market_monitor_import_keywords.py"
    module_spec = importlib.util.spec_from_file_location("keyword_import_cli", script)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(script), "--dry-run"])

    assert module.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "active": 256,
        "high": 77,
        "mid": 40,
        "low": 139,
    }
