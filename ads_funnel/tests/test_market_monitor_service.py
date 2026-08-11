import json
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from ads_funnel.api.market_monitoring import repository, service
from ads_funnel.api.market_monitoring.constants import DEFAULT_MARKET_ID


OWN_ASIN = "B0D6G27DNH"
TEST_MARKET_ID = f"task6_{uuid4().hex}"
TEST_RUN_ID = uuid4().hex
TEST_DATE = "2026-07-20"


def _market_rows():
    return [
        {
            "date": "2026-07-18",
            "total_traffic": 1000,
            "top_asin_share": 0.40,
            "source_request_id": "market-18",
        },
        {
            "date": "2026-07-20",
            "total_traffic": 1200,
            "top_asin_share": 0.35,
            "source_request_id": "market-20",
        },
    ]


def _asin_rows():
    return [
        {
            "date": "2026-07-18",
            "child_asin": OWN_ASIN,
            "total_traffic": 100,
            "weighted_traffic": 90,
            "organic_traffic": 70,
            "ad_traffic": 30,
            "high_keyword_share": 0.20,
            "rank_in_market": 5,
            "source_request_id": "own-18",
        },
        {
            "date": "2026-07-20",
            "child_asin": OWN_ASIN,
            "total_traffic": 180,
            "weighted_traffic": 150,
            "organic_traffic": 100,
            "ad_traffic": 80,
            "high_keyword_share": 0.25,
            "rank_in_market": 3,
            "source_request_id": "own-20",
        },
    ]


def _patch_dashboard_repository(monkeypatch):
    monkeypatch.setattr(repository, "get_market_trend", lambda *args: _market_rows())
    monkeypatch.setattr(
        repository,
        "list_asins",
        lambda market_id: [{"asin": OWN_ASIN, "is_own_product": 1}],
    )
    monkeypatch.setattr(repository, "get_asin_trends", lambda *args: _asin_rows())
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda *args: {
            "run_id": "run-20",
            "status": "success",
            "collect_time_bj": "2026-07-21T17:00:00",
            "target_data_date": "2026-07-20",
            "data_available_through": "2026-07-20",
            "source_summary_json": json.dumps(
                {
                    "source_provider": "xydc",
                    "completeness_score": 1,
                    "provider_coverage": 1,
                    "missing_data_types": [],
                }
            ),
            "error_message": None,
        },
    )
    monkeypatch.setattr(
        repository,
        "get_keyword_level_summary",
        lambda market_id, *args: [
            {
                "relevance_level": "high",
                "keyword_count": 77,
                "search_volume": 1000,
                "category_search_volume": 800,
                "average_relevance_weight": 0.8,
            }
        ],
    )


def test_dashboard_payload_has_stable_sections(monkeypatch):
    _patch_dashboard_repository(monkeypatch)

    payload = service.get_dashboard(DEFAULT_MARKET_ID, date_to=TEST_DATE)

    assert set(payload) == {
        "context",
        "data_quality",
        "kpis",
        "trend",
        "insights",
        "opportunities",
    }
    assert payload["context"]["baselines"] == [
        "previous_complete_day",
        "latest_7_complete_days",
    ]
    assert payload["context"]["latest_data_date"] == TEST_DATE
    assert payload["data_quality"]["run_id"] == "run-20"
    assert all("baseline_sample_size" in item for item in payload["kpis"])
    assert all("evidence_ids" in item for item in payload["kpis"])
    assert "<" not in json.dumps(payload)


def test_dashboard_uses_explicit_nulls_for_unavailable_baselines(monkeypatch):
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(repository, "get_market_trend", lambda *args: _market_rows()[-1:])
    monkeypatch.setattr(repository, "get_asin_trends", lambda *args: _asin_rows()[-1:])

    payload = service.get_dashboard(DEFAULT_MARKET_ID, date_to=TEST_DATE)
    total_traffic = next(item for item in payload["kpis"] if item["id"] == "market_total_traffic")

    assert total_traffic["previous_complete_day"]["value"] is None
    assert total_traffic["previous_complete_day"]["change"] is None
    assert total_traffic["latest_7_complete_days"]["average"] is None
    assert total_traffic["latest_7_complete_days"]["deviation"] is None
    assert total_traffic["baseline_sample_size"] == 0


