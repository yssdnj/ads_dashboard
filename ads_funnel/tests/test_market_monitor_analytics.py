import math
from decimal import Decimal
from inspect import signature
from typing import Sequence

import pytest

from ads_funnel.api.market_monitoring import analytics as market_analytics
from ads_funnel.api.market_monitoring.analytics import (
    Insight,
    MetricComparison,
    build_insight,
    compare_metric,
    score_confidence,
)


def test_keyword_opportunity_formula_is_centralized_traceable_and_bounded():
    assert sum(market_analytics.KEYWORD_OPPORTUNITY_WEIGHTS.values()) == pytest.approx(1)

    result = market_analytics.analyze_keyword_opportunity(
        {
            "keyword": "slip lead",
            "search_volume": 50_000,
            "relevance_weight": 0.8,
            "competitive_difficulty": 40,
            "click_conversion_rate": 0.1,
            "source_request_id": "keyword-source-1",
        },
        market_id="slip_lead_leash",
    )

    assert result["status"] == "available"
    assert result["score"] == pytest.approx(72.5)
    assert result["confidence"] == 1
    assert result["dimensions"]["demand"]["score"] == 100
    assert result["dimensions"]["attainability"]["score"] == 60
    assert result["evidence_ids"] == ["keyword-source-1"]


def test_keyword_opportunity_fails_closed_without_required_data():
    result = market_analytics.analyze_keyword_opportunity(
        {"keyword": "unknown", "competitive_difficulty": 20},
        market_id="slip_lead_leash",
    )

    assert result["status"] == "unavailable"
    assert result["score"] is None
    assert result["confidence"] < 0.6
    assert result["recommended_action"] is None
    assert result["unavailable_reason"]


@pytest.mark.parametrize(
    "field, invalid_value",
    [
        ("search_volume", -1),
        ("search_volume", True),
        ("search_volume", math.nan),
        ("competitive_difficulty", -1),
        ("competitive_difficulty", 101),
        ("competitive_difficulty", math.inf),
        ("click_conversion_rate", -0.01),
        ("click_conversion_rate", 1.01),
        ("click_conversion_rate", True),
        ("cpc", -0.01),
        ("cpc", math.inf),
        ("relevance_weight", -0.01),
        ("relevance_weight", 1.01),
    ],
)
def test_keyword_opportunity_rejects_explicit_out_of_domain_inputs(
    field, invalid_value
):
    row = {
        "keyword": "slip lead",
        "search_volume": 1_000,
        "relevance_weight": 0.8,
        "competitive_difficulty": 40,
        "click_conversion_rate": 0.1,
        "cpc": 0.8,
    }
    row[field] = invalid_value

    result = market_analytics.analyze_keyword_opportunity(
        row, market_id="slip_lead_leash"
    )

    assert result["status"] == "unavailable"
    assert result["score"] is None
    assert result["confidence"] == 0
    assert result["recommended_action"] is None
    assert result["unavailable_reason"]


