import json
import importlib.util
from pathlib import Path

from sqlalchemy import text

from ads_funnel.api import market_monitor_db as db


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ads_funnel" / "scripts" / "market_monitor_collect_daily.py"
MARKET_ID = "test_daily_script_market"
DATE = "2026-07-20"

spec = importlib.util.spec_from_file_location("market_monitor_collect_daily", SCRIPT)
daily_script = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(daily_script)


def _prepare_market():
    db.init_market_db(seed_defaults=False)
    with db.get_market_engine().begin() as conn:
        _cleanup_market(conn)
        conn.execute(
            text(
                """
                INSERT INTO market_config
                  (market_id, market_name, market_display_name, country, timezone,
                   default_collect_time, description, status, source_channel,
                   source_provider)
                VALUES
                  (:market_id, 'script_test_market', 'Script Test Market',
                   'US', 'America/New_York', '17:00 Asia/Shanghai',
                   'Daily script test market', 'active', 'manual', 'user')
                """
            ),
            {"market_id": MARKET_ID},
        )
        conn.execute(
            text(
                """
                INSERT INTO seed_asin_config
                  (market_id, asin, source_category, source_category_path,
                   is_seed_asin, is_own_product, notes, first_seen_date,
                   status, source_channel, source_provider)
                VALUES
                  (:market_id, 'B0D6G27DNH', 'Training Leashes',
                   'Pet Supplies > Dogs > Training Leashes',
                   1, 1, 'script test own asin', :date, 'active', 'manual', 'user')
                """
            ),
            {"market_id": MARKET_ID, "date": DATE},
        )
        for keyword, level, weight in (
            ("slip lead", "high", 0.925),
            ("dog leash", "mid", 0.35),
            ("dog harness", "low", 0.10),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO keyword_pool_config
                      (market_id, keyword, relevance_level, relevance_weight,
                       source, is_core_keyword, status, source_channel, source_provider)
                    VALUES
                      (:market_id, :keyword, :level, :weight,
                       'script_test', 1, 'active', 'manual', 'user')
                    """
                ),
                {"market_id": MARKET_ID, "keyword": keyword, "level": level, "weight": weight},
            )


def _cleanup_market(conn):
    for table in [
        "daily_report_result",
        "keyword_asin_competition_daily",
        "asin_keyword_snapshot_daily",
        "keyword_snapshot_daily",
        "asin_snapshot_daily",
        "market_snapshot_daily",
        "listing_variation_map",
        "raw_response_log",
        "raw_request_log",
        "market_run_log",
        "keyword_pool_config",
        "seed_asin_config",
        "market_config",
    ]:
        conn.execute(text(f"DELETE FROM {table} WHERE market_id=:market_id"), {"market_id": MARKET_ID})


def test_daily_script_emits_xydc_primary_plan_with_auxiliary_sources():
    _prepare_market()
    plan = daily_script.build_collection_plan(
        market_id=MARKET_ID,
        run_date=DATE,
        target_data_date=DATE,
        country="US",
        core_asins=["B0D6G27DNH"],
        keyword_page_size=50,
        competition_page_size=20,
        include_weekly=False,
    )

    assert plan["source_priority"] == ["xydc", "sif", "sorftime"]
    assert {call["provider"] for call in plan["primary_calls"]} == {"xydc"}
    assert any(call["tool"] == "get_asin_traffic" for call in plan["primary_calls"])
    keyword_info = next(call for call in plan["primary_calls"] if call["tool"] == "get_keyword_info")
    assert keyword_info["params"]["keywords"] == ["slip lead", "dog leash", "dog harness"]
    keyword_analysis = [
        call["params"]["keyword"]
        for call in plan["primary_calls"]
        if call["tool"] == "get_keyword_asin_analysis"
    ]
    assert keyword_analysis == ["slip lead"]
    auxiliary_keywords = [
        call["params_template"]["keyword"]
        for call in plan["auxiliary_calls"]
        if call["tool"] == "product_ranking_trend_by_keyword"
    ]
    assert auxiliary_keywords == ["slip lead"]
    assert {"sif", "sorftime"}.issubset({call["provider"] for call in plan["auxiliary_calls"]})

    with db.get_market_engine().begin() as conn:
        _cleanup_market(conn)


def test_daily_script_ingests_source_agnostic_payload(tmp_path):
    _prepare_market()
    payload = {
        "market_id": MARKET_ID,
        "run_date": DATE,
        "collect_time_bj": "2026-07-20 17:00:00",
        "source_channel": "api",
        "source_provider": "script_provider",
        "source_tool": "keyword_metrics",
        "request_params": {"keywords": ["slip lead"]},
        "response_json": {"ok": True},
        "target_data_date": DATE,
        "data_available_through": DATE,
        "normalized": {
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
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    output = daily_script.ingest_payload(payload_path)
    rows = db.get_keyword_trend(MARKET_ID, "slip lead", DATE, DATE)

    assert output["batch_count"] == 1
    assert output["results"][0]["status"] == "success"
    assert len(rows) == 1
    assert rows[0]["source_provider"] == "script_provider"

    with db.get_market_engine().begin() as conn:
        _cleanup_market(conn)