def test_comparison_declares_metric_availability(monkeypatch):
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(repository, "get_gap_contributors", lambda *args: [])

    payload = service.get_comparison(
        DEFAULT_MARKET_ID, OWN_ASIN, [], "keyword_competition", "keyword_traffic"
    )

    assert payload["context"]["metric_available"] is False
    assert payload["context"]["metric_availability"] == {
        "keyword_traffic": False,
        "organic_rank": False,
        "keyword_traffic_share": False,
    }
    assert payload["context"]["unavailable_reason"]


def test_comparison_metric_availability_ignores_incomplete_rows(monkeypatch):
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(
        repository,
        "get_asin_trends",
        lambda *args: [
            {
                "date": TEST_DATE,
                "child_asin": OWN_ASIN,
                "ad_traffic": 42,
                "source_request_id": "incomplete-ad-traffic",
            }
        ],
    )
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda *args: {
            "status": "partial_success",
            "target_data_date": TEST_DATE,
            "data_available_through": "2026-07-19",
            "effective_data_available_through": "2026-07-19",
        },
    )
    monkeypatch.setattr(repository, "get_gap_contributors", lambda *args: [])

    payload = service.get_comparison(
        DEFAULT_MARKET_ID, OWN_ASIN, [], "growth_quality", "ad_traffic"
    )

    assert payload["series"][0]["points"][0]["is_complete"] is False
    assert payload["context"]["metric_availability"]["ad_traffic"] is False
    assert payload["context"]["metric_available"] is False


def test_dashboard_insights_include_complete_evidence_contract(monkeypatch):
    _patch_dashboard_repository(monkeypatch)

    payload = service.get_dashboard(DEFAULT_MARKET_ID, date_to=TEST_DATE)
    insight = payload["insights"][0]

    assert {
        "severity", "metric", "current", "previous_complete_day",
        "latest_7_complete_days", "persistence_days", "direction",
        "contributors", "provider_coverage", "evidence_ids",
    }.issubset(insight)


def test_dashboard_emits_persistent_evidence_backed_risk_and_opportunity(monkeypatch):
    dates = ["2026-07-17", "2026-07-18", "2026-07-19", TEST_DATE]
    market_rows = [
        {
            "date": date,
            "total_traffic": traffic,
            "top_asin_share": pressure,
            "source_request_id": f"market-{date}",
        }
        for date, traffic, pressure in zip(
            dates, [1600, 1350, 1100, 800], [0.25, 0.29, 0.34, 0.41]
        )
    ]
    own_rows = [
        {
            "date": date,
            "child_asin": OWN_ASIN,
            "total_traffic": traffic,
            "high_keyword_share": visibility,
            "source_request_id": f"own-{date}",
        }
        for date, traffic, visibility in zip(
            dates, [160, 170, 180, 190], [0.15, 0.18, 0.22, 0.28]
        )
    ]
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(repository, "get_market_trend", lambda *args: market_rows)
    monkeypatch.setattr(repository, "get_asin_trends", lambda *args: own_rows)

    payload = service.get_dashboard(DEFAULT_MARKET_ID, date_to=TEST_DATE)

    risk = next(row for row in payload["insights"] if row["severity"] == "risk")
    opportunity = payload["opportunities"][0]
    assert risk["scope"] == "market_total_traffic"
    assert risk["anomaly_score"] >= 70
    assert risk["fact"] and risk["inference"] and risk["recommendation"]
    assert risk["evidence"] and risk["evidence_ids"]
    assert risk["confidence"] >= 0.6
    assert opportunity["severity"] == "opportunity"
    assert opportunity["priority_score"] is not None
    assert opportunity["fact"] and opportunity["inference"] and opportunity["recommendation"]
    assert payload["trend"]["anomaly_markers"][0]["insight_id"] == risk["insight_id"]


