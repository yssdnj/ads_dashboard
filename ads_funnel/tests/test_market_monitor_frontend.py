from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "template.html"
CSS_PATH = PROJECT_ROOT / "frontend" / "static" / "market-monitor.css"
JS_PATH = PROJECT_ROOT / "frontend" / "static" / "market-monitor.js"


def test_market_monitor_template_has_command_center_regions():
    html = TEMPLATE_PATH.read_text(encoding="utf-8")

    for element_id in (
        "mi-context",
        "mi-data-quality",
        "mi-kpis",
        "mi-trend",
        "mi-insights",
        "mi-anomalies",
        "mi-opportunities",
        "mi-evidence-drawer",
        "mi-comparison",
        "mi-keyword-dashboard",
    ):
        assert f'id="{element_id}"' in html


def test_market_monitor_template_uses_semantic_controls_and_parent_grouped_listbox():
    html = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert 'role="listbox"' in html
    assert 'aria-controls="mi-object-options"' in html
    assert 'id="mi-object-options"' in html
    assert 'for="mi-market"' in html
    assert 'aria-label="分析场景"' in html
    assert 'aria-label="关键词相关性"' in html
    assert '<aside id="mi-evidence-drawer"' in html
    assert 'aria-hidden="true"' in html
    assert 'id="mi-gap-analysis"' in html
    assert 'id="mi-keyword-opportunity"' in html
    assert 'id="mi-keyword-search"' in html
    assert 'id="mi-keyword-metrics"' in html
    assert re.search(r'<canvas id="miTrendChart"[^>]*tabindex="-1"', html)


def test_market_monitor_css_defines_accessible_semantic_tokens():
    css = CSS_PATH.read_text(encoding="utf-8")

    for token in (
        "--mi-primary",
        "--mi-own",
        "--mi-high",
        "--mi-mid",
        "--mi-low",
        "--mi-risk",
        "--mi-opportunity",
    ):
        assert token in css
    assert "body.dark .mi-root" in css
    assert ":focus-visible" in css
    assert "@media (max-width:980px)" in css
    assert "@media (max-width:620px)" in css
    assert "@media (prefers-reduced-motion:reduce)" in css
    assert "linear-gradient" not in css
    assert "border-left:" not in css
    assert "backdrop-filter" not in css


def test_market_monitor_filter_controls_are_left_aligned_on_desktop():
    css = CSS_PATH.read_text(encoding="utf-8")

    context_rule = re.search(r"\.mi-context\s*\{(?P<body>[^}]*)\}", css, re.DOTALL)
    controls_rule = re.search(
        r"\.mi-context-controls\s*\{(?P<body>[^}]*)\}", css, re.DOTALL
    )

    assert context_rule
    assert controls_rule
    assert "justify-content:flex-start" in context_rule.group("body")
    assert "justify-content:flex-start" in controls_rule.group("body")
    assert "justify-content:flex-end" not in controls_rule.group("body")


def test_market_monitor_object_filter_is_wider_with_readable_thumbnails():
    css = CSS_PATH.read_text(encoding="utf-8")

    controls_rule = re.search(
        r"\.mi-context-controls\s*\{(?P<body>[^}]*)\}", css, re.DOTALL
    )
    object_rule = re.search(r"\.mi-object-field\s*\{(?P<body>[^}]*)\}", css)
    thumb_rule = re.search(r"\.mi-object-thumb\s*\{(?P<body>[^}]*)\}", css)

    assert controls_rule
    assert object_rule
    assert thumb_rule
    assert "gap:14px" in controls_rule.group("body")
    assert "flex-wrap:nowrap" in controls_rule.group("body")
    assert "flex-wrap:wrap" not in controls_rule.group("body")
    assert "min-width:360px" in object_rule.group("body")
    assert "max-width:520px" in object_rule.group("body")
    assert "width:44px" in thumb_rule.group("body")
    assert "height:44px" in thumb_rule.group("body")


def test_market_monitor_javascript_uses_bounded_renderers_and_three_tier_keyword_state():
    javascript = JS_PATH.read_text(encoding="utf-8")

    for name in (
        "loadDashboard",
        "loadComparison",
        "renderContext",
        "renderDataQuality",
        "renderKpis",
        "renderTrend",
        "renderInsights",
        "renderAnomalies",
        "renderOpportunities",
        "renderEvidenceDrawer",
        "renderComparison",
        "renderKeywordDashboard",
    ):
        assert re.search(rf"\bfunction\s+{name}\s*\(", javascript)

    assert 'relevance: "high"' in javascript
    assert 'const RELEVANCE_LEVELS = ["high", "mid", "low"]' in javascript
    assert "relevance_level=${encodeURIComponent(MI.relevance)}" in javascript
    assert "relevance_level=strong" not in javascript
    assert 'relevance: "all"' not in javascript


def test_market_monitor_javascript_guards_races_charts_and_keyboard_access():
    javascript = JS_PATH.read_text(encoding="utf-8")

    assert "AbortController" in javascript
    assert ".abort()" in javascript
    assert "requestToken" in javascript
    assert ".destroy()" in javascript
    assert 'mode: "index"' in javascript
    assert "intersect: false" in javascript
    assert 'event.key === "ArrowDown"' in javascript
    assert 'event.key === "ArrowUp"' in javascript
    assert 'event.key === "Escape"' in javascript
    assert "textContent" in javascript
    assert "placeholder-product.svg" in javascript


def test_market_monitor_handles_unavailable_metrics_and_linked_generations():
    javascript = JS_PATH.read_text(encoding="utf-8")

    for token in (
        "metric_availability", "hasUsableValues", "beginGeneration",
        "abortAllRequests", "isCurrentGeneration", "Promise.allSettled",
        "renderLinkedAnalysis", "active.find",
    ):
        assert token in javascript

    current_insights = re.search(
        r"function currentInsights\(\) \{(?P<body>.*?)\n  \}\n\n  function renderAnomalies",
        javascript,
        re.DOTALL,
    )
    assert current_insights
    assert "MI.comparison?.context?.metric_available === false" in current_insights.group("body")


def test_market_monitor_renders_complete_evidence_and_quality_contract():
    javascript = JS_PATH.read_text(encoding="utf-8")

    for field in (
        "severity", "metric", "previous_complete_day", "latest_7_complete_days",
        "persistence_days", "contributors", "provider_coverage",
        "collect_time_bj", "collection_timezone", "missing_data_types",
        "collection_markers", "movingAverage", "evidenceInvoker",
    ):
        assert field in javascript
