import pytest
from sqlalchemy import text

from ads_funnel.api import market_monitor_collector as collector
from ads_funnel.api import market_monitor_db as db
from ads_funnel.api.market_monitoring import collector as collector_impl


MARKET_ID = "test_slip_lead_leash_collector"
DATE = "2026-07-20"


def _cleanup_test_rows():
    db.init_market_db(seed_defaults=False)
    with db.get_market_engine().begin() as conn:
        conn.execute(text("DELETE FROM daily_report_result WHERE market_id=:market_id AND report_date=:date"), {"market_id": MARKET_ID, "date": DATE})
        conn.execute(text("DELETE FROM keyword_asin_competition_daily WHERE market_id=:market_id AND date=:date"), {"market_id": MARKET_ID, "date": DATE})
        conn.execute(text("DELETE FROM asin_keyword_snapshot_daily WHERE market_id=:market_id AND date=:date"), {"market_id": MARKET_ID, "date": DATE})
        conn.execute(text("DELETE FROM keyword_snapshot_daily WHERE market_id=:market_id AND date=:date"), {"market_id": MARKET_ID, "date": DATE})
        conn.execute(text("DELETE FROM asin_snapshot_daily WHERE market_id=:market_id AND date=:date"), {"market_id": MARKET_ID, "date": DATE})
        conn.execute(text("DELETE FROM market_snapshot_daily WHERE market_id=:market_id AND date=:date"), {"market_id": MARKET_ID, "date": DATE})
        conn.execute(text("DELETE FROM listing_variation_map WHERE market_id=:market_id AND source_request_id='test-request'"), {"market_id": MARKET_ID})
        conn.execute(text("DELETE FROM raw_response_log WHERE market_id=:market_id"), {"market_id": MARKET_ID})
        conn.execute(text("DELETE FROM raw_request_log WHERE market_id=:market_id"), {"market_id": MARKET_ID})
        conn.execute(text("DELETE FROM market_run_log WHERE market_id=:market_id AND run_date=:date"), {"market_id": MARKET_ID, "date": DATE})
        conn.execute(text("DELETE FROM keyword_pool_config WHERE market_id=:market_id"), {"market_id": MARKET_ID})
        conn.execute(text("DELETE FROM seed_asin_config WHERE market_id=:market_id"), {"market_id": MARKET_ID})
        conn.execute(text("DELETE FROM market_config WHERE market_id=:market_id"), {"market_id": MARKET_ID})
        conn.execute(
            text(
                """
                INSERT INTO market_config
                  (market_id, market_name, market_display_name, country, timezone,
                   default_collect_time, description, status, source_channel,
                   source_provider)
                VALUES
                  (:market_id, 'collector_test_market', 'Collector Test Market',
                   'US', 'America/New_York', '17:00 Asia/Shanghai',
                   'Collector persistence test market', 'active', 'manual', 'user')
                """
            ),
            {"market_id": MARKET_ID},
        )


