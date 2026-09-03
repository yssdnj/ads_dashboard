"""Persist keyword_asin_competition from compact payload files."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ads_funnel.api import market_monitor_collector as collector  # noqa: E402
from ads_funnel.api import market_monitor_db as db  # noqa: E402


def persist_one(keyword: str, rows: list, run_date: str, write_mode: str = "upsert"):
    keyword_meta = {row["keyword"]: row for row in db.list_keywords("slip_lead_leash")}
    asin_meta = {row["asin"].upper(): row for row in db.list_asins("slip_lead_leash")}
    kw_meta = keyword_meta.get(keyword, {})

    normalized_rows = []
    for entry in rows:
        asin = entry["asin"]
        ranks = {r.get("position"): r.get("pageRank") for r in entry.get("ranks", []) if isinstance(r, dict)}
        or_rank = ranks.get("or")
        sp_rank = ranks.get("sp")
        traffic = entry.get("trafficSummary", {}).get("traffic", {}).get("total", 0)
        ad_traffic = entry.get("trafficSummary", {}).get("traffic", {}).get("advertising", 0) or 0
        organic_traffic = max(0, traffic - ad_traffic)
        asin_row = asin_meta.get(asin.upper(), {})
        normalized_rows.append({
            "keyword": keyword,
            "relevance_level": kw_meta.get("relevance_level", "high"),
            "relevance_weight": float(kw_meta.get("relevance_weight", 0.0)),
            "parent_asin": asin,
            "child_asin": asin,
            "brand": entry.get("asinInfo", {}).get("brand"),
            "title": entry.get("asinInfo", {}).get("title"),
            "competition_rank": entry.get("rank_index", 0) or 0,
            "keyword_traffic": traffic,
            "keyword_traffic_share": 0.0,
            "organic_rank": or_rank or 0,
            "ad_rank": sp_rank or 0,
            "organic_traffic": organic_traffic,
            "ad_traffic": ad_traffic,
            "is_seed_asin": int(bool(asin_row.get("is_seed_asin", True))),
            "is_own_product": int(bool(asin_row.get("is_own_product", False))),
        })

    if not normalized_rows:
        return None

    result = collector.persist_daily_source_batch(
        market_id="slip_lead_leash",
        run_date=run_date,
        collect_time_bj="2026-08-18 17:50:00",
        target_data_date=run_date,
        data_available_through=run_date,
        source_channel="mcp",
        source_provider="xydc",
        source_tool="xydc_get_keyword_asin_analysis",
        request_params={"keyword": keyword, "country": "US", "page": 1},
        response_status="success",
        write_mode=write_mode,
        normalized={"keyword_asin_competition": normalized_rows},
    )
    return {"keyword": keyword, "rows": len(normalized_rows), "result": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", default="2026-08-18")
    parser.add_argument("--write-mode", default="upsert")
    parser.add_argument("--input-dir", default=".")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    payload_files = sorted(input_dir.glob(".tmp_r_kac_*.json"))
    print(f"Found {len(payload_files)} payload files")
    for pf in payload_files:
        # filename -> keyword
        keyword = pf.stem.replace(".tmp_r_kac_", "").replace("_", " ")
        data = json.loads(pf.read_text(encoding="utf-8"))
        rows = data.get("data", {}).get("list", [])
        # Add rank_index
        for idx, row in enumerate(rows, start=1):
            row["rank_index"] = idx
        try:
            result = persist_one(keyword, rows, args.run_date, args.write_mode)
            if result:
                print(f"[{keyword}] persisted {result['rows']} rows: status={result['result']['status']}")
        except Exception as e:
            print(f"[{keyword}] FAILED: {e}")


if __name__ == "__main__":
    raise SystemExit(main())