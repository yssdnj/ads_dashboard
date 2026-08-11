"""Pure metric comparison and insight confidence helpers."""

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from numbers import Number
from types import MappingProxyType
from typing import Mapping, Sequence, cast


# Centralized analysis rules. These weights are intentionally backend-only so UI
# code cannot silently change ranking semantics. Missing optional dimensions are
# omitted and the remaining weights are re-normalized; required dimensions fail
# closed instead of manufacturing a score.
KEYWORD_OPPORTUNITY_WEIGHTS = MappingProxyType(
    {"demand": 0.35, "relevance": 0.30, "attainability": 0.20, "conversion": 0.15}
)
KEYWORD_SEARCH_VOLUME_REFERENCE = 50_000.0
COMPETITIVE_DIFFICULTY_SCALE = 100.0
KEYWORD_REQUIRED_DIMENSIONS = ("demand", "relevance")

# Gap priority combines the observed traffic impact with relevance-based
# actionability. Confidence is reported separately and gates recommendations.
GAP_ACTION_WEIGHTS = MappingProxyType({"impact": 0.55, "actionability": 0.45})
GAP_RELEVANCE_ACTIONABILITY = MappingProxyType(
    {"high": 0.90, "mid": 0.65, "low": 0.40}
)
MIN_ACTION_CONFIDENCE = 0.60

# Dashboard signals require agreement between both default baselines and a
# persistent direction. Ratios are deliberately defined here, not in service or
# JavaScript, so operational tuning has one reviewable source of truth.
DASHBOARD_SIGNAL_RULES = MappingProxyType(
    {
        "market_total_traffic": MappingProxyType(
            {
                "risk_direction": -1,
                "previous_threshold": 0.15,
                "seven_day_threshold": 0.10,
                "risk_inference": "Market demand may be weakening across complete-day observations.",
                "risk_recommendation": "Review source coverage, keyword demand, and leading ASIN traffic before changing spend.",
                "opportunity_inference": "Market demand may be expanding across complete-day observations.",
                "opportunity_recommendation": "Review inventory and qualified traffic coverage for the expanding demand.",
            }
        ),
        "own_market_share": MappingProxyType(
            {
                "risk_direction": -1,
                "previous_threshold": 0.10,
                "seven_day_threshold": 0.08,
                "risk_inference": "The own product may be losing traffic share relative to the monitored market.",
                "risk_recommendation": "Inspect competitor and keyword gap evidence before adjusting bids or content.",
                "opportunity_inference": "The own product may be gaining traffic share relative to the monitored market.",
                "opportunity_recommendation": "Identify the keywords and placements contributing to the share gain.",
            }
        ),
        "core_keyword_visibility": MappingProxyType(
            {
                "risk_direction": -1,
                "previous_threshold": 0.10,
                "seven_day_threshold": 0.08,
                "risk_inference": "Visibility on confirmed high-relevance keywords may be deteriorating.",
                "risk_recommendation": "Review organic rank, ad rank, and content coverage for high-relevance keywords.",
                "opportunity_inference": "Visibility on confirmed high-relevance keywords may be improving.",
                "opportunity_recommendation": "Review which high-relevance keywords can sustain the visibility gain.",
            }
        ),
        "competitive_pressure": MappingProxyType(
            {
                "risk_direction": 1,
                "previous_threshold": 0.10,
                "seven_day_threshold": 0.08,
                "risk_inference": "Traffic concentration among leading ASINs may be increasing competitive pressure.",
                "risk_recommendation": "Inspect leading-ASIN and keyword contribution evidence before responding.",
                "opportunity_inference": "Traffic concentration among leading ASINs may be easing.",
                "opportunity_recommendation": "Review attainable keyword gaps while competitive concentration is lower.",
            }
        ),
    }
)
DASHBOARD_MIN_BASELINE_DAYS = 3
DASHBOARD_MIN_PERSISTENCE_DAYS = 2
DASHBOARD_MIN_CONFIDENCE = 0.60


@dataclass(frozen=True)
class MetricComparison:
    current: float | None
    previous: float | None
    previous_change: float | None
    seven_day_average: float | None
    seven_day_deviation: float | None
    baseline_sample_size: int
    persistence_days: int


@dataclass(frozen=True)
class Insight:
    insight_id: str
    severity: str
    scope: str
    fact: str
    inference: str | None
    recommendation: str | None
    confidence: float
    evidence: Sequence[dict]


