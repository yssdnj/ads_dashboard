"""Presentation-ready DTO assembly for the market-monitor HTTP API."""

from __future__ import annotations

import json
import math
from numbers import Number
from typing import Any, Mapping, Sequence

from . import repository
from .analytics import (
    MetricComparison,
    analyze_keyword_opportunity,
    compare_metric,
    evaluate_dashboard_signal,
    prioritize_gap_actions,
    score_confidence,
)
from .constants import RELEVANCE_LEVELS


SCENARIO_METRICS = {
    "market_share": ("total_traffic", "market_share", "rank_in_market"),
    "keyword_competition": (
        "keyword_traffic",
        "organic_rank",
        "keyword_traffic_share",
    ),
    "ad_competition": ("ad_traffic", "ad_rank", "ad_traffic_share"),
    "growth_quality": ("weighted_traffic", "organic_traffic", "ad_traffic"),
}

_BASELINES = ["previous_complete_day", "latest_7_complete_days"]


def get_date_context(market_id: str) -> dict[str, Any]:
    """Return the range bootstrap dates before any dashboard query is issued."""
    quality = repository.get_latest_run_quality(market_id)
    available = repository.get_available_dates(market_id)
    latest_complete = quality.get("effective_data_available_through")
    if not latest_complete and quality.get("status") in {"success", "partial_success"}:
        latest_complete = quality.get("data_available_through")
    return {
        "market_id": market_id,
        "earliest_data_date": _iso_date(available.get("min_date")),
        "latest_complete_date": _iso_date(latest_complete),
        "latest_data_date": _iso_date(
            available.get("max_date") or quality.get("target_data_date")
        ),
    }


