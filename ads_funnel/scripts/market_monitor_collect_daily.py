"""Daily market monitor orchestration helpers.

This script intentionally does not call MCP directly. Codex automation can use
the emitted plan to call MCP tools, normalize the responses, and pass the
cleaned payload back through the `ingest` command. Future API collectors can
write the same payload shape without changing database persistence.
"""

from __future__ import annotations

import argparse
import json
import sys
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
DEFAULT_COUNTRY = "US"
DEFAULT_CORE_ASINS = [
    "B0D6G27DNH",
    "B09T3KS313",
    "B091TMYKHF",
    "B095775SWX",
    "B0BMQQ74H2",
]
USER_TASK = (
    "做 slip lead leash 市场每日监控系统：每天采集市场、ASIN、关键词、"
    "ASIN×关键词和关键词×ASIN竞品数据，xydc 为主，sif 和 sorftime 为辅，"
    "写入 MySQL 并供 ads_funnel dashboard 查询。"
)


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Slip lead leash daily market monitor.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Emit the MCP/API collection plan as JSON.")
    _add_common_args(plan_parser)
    plan_parser.add_argument("--country", default=DEFAULT_COUNTRY)
    plan_parser.add_argument("--core-asin", action="append", dest="core_asins")
    plan_parser.add_argument("--keyword-page-size", type=int, default=50)
    plan_parser.add_argument("--competition-page-size", type=int, default=20)
    plan_parser.add_argument("--include-weekly", action="store_true")

    ingest_parser = subparsers.add_parser("ingest", help="Persist normalized provider payload JSON.")
    ingest_parser.add_argument("--payload", required=True, help="Path to normalized payload JSON.")

    args = parser.parse_args()
    if args.command == "plan":
        plan = build_collection_plan(
            market_id=args.market_id,
            run_date=args.date,
            target_data_date=args.target_data_date,
            country=args.country,
            core_asins=args.core_asins or DEFAULT_CORE_ASINS,
            keyword_page_size=args.keyword_page_size,
            competition_page_size=args.competition_page_size,
            include_weekly=args.include_weekly,
        )
        print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
        return 0

    result = ingest_payload(Path(args.payload))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    today = datetime.now(BEIJING_TZ).date().isoformat()
    parser.add_argument("--market-id", default=db.DEFAULT_MARKET_ID)
    parser.add_argument("--date", default=today, help="Run date in YYYY-MM-DD.")
    parser.add_argument(
        "--target-data-date",
        default=today,
        help="Provider data date represented by this run. Defaults to run date.",
    )


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def build_collection_plan(
    *,
    market_id: str,
    run_date: str,
    target_data_date: str,
    country: str,
    core_asins: list[str],
    keyword_page_size: int,
    competition_page_size: int,
    include_weekly: bool,
) -> dict[str, Any]:
    db.init_market_db(seed_defaults=True)
    seed_asins = _active_seed_asins(market_id)
    keywords = _active_keywords(market_id)
    high_keywords = [row["keyword"] for row in keywords if row["relevance_level"] == "high"]
    monitored_keywords = [row["keyword"] for row in keywords]

    xydc_calls = [
        {
            "provider": "xydc",
            "tool": "get_asin_traffic",
            "cadence": "daily",
            "params": {
                "country": country,
                "asins": seed_asins,
                "intent_summary": "批量采集市场种子ASIN近7天流量快照",
                "user_task": USER_TASK,
            },
            "normalize_to": ["market_snapshot", "asin_snapshots"],
        },
        {
            "provider": "xydc",
            "tool": "get_asin_info",
            "cadence": "daily",
            "params": {
                "country": country,
                "asins": seed_asins,
                "intent_summary": "补全市场种子ASIN商品基础信息",
                "user_task": USER_TASK,
            },
            "normalize_to": ["asin_snapshots"],
        },
        {
            "provider": "xydc",
            "tool": "get_keyword_info",
            "cadence": "daily",
            "params": {
                "country": country,
                "keywords": monitored_keywords,
                "intent_summary": "批量采集监控关键词最近一周基础指标",
                "user_task": USER_TASK,
            },
            "normalize_to": ["keyword_snapshots"],
        },
    ]

    for asin in _ordered_unique(core_asins):
        xydc_calls.append(
            {
                "provider": "xydc",
                "tool": "get_asin_keywords",
                "cadence": "daily",
                "params": {
                    "country": country,
                    "asin": asin,
                    "page": 1,
                    "page_size": keyword_page_size,
                    "sort_field": "traffic",
                    "sort_order": "desc",
                    "intent_summary": "采集核心ASIN关键词流量来源",
                    "user_task": USER_TASK,
                },
                "normalize_to": ["asin_keyword_snapshots", "keyword_snapshots"],
            }
        )

    for keyword in high_keywords:
        xydc_calls.append(
            {
                "provider": "xydc",
                "tool": "get_keyword_asin_analysis",
                "cadence": "daily",
                "params": {
                    "country": country,
                    "keyword": keyword,
                    "page": 1,
                    "page_size": competition_page_size,
                    "sort_field": "traffic",
                    "sort_order": "desc",
                    "intent_summary": "采集强相关关键词下的ASIN竞争矩阵",
                    "user_task": USER_TASK,
                },
                "normalize_to": ["keyword_asin_competition"],
            }
        )

    if include_weekly:
        for asin in seed_asins:
            xydc_calls.append(
                {
                    "provider": "xydc",
                    "tool": "get_asin_variations",
                    "cadence": "weekly",
                    "params": {
                        "country": country,
                        "asin": asin,
                        "intent_summary": "更新种子ASIN父子变体关系",
                        "user_task": USER_TASK,
                    },
                    "normalize_to": ["listing_variations"],
                }
            )

    return {
        "market_id": market_id,
        "run_date": run_date,
        "target_data_date": target_data_date,
        "country": country,
        "generated_at_bj": datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
        "source_priority": ["xydc", "sif", "sorftime"],
        "write_contract": {
            "entrypoint": "ads_funnel.api.market_monitor_collector.persist_daily_source_batch",
            "default_write_mode": "upsert",
        },
        "primary_calls": xydc_calls,
        "auxiliary_calls": _auxiliary_calls(country, core_asins, high_keywords),
        "notes": [
            "普通 Python 定时任务不能直接调用 Codex MCP；Codex cron automation 应读取此 plan 后调用 MCP。",
            "xydc 负责主数据闭环；sif/sorftime 先作为补充校验或缺口分析来源。",
            "父子变体变化慢，默认不每日全量采集；需要时传 --include-weekly。",
        ],
    }