def test_persist_daily_source_batch_supports_mcp_and_api_sources_without_full_day_replace():
    _cleanup_test_rows()

    mcp_result = collector.persist_daily_source_batch(
        market_id=MARKET_ID,
        run_date=DATE,
        collect_time_bj="2026-07-20 17:00:00",
        source_channel="mcp",
        source_provider="xydc",
        source_tool="get_keyword_info",
        request_params={"keywords": ["slip lead"]},
        response_json={"status": 200},
        target_data_date=DATE,
        data_available_through=DATE,
        purpose="test source-agnostic mcp write",
        normalized={
            "keyword_snapshots": [
                {
                    "keyword": "slip lead",
                    "relevance_level": "high",
                    "relevance_weight": 0.925,
                    "search_volume": 2342,
                    "aba_rank": 91397,
                    "cpc": 0.79,
                    "competitive_difficulty": 57,
                    "click_conversion_rate": 0.111321,
                    "total_market_traffic": 8055,
                    "market_traffic_share": 0.08822,
                }
            ]
        },
        write_mode="upsert",
    )
    api_result = collector.persist_daily_source_batch(
        market_id=MARKET_ID,
        run_date=DATE,
        collect_time_bj="2026-07-20 17:05:00",
        source_channel="api",
        source_provider="future_provider",
        source_tool="keyword_metrics",
        request_params={"keywords": ["slip leash"]},
        response_json={"ok": True},
        target_data_date=DATE,
        data_available_through=DATE,
        purpose="test source-agnostic api write",
        normalized={
            "keyword_snapshots": [
                {
                    "keyword": "slip leash",
                    "relevance_level": "high",
                    "relevance_weight": 0.925,
                    "search_volume": 1422,
                    "aba_rank": 134430,
                    "cpc": 0.63,
                    "competitive_difficulty": 58,
                    "click_conversion_rate": 0.113262,
                    "total_market_traffic": 3065,
                    "market_traffic_share": 0.048998,
                }
            ],
            "keyword_asin_competition": [
                {
                    "keyword": "slip leash",
                    "relevance_level": "high",
                    "relevance_weight": 0.925,
                    "parent_asin": "B08Y1PH64J",
                    "child_asin": "B08Y5RYPTS",
                    "brand": "Fida",
                    "title": "Fida Durable Slip Lead Dog Leash",
                    "competition_rank": 1,
                    "keyword_traffic": 3065,
                    "keyword_traffic_share": 0.048998,
                    "organic_rank": None,
                    "ad_rank": None,
                    "organic_traffic": 0,
                    "ad_traffic": 0,
                    "is_seed_asin": 1,
                    "is_own_product": 0,
                }
            ],
        },
        write_mode="upsert",
    )

    assert mcp_result["status"] == "success"
    assert api_result["status"] == "success"
    assert len(db.get_keyword_trend(MARKET_ID, "slip lead", DATE, DATE)) == 1
    assert len(db.get_keyword_trend(MARKET_ID, "slip leash", DATE, DATE)) == 1
    assert len(db.get_keyword_asin_competition(MARKET_ID, "slip leash", DATE)) == 1

    with db.get_market_engine().connect() as conn:
        raw_sources = {
            (row["source_channel"], row["source_provider"], row["source_tool"])
            for row in conn.execute(
                text(
                    """
                    SELECT source_channel, source_provider, source_tool
                    FROM raw_request_log
                    WHERE market_id=:market_id
                    """
                ),
                {"market_id": MARKET_ID},
            ).mappings().all()
        }
        keyword_count = conn.execute(
            text("SELECT COUNT(*) FROM keyword_snapshot_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).scalar()

    assert ("mcp", "xydc", "get_keyword_info") in raw_sources
    assert ("api", "future_provider", "keyword_metrics") in raw_sources
    assert keyword_count == 2

    _cleanup_test_rows()


def test_collector_writes_run_raw_logs_snapshots_and_report():
    _cleanup_test_rows()

    run_id = collector.create_run(MARKET_ID, DATE, "2026-07-20 17:00:00")
    request_id = collector.record_raw_request(
        run_id=run_id,
        market_id=MARKET_ID,
        source_channel="mcp",
        source_provider="xydc",
        source_tool="get_asin_keywords",
        request_params={"asin": "B0D6G27DNH", "country": "US"},
        purpose="test collector raw request",
        request_id="test-request",
    )
    response_id = collector.record_raw_response(
        request_id=request_id,
        run_id=run_id,
        market_id=MARKET_ID,
        source_channel="mcp",
        source_provider="xydc",
        source_tool="get_asin_keywords",
        response_status="success",
        response_json={"status": 200, "data": {"asin": "B0D6G27DNH"}},
        cost_credits=1,
        response_id="test-response",
    )

    collector.upsert_listing_variations(
        [
            {
                "market_id": MARKET_ID,
                "parent_asin": "B0GR928XD8",
                "child_asin": "B0D6G27DNH",
                "is_seed_child": 1,
                "is_representative_child": 1,
                "first_seen_date": DATE,
                "last_seen_date": DATE,
                "source_channel": "mcp",
                "source_provider": "xydc",
                "source_tool": "get_asin_variations",
                "source_request_id": request_id,
            }
        ]
    )
    collector.replace_market_snapshot(
        {
            "date": DATE,
            "market_id": MARKET_ID,
            "total_traffic": 1000,
            "weighted_traffic": 800,
            "organic_traffic": 600,
            "ad_traffic": 400,
            "high_relevance_traffic": 300,
            "mid_relevance_traffic": 150,
            "low_relevance_traffic": 50,
            "top_asin_share": 0.35,
            "top_keyword_share": 0.42,
            "listing_count": 20,
            "asin_count": 24,
            "keyword_count": 80,
            "source_channel": "mcp",
            "source_provider": "xydc",
            "source_tool": "normalized_market_snapshot",
            "source_request_id": request_id,
        }
    )
    asin_rows = [
        {
            "date": DATE,
            "market_id": MARKET_ID,
            "parent_asin": "B0GR928XD8",
            "child_asin": "B0D6G27DNH",
            "brand": "HowGo",
            "title": "HowGo Slip Lead Dog Leash",
            "image_url": "https://images.example.test/howgo.jpg",
            "price": 23.29,
            "rating": 4.4,
            "reviews": 1210,
            "total_traffic": 100,
            "organic_traffic": 60,
            "ad_traffic": 40,
            "weighted_traffic": 85,
            "high_keyword_share": 0.2,
            "mid_keyword_share": 0.1,
            "low_keyword_share": 0,
            "rank_in_market": 5,
            "is_own_product": 1,
            "source_channel": "mcp",
            "source_provider": "xydc",
            "source_tool": "normalized_asin_snapshot",
            "source_request_id": request_id,
        }
    ]
    collector.replace_asin_snapshots(MARKET_ID, DATE, asin_rows)
    collector.replace_asin_snapshots(MARKET_ID, DATE, asin_rows)

    persisted_asin = db.get_asin_snapshots(MARKET_ID, DATE)[0]
    assert persisted_asin["image_url"] == "https://images.example.test/howgo.jpg"

    keyword_rows = [
        {
            "date": DATE,
            "market_id": MARKET_ID,
            "keyword": "slip lead",
            "relevance_level": "high",
            "relevance_weight": 0.925,
            "search_volume": 2342,
            "aba_rank": None,
            "cpc": 0.79,
            "competitive_difficulty": None,
            "click_conversion_rate": 0.111321,
            "total_market_traffic": 200,
            "market_traffic_share": 0.2,
            "source_channel": "mcp",
            "source_provider": "xydc",
            "source_tool": "normalized_keyword_snapshot",
            "source_request_id": request_id,
        }
    ]
    collector.replace_keyword_snapshots(MARKET_ID, DATE, keyword_rows)
    collector.replace_asin_keyword_snapshots(
        MARKET_ID,
        DATE,
        [
            {
                "date": DATE,
                "market_id": MARKET_ID,
                "parent_asin": "B0GR928XD8",
                "child_asin": "B0D6G27DNH",
                "keyword": "slip lead",
                "relevance_level": "high",
                "relevance_weight": 0.925,
                "keyword_traffic": 30,
                "asin_keyword_share": 0.3,
                "organic_rank": 27,
                "ad_rank": 16,
                "organic_traffic": 10,
                "ad_traffic": 20,
                "source_channel": "mcp",
                "source_provider": "xydc",
                "source_tool": "normalized_asin_keyword_snapshot",
                "source_request_id": request_id,
            }
        ],
    )
    competition_rows = [
        {
            "date": DATE,
            "market_id": MARKET_ID,
            "keyword": "slip lead",
            "relevance_level": "high",
            "relevance_weight": 0.925,
            "parent_asin": "B0GR928XD8",
            "child_asin": "B0D6G27DNH",
            "brand": "HowGo",
            "title": "HowGo Slip Lead Dog Leash",
            "competition_rank": 7,
            "keyword_traffic": 1949,
            "keyword_traffic_share": 0.018994,
            "organic_rank": 23,
            "ad_rank": 4,
            "organic_traffic": 840,
            "ad_traffic": 1109,
            "is_seed_asin": 1,
            "is_own_product": 1,
            "source_channel": "mcp",
            "source_provider": "xydc",
            "source_tool": "get_keyword_asin_analysis",
            "source_request_id": request_id,
        }
    ]
    collector.replace_keyword_asin_competition(MARKET_ID, DATE, competition_rows)
    collector.replace_keyword_asin_competition(MARKET_ID, DATE, competition_rows)
    report_id = collector.save_daily_report(
        {
            "run_id": run_id,
            "market_id": MARKET_ID,
            "report_date": DATE,
            "target_data_date": DATE,
            "report_type": "daily_market_monitor",
            "title": "Slip Lead Leash Daily Monitor",
            "summary_md": "summary",
            "report_md": "# report",
            "report_html": "<h1>report</h1>",
        }
    )
    report_id_again = collector.save_daily_report(
        {
            "run_id": run_id,
            "market_id": MARKET_ID,
            "report_date": DATE,
            "target_data_date": DATE,
            "report_type": "daily_market_monitor",
            "title": "Slip Lead Leash Daily Monitor",
            "summary_md": "summary updated",
            "report_md": "# report updated",
            "report_html": "<h1>report updated</h1>",
        }
    )
    collector.finish_run(run_id, "success", target_data_date=DATE, data_available_through=DATE, source_summary={"xydc": DATE})

    assert request_id == "test-request"
    assert response_id == "test-response"
    assert report_id > 0
    assert report_id_again > 0
    assert db.get_available_dates(MARKET_ID)["max_date"] == DATE
    assert len(db.get_market_trend(MARKET_ID, DATE, DATE)) == 1
    assert len(db.get_asin_trend(MARKET_ID, "B0D6G27DNH", DATE, DATE)) == 1
    assert len(db.get_keyword_trend(MARKET_ID, "slip lead", DATE, DATE)) == 1
    assert len(db.get_asin_keyword_composition(MARKET_ID, "B0D6G27DNH", DATE)) == 1
    competition = db.get_keyword_asin_competition(MARKET_ID, "slip lead", DATE)
    assert len(competition) == 1
    assert competition[0]["child_asin"] == "B0D6G27DNH"
    assert len(db.get_daily_reports(MARKET_ID, DATE)) == 1
    assert any(row["run_id"] == run_id and row["status"] == "success" for row in db.list_run_logs(MARKET_ID))

    with db.get_market_engine().connect() as conn:
        asin_count = conn.execute(
            text("SELECT COUNT(*) FROM asin_snapshot_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).scalar()
        competition_count = conn.execute(
            text("SELECT COUNT(*) FROM keyword_asin_competition_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).scalar()
    assert asin_count == 1
    assert competition_count == 1

    with db.get_market_engine().connect() as conn:
        legacy_market_traffic = conn.execute(
            text("SELECT strong_relevance_traffic FROM market_snapshot_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).scalar()
        legacy_asin_share = conn.execute(
            text("SELECT strong_keyword_share FROM asin_snapshot_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).scalar()
    assert legacy_market_traffic == 0
    assert legacy_asin_share == 0

    _cleanup_test_rows()


@pytest.mark.parametrize(
    "write_rows",
    [
        lambda rows: collector.replace_keyword_snapshots(MARKET_ID, DATE, rows),
        lambda rows: collector.upsert_keyword_snapshots(rows),
        lambda rows: collector.replace_asin_keyword_snapshots(MARKET_ID, DATE, rows),
        lambda rows: collector.upsert_asin_keyword_snapshots(rows),
        lambda rows: collector.replace_keyword_asin_competition(MARKET_ID, DATE, rows),
        lambda rows: collector.upsert_keyword_asin_competition(rows),
    ],
)
def test_collector_rejects_removed_relevance_tier_for_all_keyword_snapshot_writes(write_rows):
    _cleanup_test_rows()

    with pytest.raises(ValueError, match="invalid relevance_level: strong"):
        write_rows([{"relevance_level": "strong"}])

    _cleanup_test_rows()


def _minimal_asin_snapshot(child_asin: str, traffic: float) -> dict:
    return {
        "parent_asin": child_asin,
        "child_asin": child_asin,
        "brand": "Test",
        "title": "Test ASIN",
        "image_url": None,
        "price": None,
        "rating": None,
        "reviews": None,
        "total_traffic": traffic,
        "organic_traffic": traffic,
        "ad_traffic": 0,
        "weighted_traffic": traffic,
        "high_keyword_share": 0.2,
        "mid_keyword_share": 0,
        "low_keyword_share": 0,
        "rank_in_market": 1,
        "is_own_product": 1,
    }


def _minimal_keyword_snapshot(keyword: str, traffic: float) -> dict:
    return {
        "keyword": keyword,
        "relevance_level": "high",
        "relevance_weight": 0.9,
        "search_volume": 100,
        "aba_rank": None,
        "cpc": None,
        "competitive_difficulty": None,
        "click_conversion_rate": None,
        "total_market_traffic": traffic,
        "market_traffic_share": 0.1,
    }


def test_daily_batch_rolls_back_earlier_sections_when_a_late_writer_fails(monkeypatch):
    _cleanup_test_rows()
    with db.get_market_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO asin_snapshot_daily "
                "(date,market_id,child_asin,total_traffic,source_channel,source_provider,source_tool) "
                "VALUES (:date,:market_id,'OLD',10,'manual','test','fixture')"
            ),
            {"date": DATE, "market_id": MARKET_ID},
        )
        conn.execute(
            text(
                "INSERT INTO keyword_snapshot_daily "
                "(date,market_id,keyword,relevance_level,relevance_weight,total_market_traffic," 
                "source_channel,source_provider,source_tool) "
                "VALUES (:date,:market_id,'old keyword','high',.9,10,'manual','test','fixture')"
            ),
            {"date": DATE, "market_id": MARKET_ID},
        )

    def fail_late(_conn, _row):
        raise RuntimeError("late normalized writer failed")

    monkeypatch.setattr(collector_impl, "_insert_keyword_snapshot", fail_late)
    with pytest.raises(RuntimeError, match="late normalized writer failed"):
        collector.persist_daily_source_batch(
            market_id=MARKET_ID,
            run_date=DATE,
            collect_time_bj="2026-07-20 17:00:00",
            source_channel="mcp",
            source_provider="xydc",
            source_tool="atomic-test",
            request_params={"test": True},
            target_data_date=DATE,
            data_available_through=DATE,
            normalized={
                "asin_snapshots": [_minimal_asin_snapshot("NEW", 999)],
                "keyword_snapshots": [_minimal_keyword_snapshot("new keyword", 999)],
            },
            write_mode="replace_day",
        )

    with db.get_market_engine().connect() as conn:
        asins = conn.execute(
            text("SELECT child_asin,total_traffic FROM asin_snapshot_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).mappings().all()
        keywords = conn.execute(
            text("SELECT keyword,total_market_traffic FROM keyword_snapshot_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).mappings().all()
        runs = conn.execute(
            text("SELECT status,error_message FROM market_run_log WHERE market_id=:market_id AND run_date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).mappings().all()
        raw_count = conn.execute(
            text("SELECT COUNT(*) FROM raw_request_log WHERE market_id=:market_id"),
            {"market_id": MARKET_ID},
        ).scalar()

    assert [(row["child_asin"], row["total_traffic"]) for row in asins] == [("OLD", 10)]
    assert [(row["keyword"], row["total_market_traffic"]) for row in keywords] == [("old keyword", 10)]
    assert len(runs) == 1 and runs[0]["status"] == "failed"
    assert "late normalized writer failed" in runs[0]["error_message"]
    assert raw_count == 0
    _cleanup_test_rows()


def test_replace_day_distinguishes_omitted_section_from_explicit_empty_section():
    _cleanup_test_rows()
    with db.get_market_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO asin_snapshot_daily "
                "(date,market_id,child_asin,total_traffic,source_channel,source_provider,source_tool) "
                "VALUES (:date,:market_id,'OLD',10,'manual','test','fixture')"
            ),
            {"date": DATE, "market_id": MARKET_ID},
        )

    common = dict(
        market_id=MARKET_ID,
        run_date=DATE,
        collect_time_bj="2026-07-20 17:00:00",
        source_channel="mcp",
        source_provider="xydc",
        source_tool="empty-test",
        request_params={"test": True},
        target_data_date=DATE,
        data_available_through=DATE,
        write_mode="replace_day",
    )
    collector.persist_daily_source_batch(**common, normalized={})
    with db.get_market_engine().connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM asin_snapshot_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).scalar() == 1

    collector.persist_daily_source_batch(**common, normalized={"asin_snapshots": []})
    with db.get_market_engine().connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM asin_snapshot_daily WHERE market_id=:market_id AND date=:date"),
            {"market_id": MARKET_ID, "date": DATE},
        ).scalar() == 0
    _cleanup_test_rows()


def test_daily_batch_prevalidates_every_section_before_any_snapshot_writer(monkeypatch):
    _cleanup_test_rows()
    writer_called = False

    def unexpected_writer(_conn, _row):
        nonlocal writer_called
        writer_called = True
        raise AssertionError("snapshot writer must not run before full validation")

    monkeypatch.setattr(collector_impl, "_insert_asin_snapshot", unexpected_writer)
    with pytest.raises(ValueError, match=r"normalized\.keyword_snapshots\[0\] missing fields"):
        collector.persist_daily_source_batch(
            market_id=MARKET_ID,
            run_date=DATE,
            collect_time_bj="2026-07-20 17:00:00",
            source_channel="mcp",
            source_provider="xydc",
            source_tool="prevalidate-test",
            request_params={"test": True},
            target_data_date=DATE,
            normalized={
                "asin_snapshots": [_minimal_asin_snapshot("NEW", 999)],
                "keyword_snapshots": [{"keyword": "incomplete", "relevance_level": "high"}],
            },
            write_mode="replace_day",
        )

    with db.get_market_engine().connect() as conn:
        raw_count = conn.execute(
            text("SELECT COUNT(*) FROM raw_request_log WHERE market_id=:market_id"),
            {"market_id": MARKET_ID},
        ).scalar()
        failed_count = conn.execute(
            text("SELECT COUNT(*) FROM market_run_log WHERE market_id=:market_id AND status='failed'"),
            {"market_id": MARKET_ID},
        ).scalar()
    assert writer_called is False
    assert raw_count == 0
    assert failed_count == 1
    _cleanup_test_rows()