def test_dashboard_intelligence_fails_closed_when_quality_is_insufficient(monkeypatch):
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(
        repository,
        "get_market_trend",
        lambda *args: [
            {"date": "2026-07-17", "total_traffic": 1600, "top_asin_share": .2, "source_request_id": "a"},
            {"date": "2026-07-18", "total_traffic": 1300, "top_asin_share": .3, "source_request_id": "b"},
            {"date": "2026-07-19", "total_traffic": 1000, "top_asin_share": .4, "source_request_id": "c"},
            {"date": TEST_DATE, "total_traffic": 700, "top_asin_share": .5, "source_request_id": "d"},
        ],
    )
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda *args: {
            "run_id": "partial",
            "status": "partial_success",
            "target_data_date": TEST_DATE,
            "data_available_through": TEST_DATE,
            "source_summary_json": json.dumps(
                {"source_provider": "xydc", "completeness_score": .4, "provider_coverage": .4}
            ),
        },
    )

    payload = service.get_dashboard(DEFAULT_MARKET_ID, date_to=TEST_DATE)

    assert not [row for row in payload["insights"] if row["severity"] == "risk"]
    assert payload["opportunities"] == []
    assert payload["trend"]["anomaly_markers"] == []
    assert all(row["inference"] is None and row["recommendation"] is None for row in payload["insights"])


def test_dashboard_does_not_treat_rows_after_available_date_as_market_decline(monkeypatch):
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(
        repository,
        "get_market_trend",
        lambda *args: [
            {
                "date": "2026-07-19",
                "total_traffic": 1000,
                "top_asin_share": 0.4,
                "source_request_id": "complete-19",
            },
            {
                "date": TEST_DATE,
                "total_traffic": 0,
                "top_asin_share": 0,
                "source_request_id": "incomplete-20",
            },
        ],
    )
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda *args: {
            "run_id": "partial-20",
            "status": "partial_success",
            "target_data_date": TEST_DATE,
            "data_available_through": "2026-07-19",
            "source_summary_json": json.dumps(
                {"source_provider": "xydc", "completeness_score": 0.5}
            ),
        },
    )

    payload = service.get_dashboard(DEFAULT_MARKET_ID, date_to=TEST_DATE)
    total_traffic = next(item for item in payload["kpis"] if item["id"] == "market_total_traffic")

    assert payload["context"]["latest_data_date"] == "2026-07-19"
    assert payload["data_quality"]["is_stale"] is True
    assert total_traffic["current"] == 1000
    assert total_traffic["evidence_ids"] == ["complete-19"]


@pytest.mark.parametrize("reported_available_through", [None, TEST_DATE])
def test_dashboard_failed_run_without_effective_availability_fails_closed(
    monkeypatch, reported_available_through
):
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda *args: {
            "run_id": "failed-20",
            "status": "failed",
            "target_data_date": TEST_DATE,
            "data_available_through": reported_available_through,
            "effective_data_available_through": None,
            "source_summary_json": None,
            "error_message": "provider unavailable",
        },
    )

    payload = service.get_dashboard(DEFAULT_MARKET_ID, date_to=TEST_DATE)

    assert payload["context"]["latest_data_date"] is None
    assert payload["data_quality"]["is_stale"] is True
    assert all(item["current"] is None for item in payload["kpis"])
    assert all(
        point["is_complete"] is False
        for series in payload["trend"]["series"]
        for point in series["points"]
    )