def test_gap_action_priority_uses_impact_actionability_confidence_and_evidence():
    assert sum(market_analytics.GAP_ACTION_WEIGHTS.values()) == pytest.approx(1)
    actions = market_analytics.prioritize_gap_actions(
        [
            {
                "keyword": "slip lead",
                "competitor_asin": "COMP",
                "competitor_traffic": 100,
                "traffic_gap": 80,
                "relevance_level": "high",
                "evidence_id": "gap-source-1",
            }
        ],
        completeness=1,
        provider_coverage=1,
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["status"] == "available"
    assert action["impact"]["score"] == 80
    assert action["actionability"]["score"] == 90
    assert action["priority_score"] == pytest.approx(84.5)
    assert action["priority"] == "high"
    assert action["confidence"] == 1
    assert action["evidence_ids"] == ["gap-source-1"]


def test_gap_action_priority_suppresses_action_when_confidence_is_low():
    action = market_analytics.prioritize_gap_actions(
        [{"keyword": "slip lead", "competitor_asin": "COMP", "competitor_traffic": 100, "traffic_gap": 20, "relevance_level": "high"}],
        completeness=0.1,
        provider_coverage=0.1,
    )[0]

    assert action["confidence"] < 0.6
    assert action["recommended_action"] is None


@pytest.mark.parametrize(
    "field, invalid_value",
    [
        ("completeness", -0.01),
        ("completeness", 1.01),
        ("completeness", True),
        ("completeness", math.nan),
        ("provider_coverage", -0.01),
        ("provider_coverage", 1.01),
        ("provider_coverage", math.inf),
    ],
)
def test_gap_action_priority_rejects_invalid_quality_inputs(field, invalid_value):
    quality = {"completeness": 1, "provider_coverage": 1}
    quality[field] = invalid_value

    action = market_analytics.prioritize_gap_actions(
        [
            {
                "keyword": "slip lead",
                "competitor_asin": "COMP",
                "competitor_traffic": 100,
                "own_traffic": 20,
                "traffic_gap": 80,
                "relevance_level": "high",
                "evidence_id": "gap-source-1",
            }
        ],
        **quality,
    )[0]

    assert action["status"] == "unavailable"
    assert action["priority_score"] is None
    assert action["confidence"] == 0
    assert action["recommended_action"] is None
    assert action["unavailable_reason"]


@pytest.mark.parametrize(
    "patch",
    [
        {"competitor_traffic": -1},
        {"competitor_traffic": True},
        {"traffic_gap": -1},
        {"traffic_gap": 101},
        {"traffic_gap": math.nan},
        {"own_traffic": -1},
        {"relevance_level": "unknown"},
    ],
)
def test_gap_action_priority_rejects_invalid_action_inputs(patch):
    row = {
        "keyword": "slip lead",
        "competitor_asin": "COMP",
        "competitor_traffic": 100,
        "own_traffic": 20,
        "traffic_gap": 80,
        "relevance_level": "high",
        "evidence_id": "gap-source-1",
    }
    row.update(patch)

    action = market_analytics.prioritize_gap_actions(
        [row], completeness=1, provider_coverage=1
    )[0]

    assert action["status"] == "unavailable"
    assert action["priority_score"] is None
    assert action["confidence"] == 0
    assert action["recommended_action"] is None
    assert action["unavailable_reason"]


def test_compare_metric_uses_previous_complete_day_and_prior_seven_rows():
    rows = [
        {"date": f"2026-07-{day:02d}", "value": value, "is_complete": True}
        for day, value in zip(range(11, 20), [80, 90, 100, 110, 100, 90, 100, 105, 120])
    ]

    result = compare_metric(rows, "value")

    assert isinstance(result, MetricComparison)
    assert result.current == 120
    assert result.previous == 105
    assert result.previous_change == pytest.approx(15 / 105)
    assert result.baseline_sample_size == 7
    assert result.seven_day_average == pytest.approx(
        (90 + 100 + 110 + 100 + 90 + 100 + 105) / 7
    )


def test_compare_metric_skips_incomplete_days():
    rows = [
        {"date": "2026-07-18", "value": 100, "is_complete": True},
        {"date": "2026-07-19", "value": 0, "is_complete": False},
        {"date": "2026-07-20", "value": 120, "is_complete": True},
    ]

    assert compare_metric(rows, "value").previous == 100


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf, True])
def test_compare_metric_excludes_non_finite_and_boolean_values(invalid_value):
    result = compare_metric(
        [
            {"date": "2026-07-18", "value": 100, "is_complete": True},
            {"date": "2026-07-19", "value": invalid_value, "is_complete": True},
            {"date": "2026-07-20", "value": 120, "is_complete": True},
        ],
        "value",
    )

    assert result.current == 120
    assert result.previous == 100
    assert result.baseline_sample_size == 1


def test_compare_metric_accepts_finite_decimal_values():
    result = compare_metric(
        [
            {"date": "2026-07-18", "value": Decimal("100.5"), "is_complete": True},
            {"date": "2026-07-19", "value": Decimal("120.5"), "is_complete": True},
        ],
        "value",
    )

    assert result.current == 120.5
    assert result.previous == 100.5


def test_compare_metric_rejects_unsorted_duplicate_complete_dates():
    rows = [
        {"date": "2026-07-20", "value": 200, "is_complete": True},
        {"date": "2026-07-19", "value": 100, "is_complete": True},
        {"date": "2026-07-20", "value": 120, "is_complete": True},
    ]

    with pytest.raises(ValueError, match="^duplicate complete date: 2026-07-20$"):
        compare_metric(rows, "value")


def test_compare_metric_handles_empty_and_zero_baselines_without_ratio_errors():
    assert compare_metric([], "value") == MetricComparison(None, None, None, None, None, 0, 0)

    result = compare_metric(
        [
            {"date": "2026-07-18", "value": 0, "is_complete": True},
            {"date": "2026-07-19", "value": 10, "is_complete": True},
        ],
        "value",
    )
    assert result.previous_change is None
    assert result.seven_day_deviation is None


def test_compare_metric_respects_current_date_and_counts_same_direction_persistence():
    rows = [
        {"date": f"2026-07-{day:02d}", "value": value, "is_complete": True}
        for day, value in zip(range(15, 21), [30, 25, 20, 15, 10, 5])
    ]

    result = compare_metric(rows, "value", current_date="2026-07-19")

    assert result.current == 10
    assert result.persistence_days == 4


