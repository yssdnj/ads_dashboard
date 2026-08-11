from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ads_funnel.api.main import app
from ads_funnel.api import market_monitor_db as db
from ads_funnel.api import market_monitor_collector as collector
from ads_funnel.api.market_monitoring import repository
from ads_funnel.api.market_monitoring.constants import KEYWORD_SOURCE_VERSION, KEYWORD_WORKBOOK_PATH
from ads_funnel.api.market_monitoring.keyword_catalog import load_keyword_catalog
from ads_funnel.api import market_monitor_db as db


client = TestClient(app)
TEST_MARKET_ID = f"task6_api_{uuid4().hex}"
TEST_DATE = "2026-07-20"


@pytest.fixture(scope="module", autouse=True)
def _initialize_market_database():
    repository.init_market_db(seed_defaults=False)
    repository.seed_default_market_config()
    repository.sync_keyword_catalog(
        db.DEFAULT_MARKET_ID,
        load_keyword_catalog(KEYWORD_WORKBOOK_PATH),
        KEYWORD_SOURCE_VERSION,
    )


def _cleanup_test_market():
    db.init_market_db(seed_defaults=False)
    with db.get_market_engine().begin() as conn:
        for table in (
            "keyword_asin_competition_daily",
            "asin_keyword_snapshot_daily",
            "keyword_snapshot_daily",
            "asin_snapshot_daily",
            "market_snapshot_daily",
            "keyword_pool_config",
        ):
            conn.execute(text(f"DELETE FROM {table} WHERE market_id=:market_id"), {"market_id": TEST_MARKET_ID})


def test_market_monitor_health_and_seed_endpoints():
    response = client.get("/api/market-monitor/health")
    assert response.status_code == 200
    assert response.json()["database"] == db.MARKET_DB_NAME

    markets = client.get("/api/market-monitor/markets")
    assert markets.status_code == 200
    assert any(row["market_id"] == db.DEFAULT_MARKET_ID for row in markets.json()["markets"])

    asins = client.get("/api/market-monitor/asins")
    assert asins.status_code == 200
    assert any(row["asin"] == "B0D6G27DNH" for row in asins.json()["asins"])


def test_market_monitor_keyword_and_trend_endpoints():
    keywords = client.get("/api/market-monitor/keywords", params={"relevance_level": "high"})
    assert keywords.status_code == 200
    assert any(row["keyword"] == "slip lead" for row in keywords.json()["keywords"])

    trend = client.get("/api/market-monitor/market-trend")
    assert trend.status_code == 200
    assert isinstance(trend.json()["rows"], list)

    comparison = client.get(
        "/api/market-monitor/comparison",
        params={"market_id": db.DEFAULT_MARKET_ID, "own_asin": "B0D6G27DNH"},
    )
    assert comparison.status_code == 200
    assert comparison.json()["context"]["own_asin"] == "B0D6G27DNH"
    assert isinstance(comparison.json()["series"], list)

    snapshots = client.get("/api/market-monitor/asin-snapshots", params={"limit": 20})
    assert snapshots.status_code == 200
    assert isinstance(snapshots.json()["asins"], list)

    competition = client.get(
        "/api/market-monitor/keyword-competition",
        params={"keyword": "slip lead", "limit": 20},
    )
    assert competition.status_code == 200
    assert competition.json()["keyword"] == "slip lead"
    assert isinstance(competition.json()["asins"], list)


def test_keywords_rejects_removed_strong_tier():
    response = client.get("/api/market-monitor/keywords", params={"relevance_level": "strong"})
    assert response.status_code == 422


def test_keywords_accepts_three_active_tiers():
    for level in ("high", "mid", "low"):
        assert client.get("/api/market-monitor/keywords", params={"relevance_level": level}).status_code == 200


def test_keywords_list_excludes_active_historical_strong_config_row():
    _cleanup_test_market()
    with db.get_market_engine().begin() as conn:
        for keyword, level in (("active high", "high"), ("historical strong", "strong")):
            conn.execute(
                text(
                    """
                    INSERT INTO keyword_pool_config
                      (market_id, keyword, relevance_level, relevance_weight,
                       source, is_core_keyword, status, source_channel, source_provider)
                    VALUES
                      (:market_id, :keyword, :level, 0.925,
                       'test', 0, 'active', 'manual', 'user')
                    """
                ),
                {"market_id": TEST_MARKET_ID, "keyword": keyword, "level": level},
            )
    try:
        response = client.get("/api/market-monitor/keywords", params={"market_id": TEST_MARKET_ID})
        assert response.status_code == 200
        assert [row["keyword"] for row in response.json()["keywords"]] == ["active high"]
    finally:
        _cleanup_test_market()