def test_comparison_batches_asins_and_validates_scenario_metric(monkeypatch):
    calls = []

    def get_asin_trends(market_id, child_asins, date_from=None, date_to=None):
        calls.append(list(child_asins))
        return _asin_rows() + [
            {
                **row,
                "child_asin": "COMPETITOR1",
                "source_request_id": row["source_request_id"].replace("own", "competitor"),
            }
            for row in _asin_rows()
        ]

    monkeypatch.setattr(repository, "get_asin_trends", get_asin_trends)
    monkeypatch.setattr(repository, "get_market_trend", lambda *args: _market_rows())
    monkeypatch.setattr(repository, "get_gap_contributors", lambda *args: [])
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda *args: {
            "status": "success",
            "target_data_date": TEST_DATE,
            "data_available_through": TEST_DATE,
            "effective_data_available_through": TEST_DATE,
        },
    )

    payload = service.get_comparison(
        DEFAULT_MARKET_ID,
        OWN_ASIN,
        ["COMPETITOR1", "COMPETITOR1", OWN_ASIN],
        "market_share",
        "market_share",
        date_to=TEST_DATE,
    )

    assert calls == [[OWN_ASIN, "COMPETITOR1"]]
    assert payload["context"]["competitor_asins"] == ["COMPETITOR1"]
    assert payload["context"]["metric"] == "market_share"
    assert len(payload["series"]) == 2
    assert all(point["date"] in {"2026-07-18", TEST_DATE} for series in payload["series"] for point in series["points"])

    with pytest.raises(ValueError, match="metric 'ad_rank' is not valid for scenario 'market_share'"):
        service.get_comparison(
            DEFAULT_MARKET_ID,
            OWN_ASIN,
            [],
            "market_share",
            "ad_rank",
        )


def test_comparison_uses_latest_complete_date_for_baselines_and_gaps(monkeypatch):
    gap_dates = []

    monkeypatch.setattr(
        repository,
        "get_asin_trends",
        lambda *args: _asin_rows()
        + [{**row, "child_asin": "COMPETITOR1"} for row in _asin_rows()],
    )
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda *args: {
            "run_id": "partial-20",
            "status": "partial_success",
            "target_data_date": TEST_DATE,
            "data_available_through": "2026-07-18",
            "effective_data_available_through": "2026-07-18",
        },
    )

    def get_gap_contributors(market_id, own_asin, competitors, date):
        gap_dates.append(date)
        return []

    monkeypatch.setattr(repository, "get_gap_contributors", get_gap_contributors)

    payload = service.get_comparison(
        DEFAULT_MARKET_ID,
        OWN_ASIN,
        ["COMPETITOR1"],
        "growth_quality",
        "weighted_traffic",
        date_to=TEST_DATE,
    )

    own = next(item for item in payload["comparisons"] if item["asin"] == OWN_ASIN)
    assert payload["context"]["latest_data_date"] == "2026-07-18"
    assert own["current"] == 90
    assert own["previous_complete_day"]["value"] is None
    assert gap_dates == ["2026-07-18"]


def test_validate_comparison_limits_unique_competitors():
    assert service.validate_comparison(OWN_ASIN, ["A1", "A1", "", OWN_ASIN, "A2"]) == [
        "A1",
        "A2",
    ]
    with pytest.raises(ValueError, match="at most three competitor ASINs are allowed"):
        service.validate_comparison(OWN_ASIN, ["A1", "A2", "A3", "A4"])


