"""Persist xydc MCP responses for today's Slip Lead Leash daily monitor.

Usage:
    python ads_funnel/scripts/persist_xydc_daily.py \\
        --tool xydc_get_asin_traffic --response-file <path> \\
        --tool xydc_get_asin_info   --response-file <path> \\
        ...

The script accepts multiple `--tool/--response-file` pairs. For the asin
snapshot sections the script merges traffic and info rows in memory before
calling `collector.persist_daily_source_batch`, because the table PK is
(date, market_id, child_asin) and a later REPLACE would wipe earlier columns.

The asin_keyword and keyword_asin_competition sections accumulate multiple
tool responses into a single normalized list before persisting, so the script
keeps the run count low while still tolerating per-call MCP failures.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ads_funnel.api import market_monitor_collector as collector  # noqa: E402
from ads_funnel.api import market_monitor_db as db  # noqa: E402

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PROVIDER = "xydc"
SOURCE_CHANNEL = "mcp"

TOOL_ASIN_TRAFFIC = "xydc_get_asin_traffic"
TOOL_ASIN_INFO = "xydc_get_asin_info"
TOOL_KEYWORD_INFO = "xydc_get_keyword_info"
TOOL_ASIN_KEYWORDS = "xydc_get_asin_keywords"
TOOL_KEYWORD_ASIN_ANALYSIS = "xydc_get_keyword_asin_analysis"

ALL_TOOLS = {
    TOOL_ASIN_TRAFFIC,
    TOOL_ASIN_INFO,
    TOOL_KEYWORD_INFO,
    TOOL_ASIN_KEYWORDS,
    TOOL_KEYWORD_ASIN_ANALYSIS,
}


def _beijing_now() -> str:
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist xydc daily monitor batch.")
    parser.add_argument("--market-id", default=db.DEFAULT_MARKET_ID)
    parser.add_argument("--target-data-date", required=True)
    parser.add_argument("--run-date", default=None,
                        help="Defaults to --target-data-date.")
    parser.add_argument("--collect-time-bj", default=_beijing_now())
    parser.add_argument("--write-mode", default="upsert",
                        choices=("upsert", "replace_day"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate + normalize but skip persistence.")
    parser.add_argument("--tool", action="append", dest="tools", required=True,
                        choices=sorted(ALL_TOOLS))
    parser.add_argument("--response-file", action="append",
                        dest="response_files", required=True)
    args = parser.parse_args()

    if len(args.tools) != len(args.response_files):
        parser.error("--tool and --response-file must appear in pairs (same count)")

    run_date = args.run_date or args.target_data_date

    keyword_meta = _load_keyword_meta(args.market_id)
    asin_meta = _load_asin_meta(args.market_id)

    asin_snapshot_rows: dict[str, dict[str, Any]] = {}
    keyword_snapshot_rows: list[dict[str, Any]] = []
    asin_keyword_rows: list[dict[str, Any]] = []
    keyword_asin_competition_rows: list[dict[str, Any]] = []
    request_params: list[dict[str, Any]] = []

    for tool, path in zip(args.tools, args.response_files):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        data = payload.get("data", payload)
        request_params.append({"tool": tool, "file": str(path)})
        if tool == TOOL_ASIN_TRAFFIC:
            for asin_row in _normalize_asin_traffic(data, args.market_id,
                                                    args.target_data_date,
                                                    asin_meta):
                _merge_row(asin_snapshot_rows, asin_row)
        elif tool == TOOL_ASIN_INFO:
            for asin_row in _normalize_asin_info(data, args.market_id,
                                                 args.target_data_date,
                                                 asin_meta):
                _merge_row(asin_snapshot_rows, asin_row)
        elif tool == TOOL_KEYWORD_INFO:
            keyword_snapshot_rows.extend(_normalize_keyword_info(
                data, args.market_id, args.target_data_date, keyword_meta))
        elif tool == TOOL_ASIN_KEYWORDS:
            asin_keyword_rows.extend(_normalize_asin_keywords(
                data, args.market_id, args.target_data_date, asin_meta,
                keyword_meta))
        elif tool == TOOL_KEYWORD_ASIN_ANALYSIS:
            keyword_asin_competition_rows.extend(_normalize_keyword_asin_analysis(
                data, args.market_id, args.target_data_date, asin_meta,
                keyword_meta))
        else:
            raise ValueError(f"unhandled tool: {tool}")

    normalized: dict[str, Any] = {}
    if asin_snapshot_rows:
        normalized["asin_snapshots"] = list(asin_snapshot_rows.values())
    if keyword_snapshot_rows:
        normalized["keyword_snapshots"] = keyword_snapshot_rows
    if asin_keyword_rows:
        normalized["asin_keyword_snapshots"] = asin_keyword_rows
    if keyword_asin_competition_rows:
        normalized["keyword_asin_competition"] = keyword_asin_competition_rows

    counts = {k: len(v) if isinstance(v, list) else (1 if v else 0)
              for k, v in normalized.items()}
    print(f"[plan] sections: {counts}")
    if not normalized:
        print("[plan] empty normalized payload; nothing to persist.")
        return 0
    if args.dry_run:
        print("[dry-run] skipped persist_daily_source_batch call.")
        return 0

    result = collector.persist_daily_source_batch(
        market_id=args.market_id,
        run_date=run_date,
        collect_time_bj=args.collect_time_bj,
        target_data_date=args.target_data_date,
        data_available_through=args.target_data_date,
        source_channel=SOURCE_CHANNEL,
        source_provider=PROVIDER,
        source_tool=",".join(args.tools),
        request_params={"calls": request_params},
        response_json=None,
        response_status="success",
        write_mode=args.write_mode,
        normalized=normalized,
    )
    print(json.dumps({"result": result, "counts": counts},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _load_keyword_meta(market_id: str) -> dict[str, dict[str, Any]]:
    return {row["keyword"]: row for row in db.list_keywords(market_id)
            if row.get("status") == "active"}


def _load_asin_meta(market_id: str) -> dict[str, dict[str, Any]]:
    return {row["asin"].upper(): row for row in db.list_asins(market_id)
            if row.get("status") == "active"}


def _normalize_asin_traffic(data: Any, market_id: str, date: str,
                            asin_meta: dict[str, dict[str, Any]]
                            ) -> list[dict[str, Any]]:
    """xydc get_asin_traffic -> asin_snapshots (traffic columns only)."""
    entities = (data or {}).get("entities", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for entity in entities:
        asin = entity.get("asin", "").upper()
        meta = asin_meta.get(asin, {})
        total_traffic = entity.get("totalTrafficScore")
        organic_traffic = entity.get("organicTrafficScore")
        ad_traffic = entity.get("advertisingTrafficScore")
        organic_share = entity.get("organicTrafficScoreRatio") or 0
        ad_share = entity.get("advertisingTrafficScoreRatio") or 0
        # weighted_traffic = organic * 1 + ad * 0.5 (mirrors test fixture convention)
        weighted = (organic_traffic or 0) + (ad_traffic or 0) * 0.5
        rows.append({
            "parent_asin": asin,
            "child_asin": asin,
            "brand": None,
            "title": None,
            "price": None,
            "rating": None,
            "reviews": None,
            "total_traffic": total_traffic if total_traffic is not None else 0,
            "organic_traffic": organic_traffic if organic_traffic is not None else 0,
            "ad_traffic": ad_traffic if ad_traffic is not None else 0,
            "weighted_traffic": weighted,
            "high_keyword_share": 0,
            "mid_keyword_share": 0,
            "low_keyword_share": 0,
            "rank_in_market": None,
            "is_own_product": int(bool(meta.get("is_own_product"))),
        })
    return rows


def _normalize_asin_info(data: Any, market_id: str, date: str,
                         asin_meta: dict[str, dict[str, Any]]
                         ) -> list[dict[str, Any]]:
    """xydc get_asin_info -> asin_snapshots (info columns only)."""
    entities = (data or {}).get("entities", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for entity in entities:
        asin = entity.get("asin", "").upper()
        meta = asin_meta.get(asin, {})
        rows.append({
            "parent_asin": asin,
            "child_asin": asin,
            "brand": entity.get("brand"),
            "title": entity.get("title"),
            "image_url": entity.get("bigPicUrl") or entity.get("smallPicUrl"),
            "price": _to_float(entity.get("price")),
            "rating": _to_float(entity.get("stars")),
            "reviews": _to_int(entity.get("ratings")),
            "total_traffic": None,
            "organic_traffic": None,
            "ad_traffic": None,
            "weighted_traffic": None,
            "high_keyword_share": None,
            "mid_keyword_share": None,
            "low_keyword_share": None,
            "rank_in_market": None,
            "is_own_product": int(bool(meta.get("is_own_product"))),
        })
    return rows


def _normalize_keyword_info(data: Any, market_id: str, date: str,
                            keyword_meta: dict[str, dict[str, Any]]
                            ) -> list[dict[str, Any]]:
    """xydc get_keyword_info -> keyword_snapshots.

    xydc returns ``{"list": [{searchTerm, abaReport, ...}, ...], "total": N}``.
    """
    items: list[dict[str, Any]] = []
    if isinstance(data, dict):
        items = data.get("list") or data.get("keywords") or []
    elif isinstance(data, list):
        items = data
    rows: list[dict[str, Any]] = []
    for entry in items:
        keyword = entry.get("searchTerm") or entry.get("keyword") or entry.get("term")
        if not keyword:
            continue
        meta = keyword_meta.get(keyword, {})
        aba = entry.get("abaReport") or {}
        search_volume = _to_float(aba.get("weeklySearchVolume"))
        cpc_obj = entry.get("costPerClick") or {}
        rows.append({
            "keyword": keyword,
            "relevance_level": meta.get("relevance_level", "low"),
            "relevance_weight": _to_float(meta.get("relevance_weight"),
                                          default=0.0),
            "search_volume": search_volume if search_volume is not None else 0,
            "aba_rank": _to_int(aba.get("searchFrequencyRank")
                                or entry.get("abaRank")
                                or entry.get("rank"), default=0),
            "cpc": _to_float(cpc_obj.get("value") if isinstance(cpc_obj, dict)
                             else entry.get("cpc"), default=0.0),
            "competitive_difficulty": _to_float(entry.get("competitiveDifficulty")
                                                or entry.get("difficulty"),
                                                default=0.0),
            "click_conversion_rate": _to_float(entry.get("clickConversionRate")
                                                 or entry.get("cvr"),
                                                 default=0.0),
            "total_market_traffic": search_volume if search_volume is not None else 0,
            "market_traffic_share": 0,
        })
    return rows


def _normalize_asin_keywords(data: Any, market_id: str, date: str,
                             asin_meta: dict[str, dict[str, Any]],
                             keyword_meta: dict[str, dict[str, Any]]
                             ) -> list[dict[str, Any]]:
    """xydc get_asin_keywords -> asin_keyword_snapshots."""
    # xydc returns a list of {keyword, searchVolume, ...} or nested entities.
    rows: list[dict[str, Any]] = []
    parent_asin = None
    items: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for ent in data.get("entities", []) or []:
            parent_asin = ent.get("asin", parent_asin)
            items.extend(ent.get("keywords", []) or [])
        if not items:
            items = data.get("keywords", []) or data.get("items", []) or []
    elif isinstance(data, list):
        items = data

    if not parent_asin:
        parent_asin = "UNKNOWN"
    parent_asin = parent_asin.upper()
    asin_own = int(bool(asin_meta.get(parent_asin, {}).get("is_own_product")))

    for entry in items:
        keyword = entry.get("keyword") or entry.get("term")
        if not keyword:
            continue
        meta = keyword_meta.get(keyword, {})
        traffic = _to_float(entry.get("searchVolume") or entry.get("traffic"))
        rows.append({
            "parent_asin": parent_asin,
            "child_asin": parent_asin,
            "keyword": keyword,
            "relevance_level": meta.get("relevance_level", "low"),
            "relevance_weight": _to_float(meta.get("relevance_weight"),
                                          default=0.0),
            "keyword_traffic": traffic,
            "asin_keyword_share": None,
            "organic_rank": _to_int(entry.get("organicRank")),
            "ad_rank": _to_int(entry.get("adRank") or entry.get("sponsoredRank")),
            "organic_traffic": None,
            "ad_traffic": None,
        })
    return rows


def _normalize_keyword_asin_analysis(data: Any, market_id: str, date: str,
                                     asin_meta: dict[str, dict[str, Any]],
                                     keyword_meta: dict[str, dict[str, Any]]
                                     ) -> list[dict[str, Any]]:
    """xydc get_keyword_asin_analysis -> keyword_asin_competition."""
    rows: list[dict[str, Any]] = []
    keyword = None
    items: list[dict[str, Any]] = []
    if isinstance(data, dict):
        keyword = data.get("keyword")
        for ent in data.get("entities", []) or []:
            keyword = ent.get("keyword", keyword)
            items.extend(ent.get("asins", []) or ent.get("items", []) or [])
        if not items:
            items = data.get("asins", []) or data.get("items", []) or []
    elif isinstance(data, list):
        items = data

    if not keyword:
        keyword = "UNKNOWN"
    meta = keyword_meta.get(keyword, {})

    for idx, entry in enumerate(items, start=1):
        asin = (entry.get("asin") or entry.get("childAsin") or "").upper()
        if not asin:
            continue
        asin_row = asin_meta.get(asin, {})
        traffic = _to_float(entry.get("searchVolume") or entry.get("traffic"))
        rows.append({
            "keyword": keyword,
            "relevance_level": meta.get("relevance_level", "low"),
            "relevance_weight": _to_float(meta.get("relevance_weight"),
                                          default=0.0),
            "parent_asin": asin,
            "child_asin": asin,
            "brand": entry.get("brand"),
            "title": entry.get("title"),
            "competition_rank": _to_int(entry.get("rank")) or idx,
            "keyword_traffic": traffic,
            "keyword_traffic_share": None,
            "organic_rank": _to_int(entry.get("organicRank")),
            "ad_rank": _to_int(entry.get("adRank") or entry.get("sponsoredRank")),
            "organic_traffic": None,
            "ad_traffic": None,
            "is_seed_asin": int(bool(asin_row.get("is_seed_asin"))),
            "is_own_product": int(bool(asin_row.get("is_own_product"))),
        })
    return rows


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _merge_row(bucket: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    """Merge `row` into `bucket[child_asin]` keeping existing non-None values."""
    key = row["child_asin"]
    existing = bucket.get(key, {})
    merged = dict(existing)
    for k, v in row.items():
        if v is not None or k not in merged:
            merged[k] = v
    # Fill schema NOT NULL fields with 0 if absent so REPLACE INTO never sees NULL.
    for field in ("high_keyword_share", "mid_keyword_share", "low_keyword_share"):
        merged.setdefault(field, 0)
    bucket[key] = merged


if __name__ == "__main__":
    raise SystemExit(main())