class FrozenDict(dict):
    """A dict-compatible mapping that refuses all normal mutation operations."""

    @staticmethod
    def _immutable(*args: object, **kwargs: object) -> None:
        raise TypeError("FrozenDict is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def copy(self) -> "FrozenDict":
        return self


def _validated_number(
    value: object, *, minimum: float, maximum: float | None = None
) -> float | None:
    numeric = _as_finite_float(value)
    if numeric is None or numeric < minimum:
        return None
    if maximum is not None and numeric > maximum:
        return None
    return numeric


def _dimension(value: object, score: float | None) -> dict:
    return {
        "available": score is not None,
        "value": _as_finite_float(value),
        "score": round(score * 100, 1) if score is not None else None,
    }


def analyze_keyword_opportunity(
    row: Mapping[str, object], *, market_id: str
) -> dict:
    """Return an evidence-backed keyword opportunity DTO.

    Demand is capped at the documented 50k reference, relevance is the confirmed
    catalog weight, attainability inverses competitive difficulty, and conversion
    uses the source click-conversion rate. Required demand/relevance absence makes
    the score unavailable. Confidence equals the share of configured weight backed
    by source values and therefore declines when optional inputs are missing.
    """
    search_volume = _validated_number(row.get("search_volume"), minimum=0)
    relevance = _validated_number(
        row.get("relevance_weight"), minimum=0, maximum=1
    )
    difficulty_value = _validated_number(
        row.get("competitive_difficulty"),
        minimum=0,
        maximum=COMPETITIVE_DIFFICULTY_SCALE,
    )
    conversion = _validated_number(
        row.get("click_conversion_rate"), minimum=0, maximum=1
    )
    cpc = _validated_number(row.get("cpc"), minimum=0)
    validated_inputs = {
        "search_volume": search_volume,
        "relevance_weight": relevance,
        "competitive_difficulty": difficulty_value,
        "click_conversion_rate": conversion,
        "cpc": cpc,
    }
    invalid_inputs = [
        name
        for name, value in validated_inputs.items()
        if row.get(name) is not None and value is None
    ]
    demand = (
        min(search_volume / KEYWORD_SEARCH_VOLUME_REFERENCE, 1.0)
        if search_volume is not None
        else None
    )
    difficulty = (
        difficulty_value / COMPETITIVE_DIFFICULTY_SCALE
        if difficulty_value is not None
        else None
    )
    attainability = None if difficulty is None else 1.0 - difficulty
    values = {
        "demand": demand,
        "relevance": relevance,
        "attainability": attainability,
        "conversion": conversion,
    }
    dimensions = {
        "demand": _dimension(row.get("search_volume"), demand),
        "relevance": _dimension(row.get("relevance_weight"), relevance),
        "attainability": _dimension(row.get("competitive_difficulty"), attainability),
        "conversion": _dimension(row.get("click_conversion_rate"), conversion),
    }
    available_weight = sum(
        KEYWORD_OPPORTUNITY_WEIGHTS[name]
        for name, value in values.items()
        if value is not None
    )
    missing_required = [name for name in KEYWORD_REQUIRED_DIMENSIONS if values[name] is None]
    evidence_id = str(row.get("source_request_id") or "").strip()
    if not evidence_id and row.get("keyword"):
        evidence_id = f"keyword-config:{market_id}:{row['keyword']}"
    evidence_ids = [evidence_id] if evidence_id else []
    if invalid_inputs:
        return {
            "status": "unavailable",
            "score": None,
            "impact": "unavailable",
            "actionability": "unavailable",
            "confidence": 0.0,
            "dimensions": dimensions,
            "recommended_action": None,
            "evidence_ids": evidence_ids,
            "unavailable_reason": "Invalid source dimensions: " + ", ".join(invalid_inputs),
        }
    confidence = round(min(max(available_weight, 0.0), 1.0), 3)
    if missing_required or available_weight <= 0:
        return {
            "status": "unavailable",
            "score": None,
            "impact": "unavailable",
            "actionability": "unavailable",
            "confidence": confidence,
            "dimensions": dimensions,
            "recommended_action": None,
            "evidence_ids": evidence_ids,
            "unavailable_reason": "Missing required source dimensions: " + ", ".join(missing_required),
        }
    weighted_score = sum(
        KEYWORD_OPPORTUNITY_WEIGHTS[name] * value
        for name, value in values.items()
        if value is not None
    ) / available_weight
    score = round(weighted_score * 100, 1)
    impact = "high" if score >= 70 else "medium" if score >= 45 else "low"
    actionability_score = dimensions["attainability"]["score"]
    actionability = (
        "high" if actionability_score is not None and actionability_score >= 60
        else "medium" if actionability_score is not None and actionability_score >= 35
        else "low" if actionability_score is not None
        else "unavailable"
    )
    return {
        "status": "available",
        "score": score,
        "impact": impact,
        "actionability": actionability,
        "confidence": confidence,
        "dimensions": dimensions,
        "recommended_action": (
            f"Review keyword coverage for {row.get('keyword')}."
            if confidence >= MIN_ACTION_CONFIDENCE
            else None
        ),
        "evidence_ids": evidence_ids,
        "unavailable_reason": None,
    }


def prioritize_gap_actions(
    rows: Sequence[Mapping[str, object]],
    *,
    completeness: float,
    provider_coverage: float,
) -> list[dict]:
    """Rank deep-comparison gaps using backend impact/actionability evidence."""
    normalized_completeness = _validated_number(
        completeness, minimum=0, maximum=1
    )
    normalized_coverage = _validated_number(
        provider_coverage, minimum=0, maximum=1
    )
    invalid_quality = normalized_completeness is None or normalized_coverage is None
    actions = []
    for row in rows:
        gap = _as_finite_float(row.get("traffic_gap"))
        competitor_traffic = _as_finite_float(row.get("competitor_traffic"))
        own_traffic = _as_finite_float(row.get("own_traffic"))
        relevance = str(row.get("relevance_level") or "")
        actionability_ratio = GAP_RELEVANCE_ACTIONABILITY.get(relevance)
        evidence_id = str(row.get("evidence_id") or "").strip()
        evidence_ids = [evidence_id] if evidence_id else []
        invalid_row = (
            (row.get("traffic_gap") is not None and (gap is None or gap < 0))
            or (
                row.get("competitor_traffic") is not None
                and (competitor_traffic is None or competitor_traffic < 0)
            )
            or (
                row.get("own_traffic") is not None
                and (own_traffic is None or own_traffic < 0)
            )
            or (
                gap is not None
                and competitor_traffic is not None
                and gap > competitor_traffic
            )
            or (bool(relevance) and actionability_ratio is None)
        )
        confidence = (
            0.0
            if invalid_quality or invalid_row
            else round(
                0.4 * normalized_completeness
                + 0.4 * normalized_coverage
                + 0.2 * (1.0 if evidence_ids else 0.0),
                3,
            )
        )
        common = {
            "keyword": row.get("keyword"),
            "competitor_asin": row.get("competitor_asin"),
            "confidence": confidence,
            "evidence_ids": evidence_ids,
        }
        if (
            invalid_quality
            or invalid_row
            or gap is None
            or competitor_traffic is None
            or competitor_traffic <= 0
            or gap <= 0
            or actionability_ratio is None
        ):
            actions.append(
                {
                    **common,
                    "status": "unavailable",
                    "priority": "unavailable",
                    "priority_score": None,
                    "impact": {"score": None, "traffic_gap": gap},
                    "actionability": {"score": None, "relevance_level": relevance or None},
                    "recommended_action": None,
                    "unavailable_reason": (
                        "Invalid quality or gap-analysis inputs."
                        if invalid_quality or invalid_row
                        else "Insufficient positive gap, competitor traffic, or relevance evidence."
                    ),
                }
            )
            continue
        impact_ratio = gap / competitor_traffic
        priority_score = round(
            (
                GAP_ACTION_WEIGHTS["impact"] * impact_ratio
                + GAP_ACTION_WEIGHTS["actionability"] * actionability_ratio
            )
            * 100,
            1,
        )
        priority = "high" if priority_score >= 75 else "medium" if priority_score >= 50 else "low"
        actions.append(
            {
                **common,
                "status": "available",
                "priority": priority,
                "priority_score": priority_score,
                "impact": {"score": round(impact_ratio * 100, 1), "traffic_gap": gap},
                "actionability": {"score": round(actionability_ratio * 100, 1), "relevance_level": relevance},
                "recommended_action": (
                    f"Review organic, advertising, and content coverage for {row.get('keyword')}."
                    if confidence >= MIN_ACTION_CONFIDENCE
                    else None
                ),
                "unavailable_reason": None,
            }
        )
    return sorted(
        actions,
        key=lambda item: (
            item["status"] != "available",
            -(item["priority_score"] or 0),
            str(item.get("keyword") or ""),
        ),
    )


def _as_finite_float(value: object) -> float | None:
    """Safely convert finite non-boolean, non-complex numeric values to float."""
    if isinstance(value, bool) or isinstance(value, complex) or not isinstance(value, Number):
        return None
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return converted if isfinite(converted) else None


def _ratio_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline in (None, 0):
        return None
    return (current - baseline) / abs(baseline)


def compare_metric(
    rows: Sequence[Mapping[str, object]], metric: str, current_date: str | None = None
) -> MetricComparison:
    """Compare the latest complete metric value with prior complete-day data."""
    complete_by_date: dict[str, float] = {}
    for row in rows:
        value = _as_finite_float(row.get(metric))
        if not row.get("is_complete", True) or value is None:
            continue
        date = str(row["date"])
        if current_date and date > current_date:
            continue
        if date in complete_by_date:
            raise ValueError(f"duplicate complete date: {date}")
        complete_by_date[date] = value

    complete = sorted(complete_by_date.items())

    if not complete:
        return MetricComparison(None, None, None, None, None, 0, 0)

    current = complete[-1][1]
    previous = complete[-2][1] if len(complete) > 1 else None
    baseline_rows = complete[max(0, len(complete) - 8) : -1]
    average = (
        sum(value for _, value in baseline_rows) / len(baseline_rows)
        if baseline_rows
        else None
    )

    direction = (
        0
        if previous is None
        else 1
        if current > previous
        else -1
        if current < previous
        else 0
    )
    persistence = 0
    for left, right in zip(reversed(complete[:-1]), reversed(complete[1:])):
        step = (
            1
            if right[1] > left[1]
            else -1
            if right[1] < left[1]
            else 0
        )
        if direction == 0 or step != direction:
            break
        persistence += 1

    return MetricComparison(
        current=current,
        previous=previous,
        previous_change=_ratio_change(current, previous),
        seven_day_average=average,
        seven_day_deviation=_ratio_change(current, average),
        baseline_sample_size=len(baseline_rows),
        persistence_days=persistence,
    )


def score_confidence(
    completeness: float, provider_agreement: float, persistence_days: int
) -> float:
    """Return a bounded confidence score, giving persistence full weight at 3 days."""
    normalized_completeness = _as_finite_float(completeness)
    normalized_agreement = _as_finite_float(provider_agreement)
    normalized_persistence_days = _as_finite_float(persistence_days)
    if (
        normalized_completeness is None
        or normalized_agreement is None
        or normalized_persistence_days is None
    ):
        return 0.0
    persistence = min(max(normalized_persistence_days, 0) / 3, 1)
    return round(
        min(
            max(
                0.5 * normalized_completeness
                + 0.3 * normalized_agreement
                + 0.2 * persistence,
                0,
            ),
            1,
        ),
        3,
    )


def evaluate_dashboard_signal(
    kpi: Mapping[str, object], *, completeness: float, provider_agreement: float
) -> dict:
    """Classify one KPI as fact, persistent risk, or ranked opportunity.

    Classification fails closed unless both default baselines agree in the same
    direction, the movement persists, data quality clears the confidence gate,
    and traceable evidence exists. A fact DTO is still returned when those
    stronger claims are unsupported.
    """
    kpi_id = str(kpi.get("id") or "")
    label = str(kpi.get("label") or kpi_id)
    metric = str(kpi.get("metric") or "")
    current = _as_finite_float(kpi.get("current"))
    previous = kpi.get("previous_complete_day")
    seven_day = kpi.get("latest_7_complete_days")
    previous = previous if isinstance(previous, Mapping) else {}
    seven_day = seven_day if isinstance(seven_day, Mapping) else {}
    previous_change = _as_finite_float(previous.get("change"))
    seven_day_deviation = _as_finite_float(seven_day.get("deviation"))
    persistence_days = int(_as_finite_float(kpi.get("persistence_days")) or 0)
    baseline_days = int(_as_finite_float(kpi.get("baseline_sample_size")) or 0)
    evidence_ids = [
        str(value) for value in (kpi.get("evidence_ids") or []) if str(value).strip()
    ]
    confidence = score_confidence(
        completeness, provider_agreement, persistence_days
    )
    fact = f"{label} is {current} on the latest complete date."
    evidence = [
        {
            "metric": metric,
            "current": current,
            "previous_value": _as_finite_float(previous.get("value")),
            "previous_change": previous_change,
            "seven_day_average": _as_finite_float(seven_day.get("average")),
            "seven_day_deviation": seven_day_deviation,
            "baseline_sample_size": baseline_days,
            "persistence_days": persistence_days,
            "evidence_ids": evidence_ids,
        }
    ]
    common = {
        "insight_id": f"{kpi_id}:latest",
        "scope": kpi_id,
        "metric": metric,
        "current": current,
        "previous_complete_day": dict(previous),
        "latest_7_complete_days": dict(seven_day),
        "persistence_days": persistence_days,
        "direction": (
            "up" if previous_change is not None and previous_change > 0
            else "down" if previous_change is not None and previous_change < 0
            else "flat"
        ),
        "contributors": [],
        "provider_coverage": provider_agreement,
        "fact": fact,
        "confidence": confidence,
        "evidence": evidence,
        "evidence_ids": evidence_ids,
    }
    rule = DASHBOARD_SIGNAL_RULES.get(kpi_id)
    eligible = (
        rule is not None
        and current is not None
        and previous_change is not None
        and seven_day_deviation is not None
        and baseline_days >= DASHBOARD_MIN_BASELINE_DAYS
        and persistence_days >= DASHBOARD_MIN_PERSISTENCE_DAYS
        and confidence >= DASHBOARD_MIN_CONFIDENCE
        and bool(evidence_ids)
    )
    if not eligible:
        return {
            **common,
            "severity": "info",
            "anomaly_score": None,
            "priority_score": None,
            "inference": None,
            "recommendation": None,
        }

    direction = 1 if previous_change > 0 else -1 if previous_change < 0 else 0
    if direction == 0 or direction != (1 if seven_day_deviation > 0 else -1):
        eligible = False
    previous_ratio = abs(previous_change) / float(rule["previous_threshold"])
    average_ratio = abs(seven_day_deviation) / float(rule["seven_day_threshold"])
    if previous_ratio < 1 or average_ratio < 1:
        eligible = False
    if not eligible:
        return {
            **common,
            "severity": "info",
            "anomaly_score": None,
            "priority_score": None,
            "inference": None,
            "recommendation": None,
        }

    persistence_ratio = min(persistence_days / 3, 1)
    signal_score = round(
        min(
            100.0,
            100
            * (
                0.45 * min(previous_ratio, 1)
                + 0.35 * min(average_ratio, 1)
                + 0.20 * persistence_ratio
            ),
        ),
        1,
    )
    is_risk = direction == int(rule["risk_direction"])
    severity = "risk" if is_risk else "opportunity"
    return {
        **common,
        "severity": severity,
        "anomaly_score": signal_score if is_risk else None,
        "priority_score": round(signal_score * confidence, 1) if not is_risk else None,
        "inference": rule[f"{severity}_inference"],
        "recommendation": rule[f"{severity}_recommendation"],
    }


def _freeze_evidence(value: object) -> object:
    """Freeze JSON-like mapping/list/tuple/set/scalar evidence without aliasing."""
    if isinstance(value, Mapping):
        return FrozenDict({key: _freeze_evidence(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_evidence(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_evidence(item) for item in value)
    if value is None or isinstance(value, (str, bytes, int, float, bool, Decimal)):
        return value
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


def build_insight(
    *,
    insight_id: str,
    severity: str,
    scope: str,
    fact: str,
    confidence: float,
    evidence: Sequence[dict],
    inference: str | None = None,
    recommendation: str | None = None,
) -> Insight:
    """Build an insight while suppressing advice unsupported by confidence."""
    normalized_confidence = _as_finite_float(confidence)
    if normalized_confidence is None:
        normalized_confidence = 0.0
    if normalized_confidence < 0.6:
        inference = None
        recommendation = None
    return Insight(
        insight_id=insight_id,
        severity=severity,
        scope=scope,
        fact=fact,
        inference=inference,
        recommendation=recommendation,
        confidence=normalized_confidence,
        evidence=tuple(
            cast(dict, _freeze_evidence(item)) for item in evidence
        ),
    )