def test_keyword_summary_fills_all_three_tiers(monkeypatch):
    monkeypatch.setattr(
        repository,
        "get_keyword_level_summary",
        lambda market_id, *args: [
            {
                "relevance_level": "high",
                "keyword_count": 77,
                "search_volume": 1000,
                "category_search_volume": 800,
                "average_relevance_weight": 0.8,
            }
        ],
    )
    monkeypatch.setattr(
        repository,
        "get_keyword_level_trend",
        lambda market_id, *args: [
            {
                "date": TEST_DATE,
                "relevance_level": "high",
                "keyword_count": 5,
                "keyword_traffic": 120,
            }
        ],
        raising=False,
    )

    payload = service.get_keyword_summary(DEFAULT_MARKET_ID)

    assert payload["counts"] == {"all": 77, "high": 77, "mid": 0, "low": 0}
    assert [row["relevance_level"] for row in payload["levels"]] == ["high", "mid", "low"]
    assert payload["levels"][1]["search_volume"] == 0
    assert payload["levels"][1]["average_relevance_weight"] is None
    assert payload["trend"] == [
        {
            "date": TEST_DATE,
            "relevance_level": "high",
            "keyword_count": 5,
            "keyword_traffic": 120.0,
        }
    ]


def test_keyword_summary_applies_global_date_range_to_aggregate(monkeypatch):
    calls = []
    monkeypatch.setattr(repository, "get_keyword_level_summary", lambda market_id: [])
    monkeypatch.setattr(
        repository,
        "get_keyword_level_trend",
        lambda market_id, date_from=None, date_to=None: calls.append(
            (market_id, date_from, date_to)
        ) or [],
    )

    payload = service.get_keyword_summary(
        DEFAULT_MARKET_ID, "2026-07-14", "2026-07-20"
    )

    assert calls == [(DEFAULT_MARKET_ID, "2026-07-14", "2026-07-20")]
    assert payload["context"]["date_from"] == "2026-07-14"
    assert payload["context"]["date_to"] == "2026-07-20"


def test_date_context_separates_latest_complete_and_latest_snapshot(monkeypatch):
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda market_id: {
            "effective_data_available_through": "2026-07-20",
            "target_data_date": "2026-07-21",
        },
    )
    monkeypatch.setattr(
        repository,
        "get_available_dates",
        lambda market_id: {"min_date": "2026-07-01", "max_date": "2026-07-21"},
    )

    assert service.get_date_context(DEFAULT_MARKET_ID) == {
        "market_id": DEFAULT_MARKET_ID,
        "earliest_data_date": "2026-07-01",
        "latest_complete_date": "2026-07-20",
        "latest_data_date": "2026-07-21",
    }


def test_keywords_include_backend_opportunity_analysis(monkeypatch):
    monkeypatch.setattr(
        repository,
        "list_keywords",
        lambda market_id, relevance_level: [
            {
                "keyword": "slip lead",
                "relevance_level": "high",
                "search_volume": 50_000,
                "relevance_weight": 0.8,
                "competitive_difficulty": 40,
                "click_conversion_rate": 0.1,
                "source_request_id": "keyword-source-1",
            }
        ],
    )

    payload = service.get_keywords(
        DEFAULT_MARKET_ID, "high", "2026-07-14", "2026-07-20"
    )

    assert payload["keywords"][0]["opportunity"]["score"] == pytest.approx(72.5)
    assert payload["keywords"][0]["opportunity"]["evidence_ids"] == [
        "keyword-source-1"
    ]
    assert payload["context"]["date_from"] == "2026-07-14"
    assert payload["context"]["date_to"] == "2026-07-20"


def test_comparison_returns_backend_action_priorities(monkeypatch):
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(
        repository,
        "get_gap_contributors",
        lambda *args: [
            {
                "keyword": "slip lead",
                "competitor_asin": "COMPETITOR1",
                "competitor_traffic": 100,
                "own_traffic": 20,
                "traffic_gap": 80,
                "relevance_level": "high",
            }
        ],
    )

    payload = service.get_comparison(
        DEFAULT_MARKET_ID,
        OWN_ASIN,
        ["COMPETITOR1"],
        "growth_quality",
        "weighted_traffic",
        date_to=TEST_DATE,
    )

    action = payload["action_priorities"][0]
    assert action["priority_score"] == pytest.approx(84.5)
    assert action["impact"]["score"] == 80
    assert action["actionability"]["score"] == 90
    assert action["confidence"] == 1
    assert action["evidence_ids"]
    assert payload["action_priority_analysis"]["status"] == "available"