def get_keywords(
    market_id: str,
    relevance_level: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    rows = repository.list_keywords(market_id, relevance_level)
    return {
        "market_id": market_id,
        "relevance_level": relevance_level,
        "context": {
            "date_from": _iso_date(date_from),
            "date_to": _iso_date(date_to),
            "catalog_scope": "confirmed_static_catalog",
        },
        "keywords": [
            {**dict(row), "opportunity": analyze_keyword_opportunity(row, market_id=market_id)}
            for row in rows
        ],
    }


def validate_comparison(own_asin: str, competitors: list[str]) -> list[str]:
    unique = [asin for asin in dict.fromkeys(competitors) if asin and asin != own_asin]
    if len(unique) > 3:
        raise ValueError("at most three competitor ASINs are allowed")
    return unique


def get_dashboard(
    market_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    market_rows = _normalize_dates(
        repository.get_market_trend(market_id, date_from, date_to)
    )
    quality_row = repository.get_latest_run_quality(market_id, date_to)
    data_quality = _data_quality(quality_row)
    market_rows = _mark_completeness(
        market_rows, data_quality["effective_data_available_through"]
    )
    latest_data_date = _latest_complete_date(market_rows, date_to)

    own_asin = _find_own_asin(repository.list_asins(market_id))
    own_rows = _normalize_dates(
        repository.get_asin_trends(market_id, [own_asin], date_from, date_to)
        if own_asin
        else []
    )
    own_rows = _mark_completeness(
        own_rows, data_quality["effective_data_available_through"]
    )
    own_share_rows = _with_market_share(own_rows, market_rows)

    kpi_specs = (
        ("market_total_traffic", "Market total traffic", market_rows, "total_traffic"),
        ("own_market_share", "Own market share", own_share_rows, "market_share"),
        (
            "core_keyword_visibility",
            "Core-keyword visibility",
            own_rows,
            "high_keyword_share",
        ),
        (
            "competitive_pressure",
            "Competitive pressure",
            market_rows,
            "top_asin_share",
        ),
    )
    kpis = [
        _kpi_payload(kpi_id, label, rows, metric, date_to)
        for kpi_id, label, rows, metric in kpi_specs
    ]
    confidence_inputs = (
        data_quality["completeness_score"],
        data_quality["provider_coverage"],
    )
    evaluated_insights = [
        evaluate_dashboard_signal(
            item,
            completeness=confidence_inputs[0],
            provider_agreement=confidence_inputs[1],
        )
        for item in kpis
        if item["current"] is not None
    ]
    insights = [
        item for item in evaluated_insights if item["severity"] != "opportunity"
    ]
    opportunities = sorted(
        (
            item
            for item in evaluated_insights
            if item["severity"] == "opportunity"
        ),
        key=lambda item: -(item.get("priority_score") or 0),
    )
    anomaly_markers = [
        {
            "date": latest_data_date,
            "value": item["current"],
            "metric": item["metric"],
            "insight_id": item["insight_id"],
            "anomaly_score": item["anomaly_score"],
        }
        for item in insights
        if item["severity"] == "risk" and latest_data_date
    ]

    return {
        "context": {
            "market_id": market_id,
            "date_from": _iso_date(date_from),
            "date_to": _iso_date(date_to),
            "latest_data_date": latest_data_date,
            "own_asin": own_asin,
            "baselines": list(_BASELINES),
        },
        "data_quality": data_quality,
        "kpis": kpis,
        "trend": {
            "series": [
                _trend_series(
                    "market_total_traffic",
                    "Market total traffic",
                    market_rows,
                    "total_traffic",
                ),
                _trend_series(
                    "own_market_share",
                    "Own market share",
                    own_share_rows,
                    "market_share",
                ),
            ],
            "anomaly_markers": anomaly_markers,
            "collection_markers": (
                [{"date": data_quality["target_data_date"], "run_id": data_quality["run_id"]}]
                if data_quality["run_id"]
                else []
            ),
        },
        "insights": insights,
        "opportunities": opportunities,
    }


def get_comparison(
    market_id: str,
    own_asin: str,
    competitor_asins: list[str],
    scenario: str = "market_share",
    metric: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    own = _normalize_asin(own_asin)
    if not own:
        raise ValueError("own_asin is required")
    competitors = validate_comparison(
        own,
        [_normalize_asin(asin) for asin in competitor_asins],
    )
    if scenario not in SCENARIO_METRICS:
        raise ValueError(f"unknown comparison scenario: {scenario}")
    selected_metric = metric or SCENARIO_METRICS[scenario][0]
    if selected_metric not in SCENARIO_METRICS[scenario]:
        raise ValueError(
            f"metric '{selected_metric}' is not valid for scenario '{scenario}'"
        )

    child_asins = [own, *competitors]
    quality = _data_quality(repository.get_latest_run_quality(market_id, date_to))
    rows = _normalize_dates(
        repository.get_asin_trends(market_id, child_asins, date_from, date_to)
    )
    rows = _mark_completeness(
        rows, quality["effective_data_available_through"]
    )
    if scenario == "market_share":
        market_rows = _normalize_dates(
            repository.get_market_trend(market_id, date_from, date_to)
        )
        market_rows = _mark_completeness(
            market_rows, quality["effective_data_available_through"]
        )
        rows = _with_market_share(rows, market_rows)

    metric_availability = {
        available_metric: any(
            row.get("is_complete") is True
            and _number_or_none(row.get(available_metric)) is not None
            for row in rows
        )
        for available_metric in SCENARIO_METRICS[scenario]
    }
    metric_available = metric_availability[selected_metric]

    rows_by_asin: dict[str, list[dict[str, Any]]] = {
        asin: [] for asin in child_asins
    }
    for row in rows:
        child_asin = row.get("child_asin")
        if child_asin in rows_by_asin:
            rows_by_asin[child_asin].append(row)

    latest_data_date = _latest_complete_date(rows, date_to)
    gap_rows = (
        repository.get_gap_contributors(
            market_id, own, competitors, latest_data_date
        )
        if competitors and latest_data_date
        else []
    )
    gaps = []
    for row in gap_rows:
        item = dict(row)
        item["date"] = latest_data_date
        item["evidence_id"] = (
            f"asin-keyword:{market_id}:{latest_data_date}:{own}:"
            f"{item.get('competitor_asin')}:{item.get('keyword')}"
        )
        gaps.append(item)
    action_priorities = prioritize_gap_actions(
        gaps,
        completeness=quality["completeness_score"],
        provider_coverage=quality["provider_coverage"],
    )
    available_actions = [
        item for item in action_priorities if item["status"] == "available"
    ]
    action_priority_analysis = {
        "status": "available" if available_actions else "unavailable",
        "confidence": (
            max(item["confidence"] for item in available_actions)
            if available_actions
            else 0.0
        ),
        "unavailable_reason": (
            None
            if available_actions
            else "No positive evidence-backed keyword gaps are available for this range."
        ),
    }

    return {
        "context": {
            "market_id": market_id,
            "date_from": _iso_date(date_from),
            "date_to": _iso_date(date_to),
            "latest_data_date": latest_data_date,
            "own_asin": own,
            "competitor_asins": competitors,
            "scenario": scenario,
            "metric": selected_metric,
            "available_metrics": list(SCENARIO_METRICS[scenario]),
            "metric_availability": metric_availability,
            "metric_available": metric_available,
            "unavailable_reason": (
                None
                if metric_available
                else "This metric is unavailable in the stored complete-day snapshots."
            ),
            "baselines": list(_BASELINES),
            "effective_data_available_through": quality[
                "effective_data_available_through"
            ],
        },
        "objects": [
            {"asin": asin, "role": "own" if asin == own else "competitor"}
            for asin in child_asins
        ],
        "series": [
            {
                "asin": asin,
                "role": "own" if asin == own else "competitor",
                "metric": selected_metric,
                "points": [
                    {
                        "date": row["date"],
                        "value": _number_or_none(row.get(selected_metric)),
                        "is_complete": bool(row.get("is_complete", True)),
                        "evidence_ids": [
                            _evidence_id(row, f"asin:{asin}", selected_metric)
                        ],
                    }
                    for row in rows_by_asin[asin]
                ],
            }
            for asin in child_asins
        ],
        "comparisons": [
            {
                "asin": asin,
                **_comparison_payload(
                    compare_metric(rows_by_asin[asin], selected_metric, date_to)
                ),
                "evidence_ids": _evidence_ids(
                    _usable_metric_rows(
                        rows_by_asin[asin], selected_metric, date_to
                    ),
                    f"asin:{asin}",
                    selected_metric,
                ),
            }
            for asin in child_asins
        ],
        "gap_contributors": gaps,
        "action_priorities": action_priorities,
        "action_priority_analysis": action_priority_analysis,
    }


def get_keyword_summary(
    market_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    rows_by_level = {
        row["relevance_level"]: row
        for row in repository.get_keyword_level_summary(market_id)
        if row.get("relevance_level") in RELEVANCE_LEVELS
    }
    levels = []
    counts = {"all": 0, **{level: 0 for level in RELEVANCE_LEVELS}}
    for level in RELEVANCE_LEVELS:
        row = rows_by_level.get(level, {})
        keyword_count = int(row.get("keyword_count") or 0)
        counts[level] = keyword_count
        counts["all"] += keyword_count
        levels.append(
            {
                "relevance_level": level,
                "keyword_count": keyword_count,
                "search_volume": _number_or_zero(row.get("search_volume")),
                "category_search_volume": _number_or_zero(
                    row.get("category_search_volume")
                ),
                "average_relevance_weight": _number_or_none(
                    row.get("average_relevance_weight")
                ),
            }
        )
    trend = [
        {
            "date": _iso_date(row.get("date")),
            "relevance_level": row.get("relevance_level"),
            "keyword_count": int(row.get("keyword_count") or 0),
            "keyword_traffic": _number_or_zero(row.get("keyword_traffic")),
        }
        for row in repository.get_keyword_level_trend(market_id, date_from, date_to)
        if row.get("relevance_level") in RELEVANCE_LEVELS
    ]
    return {
        "context": {
            "market_id": market_id,
            "relevance_levels": list(RELEVANCE_LEVELS),
            "date_from": _iso_date(date_from),
            "date_to": _iso_date(date_to),
        },
        "counts": counts,
        "levels": levels,
        "trend": trend,
    }


def _find_own_asin(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        if row.get("is_own_product"):
            return _normalize_asin(str(row.get("asin") or "")) or None
    return None


def _with_market_share(
    asin_rows: Sequence[Mapping[str, Any]],
    market_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    market_traffic = {
        str(row.get("date")): _number_or_none(row.get("total_traffic"))
        for row in market_rows
    }
    enriched = []
    for original in asin_rows:
        row = dict(original)
        own_traffic = _number_or_none(row.get("total_traffic"))
        total_traffic = market_traffic.get(str(row.get("date")))
        row["market_share"] = (
            own_traffic / total_traffic
            if own_traffic is not None and total_traffic not in (None, 0)
            else None
        )
        enriched.append(row)
    return enriched


def _kpi_payload(
    kpi_id: str,
    label: str,
    rows: Sequence[Mapping[str, Any]],
    metric: str,
    current_date: str | None,
) -> dict[str, Any]:
    comparison = compare_metric(rows, metric, current_date)
    return {
        "id": kpi_id,
        "label": label,
        "metric": metric,
        "current": comparison.current,
        "previous_complete_day": {
            "value": comparison.previous,
            "change": comparison.previous_change,
        },
        "latest_7_complete_days": {
            "average": comparison.seven_day_average,
            "deviation": comparison.seven_day_deviation,
        },
        "baseline_sample_size": comparison.baseline_sample_size,
        "persistence_days": comparison.persistence_days,
        "evidence_ids": _evidence_ids(
            _usable_metric_rows(rows, metric, current_date), kpi_id, metric
        ),
    }


def _comparison_payload(comparison: MetricComparison) -> dict[str, Any]:
    return {
        "current": comparison.current,
        "previous_complete_day": {
            "value": comparison.previous,
            "change": comparison.previous_change,
        },
        "latest_7_complete_days": {
            "average": comparison.seven_day_average,
            "deviation": comparison.seven_day_deviation,
        },
        "baseline_sample_size": comparison.baseline_sample_size,
        "persistence_days": comparison.persistence_days,
    }


def _kpi_insight(
    kpi: Mapping[str, Any], completeness: float, provider_coverage: float
) -> dict[str, Any]:
    confidence = score_confidence(
        completeness, provider_coverage, int(kpi["persistence_days"])
    )
    return {
        "insight_id": f"{kpi['id']}:latest",
        "severity": "info",
        "scope": str(kpi["id"]),
        "metric": kpi["metric"],
        "current": kpi["current"],
        "previous_complete_day": dict(kpi["previous_complete_day"]),
        "latest_7_complete_days": dict(kpi["latest_7_complete_days"]),
        "persistence_days": int(kpi["persistence_days"]),
        "direction": _direction(kpi["previous_complete_day"].get("change")),
        "contributors": [],
        "provider_coverage": provider_coverage,
        "fact": f"{kpi['label']} is {kpi['current']} on the latest complete date.",
        "inference": None,
        "recommendation": None,
        "confidence": confidence,
        "evidence_ids": list(kpi["evidence_ids"]),
    }


def _direction(change: Any) -> str:
    number = _number_or_none(change)
    if number is None or number == 0:
        return "flat"
    return "up" if number > 0 else "down"


def _trend_series(
    series_id: str,
    label: str,
    rows: Sequence[Mapping[str, Any]],
    metric: str,
) -> dict[str, Any]:
    return {
        "id": series_id,
        "label": label,
        "metric": metric,
        "points": [
            {
                "date": row.get("date"),
                "value": _number_or_none(row.get(metric)),
                "is_complete": bool(row.get("is_complete", True)),
                "evidence_ids": [_evidence_id(row, series_id, metric)],
            }
            for row in rows
        ],
    }


def _data_quality(row: Mapping[str, Any]) -> dict[str, Any]:
    summary = _json_object(row.get("source_summary_json"))
    status = str(row.get("status") or "unavailable")
    providers_value = summary.get("providers")
    if isinstance(providers_value, list):
        providers = sorted({str(value) for value in providers_value if value})
    elif summary.get("source_provider"):
        providers = [str(summary["source_provider"])]
    else:
        providers = []
    completeness = _bounded_score(
        summary.get("completeness_score", summary.get("completeness")),
        default=1.0 if status == "success" else 0.0,
    )
    coverage = _bounded_score(
        summary.get("provider_coverage"),
        default=1.0 if status == "success" and providers else 0.0,
    )
    target_date = _iso_date(row.get("target_data_date"))
    available_date = _iso_date(row.get("data_available_through"))
    effective_available_date = _iso_date(
        row["effective_data_available_through"]
        if "effective_data_available_through" in row
        else row.get("data_available_through")
    )
    missing = summary.get("missing_data_types")
    return {
        "run_id": row.get("run_id"),
        "status": status,
        "collect_time_bj": _iso_date(row.get("collect_time_bj")),
        "collection_timezone": "Asia/Shanghai",
        "target_data_date": target_date,
        "data_available_through": available_date,
        "effective_data_available_through": effective_available_date,
        "providers": providers,
        "provider_coverage": coverage,
        "missing_data_types": list(missing) if isinstance(missing, list) else [],
        "completeness_score": completeness,
        "is_stale": bool(
            effective_available_date is None
            or (target_date and effective_available_date < target_date)
        ),
        "error_message": row.get("error_message"),
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _normalize_dates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for original in rows:
        row = dict(original)
        row["date"] = _iso_date(row.get("date"))
        normalized.append(row)
    return normalized


def _latest_complete_date(
    rows: Sequence[Mapping[str, Any]], current_date: str | None
) -> str | None:
    dates = [
        str(row["date"])
        for row in rows
        if row.get("date")
        and row.get("is_complete", True)
        and (current_date is None or str(row["date"]) <= current_date)
    ]
    return max(dates) if dates else None


def _mark_completeness(
    rows: Sequence[Mapping[str, Any]], available_through: str | None
) -> list[dict[str, Any]]:
    marked = []
    for original in rows:
        row = dict(original)
        row["is_complete"] = bool(
            available_through
            and row.get("date")
            and row.get("is_complete", True)
            and str(row["date"]) <= available_through
        )
        marked.append(row)
    return marked


def _usable_metric_rows(
    rows: Sequence[Mapping[str, Any]], metric: str, current_date: str | None
) -> list[Mapping[str, Any]]:
    return [
        row
        for row in rows
        if row.get("is_complete", True)
        and (current_date is None or str(row.get("date")) <= current_date)
        and _number_or_none(row.get(metric)) is not None
    ]


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _normalize_asin(value: str) -> str:
    return value.strip().upper()


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or isinstance(value, complex) or not isinstance(value, Number):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number_or_zero(value: Any) -> float:
    return _number_or_none(value) or 0.0


def _bounded_score(value: Any, default: float) -> float:
    number = _number_or_none(value)
    return min(max(number, 0.0), 1.0) if number is not None else default


def _evidence_id(row: Mapping[str, Any], scope: str, metric: str) -> str:
    source_request_id = row.get("source_request_id")
    if source_request_id:
        return str(source_request_id)
    return f"{scope}:{row.get('date')}:{metric}"


def _evidence_ids(
    rows: Sequence[Mapping[str, Any]], scope: str, metric: str
) -> list[str]:
    return list(dict.fromkeys(_evidence_id(row, scope, metric) for row in rows))