def test_snapshot_apis_filter_historical_strong_rows_and_hide_legacy_fact_fields():
    _cleanup_test_market()
    with db.get_market_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO keyword_snapshot_daily
                  (date, market_id, keyword, relevance_level, relevance_weight,
                   search_volume, aba_rank, cpc, competitive_difficulty,
                   click_conversion_rate, total_market_traffic, market_traffic_share,
                   source_channel, source_provider, source_tool, source_request_id)
                VALUES
                  (:date, :market_id, 'historical strong', 'strong', 1,
                   1, 1, 1, 1, 1, 1, 1, 'test', 'test', 'test', 'test')
                """
            ),
            {"date": TEST_DATE, "market_id": TEST_MARKET_ID},
        )
        conn.execute(
            text(
                """
                INSERT INTO asin_keyword_snapshot_daily
                  (date, market_id, parent_asin, child_asin, keyword, relevance_level,
                   relevance_weight, keyword_traffic, asin_keyword_share,
                   organic_rank, ad_rank, organic_traffic, ad_traffic,
                   source_channel, source_provider, source_tool, source_request_id)
                VALUES
                  (:date, :market_id, 'PARENT', 'CHILD', 'historical strong', 'strong',
                   1, 1, 1, 1, 1, 1, 1, 'test', 'test', 'test', 'test')
                """
            ),
            {"date": TEST_DATE, "market_id": TEST_MARKET_ID},
        )
        conn.execute(
            text(
                """
                INSERT INTO keyword_asin_competition_daily
                  (date, market_id, keyword, relevance_level, relevance_weight,
                   parent_asin, child_asin, brand, title, competition_rank,
                   keyword_traffic, keyword_traffic_share, organic_rank, ad_rank,
                   organic_traffic, ad_traffic, is_seed_asin, is_own_product,
                   source_channel, source_provider, source_tool, source_request_id)
                VALUES
                  (:date, :market_id, 'historical strong', 'strong', 1,
                   'PARENT', 'CHILD', 'brand', 'title', 1,
                   1, 1, 1, 1, 1, 1, 0, 0, 'test', 'test', 'test', 'test')
                """
            ),
            {"date": TEST_DATE, "market_id": TEST_MARKET_ID},
        )
    collector.replace_market_snapshot(
        {
            "date": TEST_DATE, "market_id": TEST_MARKET_ID, "total_traffic": 1,
            "weighted_traffic": 1, "organic_traffic": 1, "ad_traffic": 0,
            "high_relevance_traffic": 1, "mid_relevance_traffic": 0,
            "low_relevance_traffic": 0, "top_asin_share": 1, "top_keyword_share": 1,
            "listing_count": 1, "asin_count": 1, "keyword_count": 1,
            "source_channel": "test", "source_provider": "test", "source_tool": "test",
            "source_request_id": "test",
        }
    )
    collector.replace_asin_snapshots(
        TEST_MARKET_ID,
        TEST_DATE,
        [
            {
                "date": TEST_DATE, "market_id": TEST_MARKET_ID, "parent_asin": "PARENT",
                "child_asin": "CHILD", "brand": "brand", "title": "title", "price": 1,
                "rating": 1, "reviews": 1, "total_traffic": 1, "organic_traffic": 1,
                "ad_traffic": 0, "weighted_traffic": 1, "high_keyword_share": 1,
                "mid_keyword_share": 0, "low_keyword_share": 0, "rank_in_market": 1,
                "is_own_product": 0, "source_channel": "test", "source_provider": "test",
                "source_tool": "test", "source_request_id": "test",
            }
        ],
    )
    try:
        assert client.get(
            "/api/market-monitor/keyword-trend",
            params={"market_id": TEST_MARKET_ID, "keyword": "historical strong"},
        ).json()["rows"] == []
        assert client.get(
            "/api/market-monitor/asin-keywords",
            params={"market_id": TEST_MARKET_ID, "child_asin": "CHILD", "date": TEST_DATE},
        ).json()["keywords"] == []
        assert client.get(
            "/api/market-monitor/keyword-competition",
            params={"market_id": TEST_MARKET_ID, "keyword": "historical strong", "date": TEST_DATE},
        ).json()["asins"] == []

        market_row = client.get(
            "/api/market-monitor/market-trend", params={"market_id": TEST_MARKET_ID}
        ).json()["rows"][0]
        asin_row = client.get(
            "/api/market-monitor/asin-snapshots", params={"market_id": TEST_MARKET_ID, "date": TEST_DATE}
        ).json()["asins"][0]
        asin_trend_row = client.get(
            "/api/market-monitor/asin-trend", params={"market_id": TEST_MARKET_ID, "child_asin": "CHILD"}
        ).json()["rows"][0]
        assert "strong_relevance_traffic" not in market_row
        assert "strong_keyword_share" not in asin_row
        assert "strong_keyword_share" not in asin_trend_row
    finally:
        _cleanup_test_market()


def test_json_ready_preserves_unrelated_nested_legacy_named_key():
    value = {"source_metadata": {"strong_keyword_share": 0.5}}
    assert repository._json_ready(value) == value


def test_comparison_rejects_more_than_three_competitors():
    response = client.get(
        "/api/market-monitor/comparison",
        params=[
            ("market_id", db.DEFAULT_MARKET_ID),
            ("own_asin", "B0D6G27DNH"),
            ("competitor_asin", "A1"),
            ("competitor_asin", "A2"),
            ("competitor_asin", "A3"),
            ("competitor_asin", "A4"),
        ],
    )
    assert response.status_code == 422


def test_comparison_accepts_four_raw_items_when_only_three_are_unique():
    response = client.get(
        "/api/market-monitor/comparison",
        params=[
            ("market_id", TEST_MARKET_ID),
            ("own_asin", "B0D6G27DNH"),
            ("competitor_asin", "a1"),
            ("competitor_asin", "A1"),
            ("competitor_asin", "A2"),
            ("competitor_asin", "A3"),
        ],
    )
    assert response.status_code == 200
    assert response.json()["context"]["competitor_asins"] == ["A1", "A2", "A3"]


def test_comparison_rejects_metric_outside_scenario():
    response = client.get(
        "/api/market-monitor/comparison",
        params={
            "market_id": db.DEFAULT_MARKET_ID,
            "own_asin": "B0D6G27DNH",
            "scenario": "market_share",
            "metric": "ad_rank",
        },
    )
    assert response.status_code == 422


def test_dashboard_and_keyword_summary_endpoints_have_stable_dtos():
    dashboard = client.get(
        "/api/market-monitor/dashboard", params={"market_id": TEST_MARKET_ID}
    )
    assert dashboard.status_code == 200
    assert set(dashboard.json()) == {
        "context",
        "data_quality",
        "kpis",
        "trend",
        "insights",
        "opportunities",
    }

    summary = client.get(
        "/api/market-monitor/keyword-summary", params={"market_id": TEST_MARKET_ID}
    )
    assert summary.status_code == 200
    assert set(summary.json()["counts"]) == {"all", "high", "mid", "low"}
    assert [row["relevance_level"] for row in summary.json()["levels"]] == [
        "high",
        "mid",
        "low",
    ]


def test_date_context_and_keyword_summary_accept_global_range(monkeypatch):
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda market_id: {"effective_data_available_through": TEST_DATE},
    )
    monkeypatch.setattr(
        repository,
        "get_available_dates",
        lambda market_id: {"max_date": "2026-07-21"},
    )
    calls = []
    monkeypatch.setattr(
        repository,
        "get_keyword_level_summary",
        lambda market_id: [],
    )
    monkeypatch.setattr(
        repository,
        "get_keyword_level_trend",
        lambda market_id, date_from=None, date_to=None: calls.append(
            (date_from, date_to)
        ) or [],
    )

    date_context = client.get(
        "/api/market-monitor/date-context", params={"market_id": TEST_MARKET_ID}
    )
    summary = client.get(
        "/api/market-monitor/keyword-summary",
        params={
            "market_id": TEST_MARKET_ID,
            "date_from": "2026-07-14",
            "date_to": TEST_DATE,
        },
    )

    assert date_context.status_code == 200
    assert date_context.json()["latest_complete_date"] == TEST_DATE
    assert date_context.json()["latest_data_date"] == "2026-07-21"
    assert summary.status_code == 200
    assert calls == [("2026-07-14", TEST_DATE)]


@pytest.mark.parametrize(
    "path,params",
    [
        ("/api/market-monitor/dashboard", {"date_to": "not-a-date"}),
        (
            "/api/market-monitor/comparison",
            {
                "market_id": TEST_MARKET_ID,
                "own_asin": "B0D6G27DNH",
                "date_from": "2026-07-21",
                "date_to": "2026-07-20",
            },
        ),
        (
            "/api/market-monitor/keyword-summary",
            {"date_from": "2026-07-21", "date_to": "2026-07-20"},
        ),
        (
            "/api/market-monitor/keywords",
            {"date_from": "2026-07-21", "date_to": "2026-07-20"},
        ),
    ],
)
def test_new_endpoints_reject_invalid_or_inverted_date_ranges(path, params):
    response = client.get(path, params=params)
    assert response.status_code == 422