def test_comparison_marks_action_priority_unavailable_without_gap_evidence(monkeypatch):
    _patch_dashboard_repository(monkeypatch)
    monkeypatch.setattr(repository, "get_gap_contributors", lambda *args: [])

    payload = service.get_comparison(
        DEFAULT_MARKET_ID,
        OWN_ASIN,
        ["COMPETITOR1"],
        "growth_quality",
        "weighted_traffic",
        date_to=TEST_DATE,
    )

    assert payload["action_priorities"] == []
    assert payload["action_priority_analysis"]["status"] == "unavailable"
    assert payload["action_priority_analysis"]["confidence"] == 0
    assert payload["action_priority_analysis"]["unavailable_reason"]


def _cleanup_repository_test_market():
    repository.init_market_db(seed_defaults=False)
    with repository.get_market_engine().begin() as conn:
        for table in (
            "asin_keyword_snapshot_daily",
            "asin_snapshot_daily",
            "market_snapshot_daily",
            "keyword_pool_config",
            "market_run_log",
            "listing_variation_map",
            "seed_asin_config",
            "market_config",
        ):
            conn.execute(
                text(f"DELETE FROM {table} WHERE market_id=:market_id"),
                {"market_id": TEST_MARKET_ID},
            )


def test_repository_batch_queries_use_expanding_asin_binds_and_json_ready_dates():
    _cleanup_repository_test_market()
    try:
        with repository.get_market_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO asin_snapshot_daily
                      (date, market_id, child_asin, total_traffic, organic_traffic,
                       ad_traffic, weighted_traffic, high_keyword_share,
                       mid_keyword_share, low_keyword_share, is_own_product,
                       source_channel, source_provider, source_tool)
                    VALUES
                      (:date, :market_id, :asin, 10, 7, 3, 9, 0.2, 0.1, 0,
                       :is_own, 'test', 'test', 'test')
                    """
                ),
                [
                    {"date": TEST_DATE, "market_id": TEST_MARKET_ID, "asin": OWN_ASIN, "is_own": 1},
                    {"date": TEST_DATE, "market_id": TEST_MARKET_ID, "asin": "COMPETITOR1", "is_own": 0},
                    {"date": TEST_DATE, "market_id": TEST_MARKET_ID, "asin": "UNSELECTED", "is_own": 0},
                ],
            )

        rows = repository.get_asin_trends(
            TEST_MARKET_ID,
            [OWN_ASIN, "COMPETITOR1' OR 1=1 --"],
            date_to=TEST_DATE,
        )

        assert [row["child_asin"] for row in rows] == [OWN_ASIN]
        assert rows[0]["date"] == TEST_DATE
        assert repository.get_asin_trends(TEST_MARKET_ID, []) == []
    finally:
        _cleanup_repository_test_market()


def test_repository_quality_keyword_and_gap_queries_are_scoped_to_test_market():
    _cleanup_repository_test_market()
    try:
        with repository.get_market_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO market_run_log
                      (run_id, market_id, run_date, target_data_date,
                       data_available_through, collect_time_bj, status,
                       source_summary_json)
                    VALUES
                      (:run_id_19, :market_id, '2026-07-20', '2026-07-19',
                       '2026-07-19', '2026-07-20 17:00:00', 'success',
                       JSON_OBJECT('source_provider', 'test')),
                      (:run_id_21, :market_id, '2026-07-21', '2026-07-21',
                       NULL, '2026-07-21 17:00:00', 'failed', NULL)
                    """
                ),
                {
                    "market_id": TEST_MARKET_ID,
                    "run_id_19": f"{TEST_RUN_ID}-19",
                    "run_id_21": f"{TEST_RUN_ID}-21",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO keyword_pool_config
                      (market_id, keyword, relevance_level, relevance_weight,
                       source, status, source_channel, source_provider,
                       search_volume, category_search_volume)
                    VALUES
                      (:market_id, 'active high', 'high', 0.8, 'test', 'active',
                       'test', 'test', 100, 80),
                      (:market_id, 'historical strong', 'strong', 1, 'test', 'active',
                       'test', 'test', 900, 900)
                    """
                ),
                {"market_id": TEST_MARKET_ID},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO asin_keyword_snapshot_daily
                      (date, market_id, child_asin, keyword, relevance_level,
                       relevance_weight, keyword_traffic, asin_keyword_share,
                       organic_rank, ad_rank, organic_traffic, ad_traffic,
                       source_channel, source_provider, source_tool)
                    VALUES
                      (:date, :market_id, :own_asin, 'slip lead', 'high', 0.8,
                       10, 0.1, 20, 10, 7, 3, 'test', 'test', 'test'),
                      (:date, :market_id, 'COMPETITOR1', 'slip lead', 'high', 0.8,
                       30, 0.3, 5, 2, 20, 10, 'test', 'test', 'test'),
                      (:date, :market_id, 'UNSELECTED', 'slip lead', 'high', 0.8,
                       90, 0.9, 1, 1, 50, 40, 'test', 'test', 'test')
                    """
                ),
                {
                    "date": TEST_DATE,
                    "market_id": TEST_MARKET_ID,
                    "own_asin": OWN_ASIN,
                },
            )

        quality = repository.get_latest_run_quality(TEST_MARKET_ID, TEST_DATE)
        failed_quality = repository.get_latest_run_quality(
            TEST_MARKET_ID, "2026-07-21"
        )
        summary = repository.get_keyword_level_summary(TEST_MARKET_ID)
        gaps = repository.get_gap_contributors(
            TEST_MARKET_ID,
            OWN_ASIN,
            ["COMPETITOR1", "X' OR 1=1 --"],
            TEST_DATE,
        )

        assert quality["run_id"] == f"{TEST_RUN_ID}-19"
        assert quality["target_data_date"] == "2026-07-19"
        assert quality["effective_data_available_through"] == "2026-07-19"
        assert failed_quality["run_id"] == f"{TEST_RUN_ID}-21"
        assert failed_quality["data_available_through"] is None
        assert failed_quality["effective_data_available_through"] == "2026-07-19"
        assert [row["relevance_level"] for row in summary] == ["high"]
        assert summary[0]["keyword_count"] == 1
        assert [row["competitor_asin"] for row in gaps] == ["COMPETITOR1"]
        assert gaps[0]["traffic_gap"] == 20
    finally:
        _cleanup_repository_test_market()