def ingest_payload(payload_path: Path) -> dict[str, Any]:
    with payload_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    batches = payload.get("batches") if isinstance(payload, dict) else None
    if batches is None:
        batches = [payload]

    results = []
    for batch in batches:
        results.append(collector.persist_daily_source_batch(**batch))
    return {"batch_count": len(results), "results": results}


def _active_seed_asins(market_id: str) -> list[str]:
    rows = db.list_asins(market_id)
    return _ordered_unique(row["asin"].upper() for row in rows if row.get("status") == "active")


def _active_keywords(market_id: str) -> list[dict[str, Any]]:
    return [row for row in db.list_keywords(market_id) if row.get("status") == "active"]


def _auxiliary_calls(country: str, core_asins: list[str], high_keywords: list[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for asin in _ordered_unique(core_asins):
        calls.extend(
            [
                {
                    "provider": "sorftime",
                    "tool": "product_detail",
                    "cadence": "fallback_or_weekly",
                    "params": {"amz_site": country, "asin": asin},
                    "purpose": "校验标题、价格、评分、变体等商品基础信息。",
                },
                {
                    "provider": "sorftime",
                    "tool": "product_traffic_terms",
                    "cadence": "fallback_or_weekly",
                    "params": {"amz_site": country, "asin": asin, "page": 1},
                    "purpose": "辅助校验核心ASIN自然曝光关键词。",
                },
            ]
        )
    for keyword in high_keywords[:5]:
        calls.append(
            {
                "provider": "sorftime",
                "tool": "product_ranking_trend_by_keyword",
                "cadence": "fallback_or_weekly",
                "params_template": {"amz_site": country, "keyword": keyword, "asin": "<core_asin>", "page": 1},
                "purpose": "辅助校验核心ASIN在强相关词下的自然排名趋势。",
            }
        )
    calls.append(
        {
            "provider": "sif",
            "tool": "traffic_or_ad_diagnostics",
            "cadence": "fallback_or_weekly",
            "params_template": {"country": country, "asin": "<core_asin>"},
            "purpose": "当 xydc 流量异常或广告/自然拆分需要复核时，用 SIF 做辅助诊断。",
        }
    )
    return calls


def _ordered_unique(values) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


if __name__ == "__main__":
    raise SystemExit(main())