def test_confidence_is_bounded_and_reduced_by_partial_data():
    assert score_confidence(1, 1, 3) == 1
    assert 0 < score_confidence(0.5, 0.5, 1) < 0.5


def test_confidence_accepts_finite_decimal_values():
    assert score_confidence(Decimal("0.5"), Decimal("0.5"), Decimal("1")) == 0.467


@pytest.mark.parametrize(
    "completeness, provider_agreement, persistence_days",
    [
        (math.nan, 1, 3),
        (1, math.inf, 3),
        (1, 1, -math.inf),
        (True, 1, 3),
        (1, False, 3),
        (1, 1, True),
    ],
)
def test_confidence_safely_degrades_for_invalid_inputs(
    completeness, provider_agreement, persistence_days
):
    assert score_confidence(completeness, provider_agreement, persistence_days) == 0.0


def test_build_insight_separates_fact_from_low_confidence_inference_and_advice():
    insight = build_insight(
        insight_id="sales-drop",
        severity="high",
        scope="US",
        fact="Sales fell 20% from the prior complete day.",
        confidence=0.59,
        evidence=[{"metric": "sales", "current": 80}],
        inference="Demand is weakening.",
        recommendation="Reduce inventory replenishment.",
    )

    assert isinstance(insight, Insight)
    assert insight.fact == "Sales fell 20% from the prior complete day."
    assert insight.inference is None
    assert insight.recommendation is None
    assert insight.evidence == ({"metric": "sales", "current": 80},)


@pytest.mark.parametrize("invalid_confidence", [math.nan, math.inf, -math.inf, True, "0.8"])
def test_build_insight_suppresses_advice_for_invalid_direct_confidence(invalid_confidence):
    insight = build_insight(
        insight_id="sales-drop",
        severity="high",
        scope="US",
        fact="Sales fell 20% from the prior complete day.",
        confidence=invalid_confidence,
        evidence=[],
        inference="Demand may be weakening.",
        recommendation="Review demand drivers.",
    )

    assert insight.confidence == 0.0
    assert insight.inference is None
    assert insight.recommendation is None


def test_build_insight_recursively_copies_and_freezes_evidence():
    evidence = [
        {"metric": "sales", "detail": {"regions": ["US"], "tags": {"us"}}}
    ]
    insight = build_insight(
        insight_id="sales-drop",
        severity="high",
        scope="US",
        fact="Sales fell 20% from the prior complete day.",
        confidence=0.8,
        evidence=evidence,
    )

    evidence[0]["metric"] = "orders"
    evidence[0]["detail"]["regions"].append("UK")
    evidence[0]["detail"]["tags"].add("uk")

    assert isinstance(insight.evidence[0], dict)
    assert isinstance(insight.evidence[0]["detail"], dict)
    assert insight.evidence[0]["metric"] == "sales"
    assert insight.evidence[0]["detail"]["regions"] == ("US",)
    assert insight.evidence[0]["detail"]["tags"] == frozenset({"us"})
    with pytest.raises(TypeError):
        insight.evidence[0]["metric"] = "orders"
    with pytest.raises(TypeError):
        insight.evidence[0]["detail"]["tags"] = frozenset({"uk"})


def test_build_insight_rejects_unknown_mutable_evidence_leaf():
    class MutableLeaf:
        pass

    with pytest.raises(TypeError, match="unsupported evidence value"):
        build_insight(
            insight_id="sales-drop",
            severity="high",
            scope="US",
            fact="Sales fell 20% from the prior complete day.",
            confidence=0.8,
            evidence=[{"unsupported": MutableLeaf()}],
        )


def test_public_annotations_match_the_published_interface():
    score_parameters = signature(score_confidence).parameters
    insight_parameters = signature(build_insight).parameters

    assert score_parameters["completeness"].annotation is float
    assert score_parameters["provider_agreement"].annotation is float
    assert score_parameters["persistence_days"].annotation is int
    assert signature(score_confidence).return_annotation is float
    assert Insight.__annotations__["evidence"] == Sequence[dict]
    assert insight_parameters["confidence"].annotation is float
    assert insight_parameters["evidence"].annotation == Sequence[dict]


def test_build_insight_keeps_supported_inference_and_recommendation():
    insight = build_insight(
        insight_id="sales-drop",
        severity="high",
        scope="US",
        fact="Sales fell 20% from the prior complete day.",
        confidence=0.6,
        evidence=[],
        inference="Demand may be weakening.",
        recommendation="Review demand drivers.",
    )

    assert insight.inference == "Demand may be weakening."
    assert insight.recommendation == "Review demand drivers."