def test_legacy_repository_comparison_uses_one_batch_asin_query(monkeypatch):
    calls = []

    def get_asin_trends(market_id, child_asins, date_from=None, date_to=None):
        calls.append(list(child_asins))
        return [
            {"child_asin": child_asins[0], "date": TEST_DATE},
            {"child_asin": child_asins[1], "date": TEST_DATE},
        ]

    monkeypatch.setattr(repository, "get_asin_trends", get_asin_trends)
    monkeypatch.setattr(repository, "get_market_trend", lambda *args: [])
    monkeypatch.setattr(
        repository,
        "get_asin_trend",
        lambda *args: pytest.fail("single-ASIN query creates an N+1 path"),
    )

    payload = repository.get_comparison(
        TEST_MARKET_ID,
        OWN_ASIN,
        "COMPETITOR1",
    )

    assert calls == [[OWN_ASIN, "COMPETITOR1"]]
    assert payload["left"][0]["child_asin"] == OWN_ASIN
    assert payload["right"][0]["child_asin"] == "COMPETITOR1"


def test_dashboard_read_path_runs_only_four_selects_after_initialization():
    _cleanup_repository_test_market()
    engine = repository.get_market_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO market_config
                      (market_id, market_name, market_display_name, country,
                       timezone, default_collect_time, status, source_channel,
                       source_provider)
                    VALUES
                      (:market_id, 'task6', 'Task 6', 'US', 'America/New_York',
                       '17:00 Asia/Shanghai', 'active', 'test', 'test')
                    """
                ),
                {"market_id": TEST_MARKET_ID},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO seed_asin_config
                      (market_id, asin, source_category, source_category_path,
                       is_seed_asin, is_own_product, first_seen_date, status,
                       source_channel, source_provider)
                    VALUES
                      (:market_id, :asin, 'test', 'test', 1, 1, :date, 'active',
                       'test', 'test')
                    """
                ),
                {"market_id": TEST_MARKET_ID, "asin": OWN_ASIN, "date": TEST_DATE},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO market_snapshot_daily
                      (date, market_id, total_traffic, weighted_traffic,
                       organic_traffic, ad_traffic, high_relevance_traffic,
                       mid_relevance_traffic, low_relevance_traffic,
                       top_asin_share, top_keyword_share, listing_count,
                       asin_count, keyword_count, source_channel, source_provider,
                       source_tool, source_request_id)
                    VALUES
                      (:date, :market_id, 1000, 900, 700, 300, 500, 300, 200,
                       0.4, 0.3, 10, 10, 20, 'test', 'test', 'test', 'market-20')
                    """
                ),
                {"date": TEST_DATE, "market_id": TEST_MARKET_ID},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO asin_snapshot_daily
                      (date, market_id, child_asin, total_traffic, organic_traffic,
                       ad_traffic, weighted_traffic, high_keyword_share,
                       mid_keyword_share, low_keyword_share, is_own_product,
                       source_channel, source_provider, source_tool,
                       source_request_id)
                    VALUES
                      (:date, :market_id, :asin, 100, 70, 30, 90, 0.2, 0.1, 0,
                       1, 'test', 'test', 'test', 'own-20')
                    """
                ),
                {"date": TEST_DATE, "market_id": TEST_MARKET_ID, "asin": OWN_ASIN},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO market_run_log
                      (run_id, market_id, run_date, target_data_date,
                       data_available_through, collect_time_bj, status,
                       source_summary_json)
                    VALUES
                      (:run_id, :market_id, :date, :date, :date,
                       '2026-07-21 17:00:00', 'success',
                       JSON_OBJECT('source_provider', 'test'))
                    """
                ),
                {
                    "run_id": TEST_RUN_ID,
                    "market_id": TEST_MARKET_ID,
                    "date": TEST_DATE,
                },
            )

        statements = []

        def record_statement(_conn, _cursor, statement, _params, _context, _many):
            statements.append(" ".join(statement.split()))

        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            payload = service.get_dashboard(TEST_MARKET_ID, date_to=TEST_DATE)
            dashboard_statements = list(statements)
            statements.clear()
            comparison = service.get_comparison(
                TEST_MARKET_ID,
                OWN_ASIN,
                ["COMPETITOR1"],
                "growth_quality",
                "weighted_traffic",
                date_to=TEST_DATE,
            )
            comparison_statements = list(statements)
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)

        assert payload["context"]["latest_data_date"] == TEST_DATE
        assert len(dashboard_statements) == 4
        assert all(
            statement.upper().startswith("SELECT")
            for statement in dashboard_statements
        )
        assert (
            sum(
                "FROM asin_snapshot_daily" in statement
                for statement in dashboard_statements
            )
            == 1
        )
        assert comparison["context"]["latest_data_date"] == TEST_DATE
        assert len(comparison_statements) == 3
        assert all(
            statement.upper().startswith("SELECT")
            for statement in comparison_statements
        )
        assert (
            sum(
                "FROM asin_snapshot_daily" in statement
                for statement in comparison_statements
            )
            == 1
        )
    finally:
        _cleanup_repository_test_market()
