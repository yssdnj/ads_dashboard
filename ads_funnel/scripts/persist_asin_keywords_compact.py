"""Persist asin_keyword_snapshots from compact tuple data.

Each tuple: (searchTerm, organic_rank, ad_rank, total_traffic).
Reads ASIN rows from per-ASIN JSON files written by .tmp_save_akw_compact.py.
"""
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


def persist_one(asin: str, rows: list, run_date: str, write_mode: str = "upsert"):
    """Build normalized asin_keyword_snapshots rows and persist via collector."""
    asin_meta = {row["asin"].upper(): row for row in db.list_asins("slip_lead_leash")}
    keyword_meta = {row["keyword"]: row for row in db.list_keywords("slip_lead_leash")}

    normalized_rows = []
    for search_term, or_rank, sp_rank, traffic in rows:
        if not search_term:
            continue
        kw_meta = keyword_meta.get(search_term, {})
        ad_traffic = 0
        organic_traffic = 0
        if or_rank and sp_rank:
            ad_traffic = int(traffic * 0.5)
            organic_traffic = traffic - ad_traffic
        elif sp_rank:
            ad_traffic = traffic
            organic_traffic = 0
        else:
            organic_traffic = traffic
            ad_traffic = 0
        normalized_rows.append({
            "parent_asin": asin,
            "child_asin": asin,
            "keyword": search_term,
            "relevance_level": kw_meta.get("relevance_level", "low"),
            "relevance_weight": float(kw_meta.get("relevance_weight", 0.0)),
            "keyword_traffic": traffic,
            "asin_keyword_share": 0.0,
            "organic_rank": or_rank or 0,
            "ad_rank": sp_rank or 0,
            "organic_traffic": organic_traffic,
            "ad_traffic": ad_traffic,
        })

    if not normalized_rows:
        print(f"[{asin}] no rows to persist")
        return None

    result = collector.persist_daily_source_batch(
        market_id="slip_lead_leash",
        run_date=run_date,
        collect_time_bj="2026-08-18 17:40:00",
        target_data_date=run_date,
        data_available_through=run_date,
        source_channel="mcp",
        source_provider="xydc",
        source_tool="xydc_get_asin_keywords",
        request_params={"asin": asin, "country": "US", "page_size": 50},
        response_status="success",
        write_mode=write_mode,
        normalized={"asin_keyword_snapshots": normalized_rows},
    )
    return {"asin": asin, "rows": len(normalized_rows), "result": result}


def load_payload(path: str) -> tuple[str, list]:
    p = Path(path)
    # Filename format: .tmp_r_akw_<ASIN>.json
    asin = p.stem.split('_')[-1]
    data = json.loads(p.read_text(encoding="utf-8"))
    rows = []
    for entry in data.get("data", {}).get("list", []):
        ranks = {r.get("position"): r.get("pageRank") for r in entry.get("ranks", []) if isinstance(r, dict)}
        or_rank = ranks.get("or")
        sp_rank = ranks.get("sp")
        traffic = entry.get("trafficSummary", {}).get("traffic", {}).get("total", 0)
        rows.append((entry["searchTerm"], or_rank, sp_rank, traffic))
    return asin, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", default="2026-08-18")
    parser.add_argument("--write-mode", default="upsert")
    parser.add_argument("--input-dir", default=".")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    payload_files = sorted(input_dir.glob(".tmp_r_akw_*.json"))
    print(f"Found {len(payload_files)} payload files")
    for pf in payload_files:
        try:
            asin, rows = load_payload(str(pf))
            result = persist_one(asin, rows, args.run_date, args.write_mode)
            if result:
                print(f"[{asin}] persisted {result['rows']} rows: status={result['result']['status']}")
        except Exception as e:
            print(f"[{pf}] FAILED: {e}")


if __name__ == "__main__":
    raise SystemExit(main())