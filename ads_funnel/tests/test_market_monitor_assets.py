import json
from pathlib import Path
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from ads_funnel.api import export, main
from ads_funnel.api.market_monitoring import repository


PROJECT_ROOT = Path(__file__).parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "template.html"
CSS_PATH = PROJECT_ROOT / "frontend" / "static" / "market-monitor.css"
JS_PATH = PROJECT_ROOT / "frontend" / "static" / "market-monitor.js"

MINIMAL_REPORT = {
    "wk": [],
    "wk_dates": {},
    "wk_iso_dates": {},
}


def test_html_export_endpoint_encodes_non_ascii_download_filename(monkeypatch):
    monkeypatch.setattr(
        main.db,
        "get_report",
        lambda _report_id: {
            "data": json.dumps(MINIMAL_REPORT),
            "title": "US test",
            "weeks": ["25W1", "26W28"],
        },
    )
    monkeypatch.setattr(main.db, "get_config", lambda _key, default: default)
    monkeypatch.setattr(main.export, "to_html_export", lambda *_args: "<html></html>")

    response = main.api_export_html(2)

    disposition = response.headers["content-disposition"]
    disposition.encode("latin-1")
    assert "filename*=UTF-8''%E5%B9%BF%E5%91%8A" in disposition


def _run_market_monitor_behavior(body: str):
    javascript = JS_PATH.read_text(encoding="utf-8").replace(
        "  window.MarketMonitor = Object.freeze({",
        "  Object.assign(window.__miTest, { MI, charts, load, loadObjectDetail, "
        "beginGeneration, renderKpis, renderTrend, renderComparisonTable, "
        "renderKeywordOpportunity, renderKeywordChart, renderGapAnalysis, "
        "openEvidence, closeEvidence });\n\n"
        "  window.MarketMonitor = Object.freeze({",
    )
    harness = f"""
const assert = require("assert");
const vm = require("vm");
const elements = new Map();
class FakeElement {{
  constructor(id = "") {{
    this.id = id; this.value = ""; this.innerHTML = ""; this.textContent = "";
    this.children = []; this.hidden = false; this.disabled = false;
    this.dataset = {{}}; this.isConnected = true;
    this.classList = {{toggle() {{}}, add() {{}}, remove() {{}}}};
  }}
  append(...nodes) {{ this.children.push(...nodes); }}
  replaceChildren(...nodes) {{ this.children = nodes; this.innerHTML = ""; }}
  addEventListener(type, listener) {{
    this.listeners = this.listeners || {{}};
    (this.listeners[type] = this.listeners[type] || []).push(listener);
  }}
  dispatch(type) {{
    return Promise.all((this.listeners?.[type] || []).map((listener) => listener({{target: this, currentTarget: this}})));
  }}
  setAttribute(name, value) {{ this[name] = String(value); }}
  getAttribute(name) {{ return this[name]; }}
  matches() {{ return false; }}
  querySelector() {{ return null; }}
  querySelectorAll() {{ return []; }}
  focus() {{ context.document.activeElement = this; this.focused = true; }}
}}
function addElement(id) {{ const node = new FakeElement(id); elements.set(id, node); return node; }}
function FakeChart(canvas, config) {{ this.canvas = canvas; this.config = config; this.destroyed = false; this.destroy = () => {{ this.destroyed = true; }}; FakeChart.instances.push(this); }}
FakeChart.instances = [];
const context = {{
  window: {{__miTest: {{}}}}, setTimeout: () => 0, setImmediate,
  AbortController, encodeURIComponent, Chart: FakeChart,
  getComputedStyle: () => ({{getPropertyValue: () => "#356ae6"}}),
  document: {{
    activeElement: null, documentElement: new FakeElement("html"),
    getElementById: (id) => elements.get(id) || null,
    querySelectorAll: () => [],
    createElement: (tag) => new FakeElement(tag),
    addEventListener() {{}},
  }},
}};
vm.createContext(context);
vm.runInContext({json.dumps(javascript)}, context);
(async () => {{
{body}
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
    result = subprocess.run(
        ["node", "-"], input=harness, text=True, encoding="utf-8", check=False,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_export_inlines_market_monitor_assets():
    html = export._build_html(MINIMAL_REPORT, "test", {}, None)

    assert "__MARKET_MONITOR_CSS__" not in html
    assert "__MARKET_MONITOR_JS__" not in html
    assert "window.MarketMonitor" in html
    assert ".mi-command-center" in html
    assert '<script src="/static/market-monitor.js"' not in html
    assert '<link rel="stylesheet" href="/static/market-monitor.css"' not in html


def test_template_keeps_market_monitor_behind_inline_markers():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert template.count("/* __MARKET_MONITOR_CSS__ */") == 1
    assert template.count("/* __MARKET_MONITOR_JS__ */") == 1
    assert re.search(r"\b(?:const|let|var)\s+MI\b", template) is None
    assert re.search(r"\b(?:async\s+)?function\s+mi[A-Za-z0-9_]*\s*\(", template) is None
    assert re.search(r"\bmi[A-Z][A-Za-z0-9_]*\s*=\s*(?:async\s*)?\(", template) is None


def test_extracted_module_uses_a_single_public_namespace():
    javascript = JS_PATH.read_text(encoding="utf-8")

    expected = [
        "load",
        "selectAsin",
        "selectKeyword",
        "setScenario",
        "setMetric",
        "setRange",
        "toggleCompetitor",
    ]
    harness = f"""
const assert = require("assert");
const vm = require("vm");
const context = {{window: {{}}, setTimeout: () => 0}};
vm.createContext(context);
vm.runInContext({json.dumps(javascript)}, context);
assert.deepStrictEqual(Object.keys(context.window), ["MarketMonitor"]);
assert.deepStrictEqual(Object.keys(context.window.MarketMonitor).sort(), {json.dumps(sorted(expected))});
assert.strictEqual(Object.isFrozen(context.window.MarketMonitor), true);
"""
    subprocess.run(
        ["node", "-"], input=harness, text=True, check=True, capture_output=True,
    )


def test_extracted_module_has_no_duplicate_private_definitions():
    javascript = JS_PATH.read_text(encoding="utf-8")
    state_definitions = re.findall(r"\b(?:const|let|var)\s+MI\b", javascript)
    function_names = re.findall(
        r"\b(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
        javascript,
    )

    assert len(state_definitions) == 1
    assert len(function_names) == len(set(function_names))


@pytest.mark.parametrize("report_id", [None, 7])
def test_export_embeds_asset_contents_verbatim_in_both_modes(report_id):
    html = export._build_html(MINIMAL_REPORT, "test", {}, report_id)

    assert CSS_PATH.read_text(encoding="utf-8") in html
    assert JS_PATH.read_text(encoding="utf-8") in html
    assert html.count("window.MarketMonitor") == 1
    assert html.count("<script") == html.count("</script>")
    assert html.count("<style") == html.count("</style>")


def test_assets_cannot_close_their_template_raw_text_elements():
    assert re.search(r"</script\s*", JS_PATH.read_text(encoding="utf-8"), re.I) is None
    assert re.search(r"</style\s*", CSS_PATH.read_text(encoding="utf-8"), re.I) is None


@pytest.mark.parametrize("marker", [
    "/* __MARKET_MONITOR_CSS__ */",
    "/* __MARKET_MONITOR_JS__ */",
])
@pytest.mark.parametrize("count", [0, 2])
def test_asset_inlining_requires_each_marker_exactly_once(marker, count):
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    broken = template.replace(marker, marker * count)

    with pytest.raises(export.TemplateAssetMarkerError, match=re.escape(marker)):
        export._inline_market_monitor_assets(broken)


@pytest.mark.parametrize("report_id", [None, 7])
def test_script_json_values_cannot_terminate_script_elements(report_id):
    hostile = "</script><script>globalThis.__task7_xss=1</script>&>\u2028\u2029"
    data = {
        "wk": [hostile],
        "wk_dates": {hostile: hostile},
        "wk_iso_dates": {hostile: [hostile, hostile]},
        "camps": [{"n": hostile}],
    }

    html = export._build_html(data, f"{hostile} report", {hostile: hostile}, report_id)

    assert hostile not in html
    assert "globalThis.__task7_xss=1" in html
    assert "\\u003c/script\\u003e\\u003cscript\\u003e" in html
    assert "\\u0026\\u003e\\u2028\\u2029" in html


def test_generated_inline_scripts_remain_parseable_with_hostile_report_data():
    hostile = "</script><script>globalThis.__task7_xss=1</script>"
    html = export.to_html_webapp(
        {"wk": [hostile], "wk_dates": {hostile: hostile}, "camps": [{"n": hostile}]},
        f"{hostile} report",
        {hostile: hostile},
        9,
    )
    inline_scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S | re.I)

    assert len(inline_scripts) >= 3
    harness = f"""
const vm = require("vm");
for (const script of {json.dumps(inline_scripts)}) new vm.Script(script);
"""
    subprocess.run(
        ["node", "-"], input=harness, text=True, check=True, capture_output=True,
    )


def test_selected_asin_is_rendered_as_dom_text_not_kpi_html():
    javascript = JS_PATH.read_text(encoding="utf-8")
    hostile = 'B0BAD"><img src=x onerror="globalThis.__task7_dom_xss=1">'
    harness = f"""
const assert = require("assert");
const vm = require("vm");

function element(id) {{
  return {{
    id, value: "", options: [], children: [], textContent: "", title: "",
    append(...nodes) {{ this.children.push(...nodes); }},
    replaceChildren(...nodes) {{ this.children = nodes; }},
    querySelectorAll() {{ return []; }},
    set innerHTML(value) {{
      if (id === "mi-kpis") throw new Error("unsafe KPI innerHTML sink");
      this._innerHTML = value;
    }},
    get innerHTML() {{ return this._innerHTML || ""; }},
  }};
}}

const elements = new Map([
  ["mi-asin", element("mi-asin")],
  ["mi-status", element("mi-status")],
  ["mi-kpis", element("mi-kpis")],
  ["mi-asin-table", element("mi-asin-table")],
  ["mi-asin-keyword-table", element("mi-asin-keyword-table")],
  ["mi-keyword-comp-table", element("mi-keyword-comp-table")],
]);
const context = {{
  window: {{}},
  document: {{
    getElementById(id) {{ return elements.get(id) || null; }},
    createElement(tagName) {{ const node = element(tagName); node.tagName = tagName.toUpperCase(); return node; }},
  }},
  fetch: async (path) => ({{
    ok: true,
    json: async () => path.includes("asin-trend")
      ? {{rows: [{{total_traffic: 5, rank_in_market: 2}}]}}
      : path.includes("asin-snapshots") ? {{asins: []}}
      : path.includes("asin-keywords") ? {{keywords: []}}
      : {{asins: []}},
  }}),
  encodeURIComponent,
  setTimeout: () => 0,
  _charts: {{}},
}};
vm.createContext(context);
vm.runInContext({json.dumps(javascript)}, context);
context.window.MarketMonitor.selectAsin({json.dumps(hostile)});

setImmediate(() => {{
  const kpis = elements.get("mi-kpis");
  assert.strictEqual(elements.get("mi-status").textContent.includes("unsafe KPI innerHTML sink"), false);
  assert.strictEqual(kpis.children.length, 6);
  const selectedAsinSummary = kpis.children[3].children[2];
  assert.strictEqual(selectedAsinSummary.textContent.includes({json.dumps(hostile)}), true);
  assert.strictEqual(selectedAsinSummary.title.includes({json.dumps(hostile)}), true);
}});
"""
    subprocess.run(
        ["node", "-"], input=harness, text=True, check=True, capture_output=True,
    )


def test_dashboard_anomaly_evidence_remains_resolvable_after_comparison_loads():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
const drawer = addElement("mi-evidence-drawer");
const content = addElement("mi-evidence-content");
addElement("mi-evidence-close");
const invoker = addElement("anomaly-button");
t.MI.dashboard = {
  insights: [{insight_id: "dashboard-risk", severity: "risk", metric: "total_traffic", current: 90, fact: "Dashboard anomaly", evidence_ids: ["e-1"]}],
  opportunities: [], data_quality: {provider_coverage: 1},
};
t.MI.comparison = {context: {metric: "total_traffic", metric_available: true}, comparisons: [{asin: "OWN", current: 100}]};
t.MI.metric = "total_traffic";
t.openEvidence("dashboard-risk", invoker);
assert.strictEqual(drawer.hidden, false);
assert.match(content.innerHTML, /Dashboard anomaly/);
t.closeEvidence();
assert.strictEqual(invoker.focused, true);
""")


def test_unavailable_comparison_uses_one_explanation_not_null_cards_or_table():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
const kpis = addElement("mi-kpis");
const table = addElement("mi-comparison-table");
t.MI.ownAsin = "OWN"; t.MI.scenario = "keyword_competition"; t.MI.metric = "keyword_traffic";
t.MI.comparison = {
  context: {own_asin: "OWN", scenario: "keyword_competition", metric: "keyword_traffic", metric_available: false, unavailable_reason: "Unavailable complete-day metric"},
  comparisons: [{asin: "OWN", current: null, previous_complete_day: {value: null}, latest_7_complete_days: {average: null}}],
};
t.renderKpis(); t.renderComparisonTable();
assert.strictEqual(kpis.children.length, 0);
assert.match(kpis.innerHTML, /Unavailable complete-day metric/);
assert.match(table.innerHTML, /Unavailable complete-day metric/);
assert.doesNotMatch(table.innerHTML, /<table/);
""")


def test_linked_trend_uses_complete_points_collection_markers_and_asin_baselines():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
addElement("mi-trend"); addElement("mi-trend-state"); const trendCanvas = addElement("miTrendChart");
const drawer = addElement("mi-evidence-drawer"); const content = addElement("mi-evidence-content"); addElement("mi-evidence-close");
t.MI.ownAsin = "OWN"; t.MI.scenario = "growth_quality"; t.MI.metric = "weighted_traffic";
t.MI.dashboard = {trend: {anomaly_markers: [{date: "2026-07-19", value: 10, insight_id: "dashboard-risk"}], collection_markers: [{date: "2026-07-19", run_id: "run-19"}]}, kpis: [], insights: [{insight_id: "dashboard-risk", severity: "risk", metric: "weighted_traffic", current: 10, fact: "Marker evidence"}], opportunities: []};
t.MI.comparison = {
  context: {own_asin: "OWN", scenario: "growth_quality", metric: "weighted_traffic", metric_available: true},
  series: [{asin: "OWN", role: "own", points: [
    {date: "2026-07-19", value: 10, is_complete: true},
    {date: "2026-07-20", value: 0, is_complete: false},
  ]}],
  comparisons: [{asin: "OWN", previous_complete_day: {value: 8}, latest_7_complete_days: {average: 7}}],
};
t.renderTrend();
const config = FakeChart.instances.at(-1).config;
assert.deepStrictEqual(Array.from(config.data.labels), ["2026-07-19"]);
assert.ok(config.data.datasets.some((dataset) => dataset.collectionMarker));
const own = config.data.datasets.find((dataset) => dataset.label === "OWN");
const lines = config.options.plugins.tooltip.callbacks.afterBody([{dataset: own}]);
assert.ok(lines.some((line) => line.includes("8")));
assert.ok(lines.some((line) => line.includes("7")));
const markerIndex = config.data.datasets.findIndex((dataset) => dataset.insightId === "dashboard-risk");
config.options.onClick(null, [{datasetIndex: markerIndex}]);
assert.strictEqual(drawer.hidden, false);
assert.match(content.innerHTML, /Marker evidence/);
t.closeEvidence();
assert.strictEqual(trendCanvas.focused, true);
""")


def test_superseding_main_load_releases_refresh_and_keeps_core_loaded():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
const refresh = addElement("mi-refresh");
const market = addElement("mi-market"); market.value = "slip_lead_leash";
let comparisonCalls = 0; const pending = [];
const response = (payload) => ({ok: true, json: async () => payload});
context.fetch = (path) => {
  if (path.includes("/date-context")) return Promise.resolve(response({latest_complete_date: "2026-07-20", latest_data_date: "2026-07-20"}));
  if (path.includes("/dashboard")) return Promise.resolve(response({context: {own_asin: "OWN", latest_data_date: "2026-07-20"}, data_quality: null, kpis: [], trend: {series: [], anomaly_markers: [], collection_markers: []}, insights: [], opportunities: []}));
  if (path.includes("/asins?")) return Promise.resolve(response({asins: [{asin: "OWN"}]}));
  if (path.includes("/keyword-summary")) return Promise.resolve(response({counts: {}, levels: [], trend: []}));
  if (path.includes("/keywords?")) return Promise.resolve(response({keywords: [{keyword: "lead"}]}));
  if (path.includes("/comparison")) {
    comparisonCalls += 1;
    if (comparisonCalls === 1) return Promise.resolve(response({context: {own_asin: "B0D6G27DNH", scenario: "market_share", metric: "total_traffic", metric_available: true, metric_availability: {total_traffic: true}}, series: [], comparisons: []}));
    return Promise.resolve(response({context: {own_asin: "OWN", scenario: "growth_quality", metric: "weighted_traffic", metric_available: true, metric_availability: {weighted_traffic: true}}, series: [], comparisons: []}));
  }
  return new Promise((resolve) => pending.push(() => resolve(response({rows: [], asins: [], keywords: []}))));
};
const mainLoad = context.window.MarketMonitor.load(true);
for (let index = 0; index < 8 && pending.length < 5; index += 1) await new Promise(setImmediate);
assert.ok(pending.length >= 5);
context.window.MarketMonitor.setScenario("growth_quality");
pending.splice(0).forEach((resolve) => resolve());
await mainLoad;
assert.strictEqual(refresh.disabled, false);
assert.strictEqual(t.MI.loaded, true);
""")


def test_initial_load_bootstraps_complete_date_then_applies_default_30_day_range():
    _run_market_monitor_behavior("""
const refresh = addElement("mi-refresh");
const market = addElement("mi-market"); market.value = "slip_lead_leash";
const paths = [];
const response = (payload) => ({ok: true, json: async () => payload});
context.fetch = async (path) => {
  paths.push(path);
  if (path.includes("/date-context")) return response({latest_complete_date: "2026-07-20", latest_data_date: "2026-07-20"});
  if (path.includes("/dashboard")) return response({context: {own_asin: "OWN", latest_data_date: "2026-07-20"}, data_quality: null, kpis: [], trend: {series: [], anomaly_markers: [], collection_markers: []}, insights: [], opportunities: []});
  if (path.includes("/asins?")) return response({asins: [{asin: "OWN"}]});
  if (path.includes("/keyword-summary")) return response({counts: {}, levels: [], trend: []});
  if (path.includes("/keywords?")) return response({keywords: [{keyword: "lead", opportunity: {status: "unavailable", score: null, confidence: 0, dimensions: {}}}]});
  if (path.includes("/comparison")) return response({context: {own_asin: "OWN", scenario: "market_share", metric: "total_traffic", metric_available: true, metric_availability: {total_traffic: true}}, series: [], comparisons: [], gap_contributors: [], action_priorities: []});
  if (path.includes("asin-trend")) return response({rows: []});
  if (path.includes("asin-keywords")) return response({keywords: []});
  if (path.includes("keyword-trend")) return response({rows: []});
  return response({asins: []});
};
await context.window.MarketMonitor.load(true);
assert.ok(paths[0].includes("/date-context?"));
const ranged = paths.filter((path) => /dashboard|comparison|keyword-summary|keyword-trend|asin-trend/.test(path) || path.includes("/keywords?"));
assert.ok(ranged.length >= 5);
for (const path of ranged) {
  assert.match(path, /date_from=2026-06-21/);
  assert.match(path, /date_to=2026-07-20/);
}
assert.strictEqual(refresh.disabled, false);
""")


def test_custom_range_survives_market_change_and_scopes_every_request():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
const refresh = addElement("mi-refresh");
const market = addElement("mi-market"); market.value = "old_market";
addElement("mi-status");
t.MI.rangePreset = "custom"; t.MI.dateFrom = "2026-07-10"; t.MI.dateTo = "2026-07-20";
let paths = [];
const response = (payload) => ({ok: true, json: async () => payload});
context.fetch = async (path) => {
  paths.push(path);
  if (path.includes("/date-context")) return response({earliest_data_date: "2026-07-01", latest_complete_date: "2026-07-31", latest_data_date: "2026-07-31"});
  if (path.includes("/dashboard")) return response({context: {own_asin: "OWN", latest_data_date: "2026-07-31"}, data_quality: null, kpis: [], trend: {series: [], anomaly_markers: [], collection_markers: []}, insights: [], opportunities: []});
  if (path.includes("/asins?")) return response({asins: [{asin: "OWN"}]});
  if (path.includes("/keyword-summary")) return response({counts: {}, levels: [], trend: []});
  if (path.includes("/keywords?")) return response({keywords: [{keyword: "lead", opportunity: {status: "unavailable", score: null, confidence: 0, dimensions: {}}}]});
  if (path.includes("/comparison")) return response({context: {own_asin: "OWN", scenario: "market_share", metric: "total_traffic", metric_available: true, metric_availability: {total_traffic: true}}, series: [], comparisons: [], gap_contributors: [], action_priorities: []});
  if (path.includes("asin-trend")) return response({rows: []});
  if (path.includes("asin-keywords")) return response({keywords: []});
  if (path.includes("keyword-trend")) return response({rows: []});
  return response({asins: []});
};
await context.window.MarketMonitor.load(true);
paths = [];
market.value = "new_market";
await market.dispatch("change");
for (let index = 0; index < 12 && (refresh.disabled || paths.length < 6); index += 1) await new Promise(setImmediate);
assert.strictEqual(t.MI.rangePreset, "custom");
assert.strictEqual(t.MI.dateFrom, "2026-07-10");
assert.strictEqual(t.MI.dateTo, "2026-07-20");
assert.ok(paths[0].includes("/date-context?"));
const scoped = paths.filter((path) => /dashboard|comparison|keyword-summary|keyword-trend|asin-trend/.test(path) || path.includes("/keywords?"));
assert.ok(scoped.length >= 5);
for (const path of scoped) {
  assert.match(path, /date_from=2026-07-10/);
  assert.match(path, /date_to=2026-07-20/);
}
""")


def test_custom_range_outside_new_market_fails_before_scoped_requests():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
const refresh = addElement("mi-refresh");
const market = addElement("mi-market"); market.value = "old_market";
const status = addElement("mi-status");
t.MI.rangePreset = "custom"; t.MI.dateFrom = "2025-07-10"; t.MI.dateTo = "2025-07-20";
let paths = [];
const response = (payload) => ({ok: true, json: async () => payload});
context.fetch = async (path) => {
  paths.push(path);
  if (path.includes("/date-context")) {
    const oldMarket = path.includes("old_market");
    return response(oldMarket
      ? {earliest_data_date: "2025-07-01", latest_complete_date: "2025-07-31", latest_data_date: "2025-07-31"}
      : {earliest_data_date: "2026-07-01", latest_complete_date: "2026-07-31", latest_data_date: "2026-07-31"});
  }
  if (path.includes("/dashboard")) return response({context: {own_asin: "OWN", latest_data_date: "2025-07-31"}, data_quality: null, kpis: [], trend: {series: [], anomaly_markers: [], collection_markers: []}, insights: [], opportunities: []});
  if (path.includes("/asins?")) return response({asins: [{asin: "OWN"}]});
  if (path.includes("/keyword-summary")) return response({counts: {}, levels: [], trend: []});
  if (path.includes("/keywords?")) return response({keywords: []});
  if (path.includes("/comparison")) return response({context: {own_asin: "OWN", scenario: "market_share", metric: "total_traffic", metric_available: true, metric_availability: {total_traffic: true}}, series: [], comparisons: []});
  return response({rows: [], asins: [], keywords: []});
};
await context.window.MarketMonitor.load(true);
paths = [];
market.value = "new_market";
await market.dispatch("change");
for (let index = 0; index < 12 && refresh.disabled; index += 1) await new Promise(setImmediate);
assert.strictEqual(t.MI.rangePreset, "custom");
assert.ok(t.MI.dateFrom); assert.ok(t.MI.dateTo);
assert.deepStrictEqual(paths.length, 1);
assert.ok(paths[0].includes("/date-context?"));
assert.match(status.textContent, /自定义日期范围/);
""")


def test_partial_detail_failure_clears_stale_slices_and_preserves_local_errors():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
const comparisonState = addElement("mi-comparison-state");
const keywordState = addElement("mi-keyword-state");
addElement("miComparisonChart");
t.MI.ownAsin = "NEW"; t.MI.keyword = "new keyword";
t.MI.asinSnapshots = [{child_asin: "OLD"}]; t.MI.asinTrend = [{total_traffic: 999}];
t.MI.asinKeywords = [{keyword: "old keyword"}]; t.MI.competition = [{child_asin: "OLD"}];
t.MI.comparison = {context: {metric_available: true}, series: [{asin: "NEW", role: "own", points: [{date: "2026-07-19", value: 1, is_complete: true}]}], comparisons: []};
context.fetch = async () => { throw new Error("forced detail failure"); };
const generation = t.beginGeneration("detail");
await t.loadObjectDetail(generation.signal, generation.requestToken);
assert.deepStrictEqual(Array.from(t.MI.asinSnapshots), []);
assert.deepStrictEqual(Array.from(t.MI.asinTrend), []);
assert.deepStrictEqual(Array.from(t.MI.asinKeywords), []);
assert.deepStrictEqual(Array.from(t.MI.competition), []);
assert.match(comparisonState.innerHTML, /失败/);
assert.match(keywordState.innerHTML, /失败/);
""")


def test_object_comparison_failure_clears_both_linked_charts():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
addElement("mi-comparison-state"); addElement("mi-trend-state");
let trendDestroyed = false; let comparisonDestroyed = false;
t.charts.trend = {destroy() { trendDestroyed = true; }};
t.charts.comparison = {destroy() { comparisonDestroyed = true; }};
const response = (payload) => ({ok: true, json: async () => payload});
context.fetch = async (path) => {
  if (path.includes("/comparison")) throw new Error("forced comparison failure");
  if (path.includes("asin-trend")) return response({rows: []});
  if (path.includes("asin-keywords")) return response({keywords: []});
  return response({asins: []});
};
await context.window.MarketMonitor.selectAsin("NEW-ASIN");
assert.strictEqual(trendDestroyed, true);
assert.strictEqual(comparisonDestroyed, true);
assert.strictEqual(t.MI.comparison, null);
""")


def test_keyword_surface_renders_aggregate_trend_sortable_opportunity_and_expanded_history():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
const opportunity = addElement("mi-keyword-opportunity");
addElement("miKeywordChart"); addElement("mi-keyword-state");
t.MI.relevance = "high"; t.MI.keyword = "slip lead"; t.MI.expandedKeyword = "slip lead";
t.MI.keywordMetric = "search_volume";
t.MI.keywords = [{keyword: "slip lead", translation: "牵引绳", search_volume: 1000, category_search_volume: 800, relevance_weight: 0.9, competitive_difficulty: 40, organic_scroll_rate: 0.3, opportunity: {status: "available", score: 72.5, confidence: 1, impact: "high", actionability: "high", dimensions: {demand: {score: 58}, attainability: {score: 60}}, evidence_ids: ["keyword-config:slip lead"]}}];
t.MI.keywordTrend = [{date: "2026-07-19", search_volume: 900, is_complete: true}, {date: "2026-07-20", search_volume: 1000, is_complete: true}];
t.MI.keywordSummary = {trend: [{date: "2026-07-19", relevance_level: "high", keyword_traffic: 120}]};
t.renderKeywordOpportunity(); t.renderKeywordChart();
assert.ok((opportunity.innerHTML.match(/data-mi-keyword-sort=/g) || []).length >= 7);
assert.match(opportunity.innerHTML, /需求分/);
assert.match(opportunity.innerHTML, /机会分/);
assert.ok(opportunity.innerHTML.includes("72.5"));
assert.match(opportunity.innerHTML, /keyword-config:slip lead/);
assert.match(opportunity.innerHTML, /2026-07-19/);
const config = FakeChart.instances.at(-1).config;
assert.ok(config.data.datasets.some((dataset) => dataset.label.includes("相关性层级汇总")));
""")


def test_deep_action_priority_renders_backend_analysis_without_frontend_ranking():
    _run_market_monitor_behavior("""
const t = context.window.__miTest;
const gap = addElement("mi-gap-analysis");
t.MI.comparison = {
  gap_contributors: [{keyword: "slip lead", competitor_asin: "COMP", own_traffic: 20, competitor_traffic: 100, traffic_gap: 80}],
  action_priorities: [{keyword: "slip lead", competitor_asin: "COMP", status: "available", priority: "high", priority_score: 84.5, impact: {score: 80}, actionability: {score: 90}, confidence: 0.9, recommended_action: "Review organic coverage", evidence_ids: ["gap-evidence-1"]}],
};
t.renderGapAnalysis();
assert.match(gap.innerHTML, /high/);
assert.ok(gap.innerHTML.includes("84.5"));
assert.match(gap.innerHTML, /Review organic coverage/);
assert.match(gap.innerHTML, /gap-evidence-1/);
assert.doesNotMatch(gap.innerHTML, />P[1-5]</);
t.MI.comparison = {gap_contributors: [], action_priorities: [], action_priority_analysis: {status: "unavailable", confidence: 0, unavailable_reason: "Backend insufficient gap evidence"}};
t.renderGapAnalysis();
assert.match(gap.innerHTML, /Backend insufficient gap evidence/);
""")


def test_frontend_contains_no_opportunity_or_action_priority_business_formula():
    javascript = JS_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "trafficPotential",
        "opportunityScore",
        "* 70",
        "* 30",
        "P${index + 1}",
    ):
        assert forbidden not in javascript


def test_kpi_and_evidence_ratio_changes_use_percent_units_for_every_metric_type():
    body = r'''
const t = context.window.__miTest;
const kpis = addElement("mi-kpis");
const drawer = addElement("mi-evidence-drawer");
const content = addElement("mi-evidence-content");
addElement("mi-evidence-close");
t.MI.dashboard = {
  kpis: [
    {id: "traffic", label: "Traffic", metric: "total_traffic", current: 1200, previous_complete_day: {value: 1000, change: .2}, latest_7_complete_days: {average: 1100, deviation: .091}},
    {id: "rank", label: "Rank", metric: "rank_in_market", current: 8, previous_complete_day: {value: 10, change: -.2}, latest_7_complete_days: {average: 9, deviation: -.111}},
    {id: "share", label: "Share", metric: "market_share", current: .3, previous_complete_day: {value: .25, change: .2}, latest_7_complete_days: {average: .27, deviation: .111}},
  ],
  insights: [{insight_id: "traffic-risk", severity: "risk", metric: "total_traffic", current: 1200, fact: "Traffic changed", previous_complete_day: {value: 1000, change: .2}, latest_7_complete_days: {average: 1100, deviation: .091}, evidence_ids: ["e1"]}],
  opportunities: [], data_quality: {provider_coverage: 1}
};
t.MI.dashboardInsights = t.MI.dashboard.insights;
t.renderKpis();
const baselineText = kpis.children.flatMap((card) => card.children).flatMap((node) => node.children || []).map((node) => node.textContent).join("|");
assert.match(baselineText, /20\.0%/);
assert.match(baselineText, /-20\.0%/);
assert.match(baselineText, /9\.1%/);
assert.match(baselineText, /-11\.1%/);
t.openEvidence("traffic-risk");
assert.match(content.innerHTML, /20\.0%/);
assert.match(content.innerHTML, /9\.1%/);
'''
    _run_market_monitor_behavior(body)


def test_production_dashboard_http_shape_drives_real_marker_and_evidence_ui(monkeypatch):
    dates = ["2026-07-17", "2026-07-18", "2026-07-19", "2026-07-20"]
    monkeypatch.setattr(
        repository,
        "get_market_trend",
        lambda *args: [
            {"date": date, "total_traffic": value, "top_asin_share": share, "source_request_id": f"market-{date}"}
            for date, value, share in zip(dates, [1600, 1350, 1100, 800], [.25, .29, .34, .41])
        ],
    )
    monkeypatch.setattr(repository, "list_asins", lambda *_: [{"asin": "OWN", "is_own_product": 1}])
    monkeypatch.setattr(
        repository,
        "get_asin_trends",
        lambda *args: [
            {"date": date, "child_asin": "OWN", "total_traffic": value, "high_keyword_share": share, "source_request_id": f"own-{date}"}
            for date, value, share in zip(dates, [160, 170, 180, 190], [.15, .18, .22, .28])
        ],
    )
    monkeypatch.setattr(
        repository,
        "get_latest_run_quality",
        lambda *args: {
            "run_id": "run-20", "status": "success", "target_data_date": "2026-07-20",
            "data_available_through": "2026-07-20",
            "source_summary_json": json.dumps({"source_provider": "xydc", "completeness_score": 1, "provider_coverage": 1}),
        },
    )
    response = TestClient(main.app).get(
        "/api/market-monitor/dashboard",
        params={"market_id": "test", "date_to": "2026-07-20"},
    )
    assert response.status_code == 200
    payload = response.json()
    risk = next(row for row in payload["insights"] if row["severity"] == "risk")

    body = f'''
const t = context.window.__miTest;
addElement("mi-trend"); addElement("mi-trend-state"); addElement("miTrendChart");
const drawer = addElement("mi-evidence-drawer");
const content = addElement("mi-evidence-content"); addElement("mi-evidence-close");
t.MI.dashboard = JSON.parse({json.dumps(json.dumps(payload))});
t.MI.dashboardInsights = t.MI.dashboard.insights;
t.renderTrend();
const marker = FakeChart.instances.at(-1).config.data.datasets.find((dataset) => dataset.insightId === {json.dumps(risk["insight_id"])});
assert.ok(marker, "production dashboard marker must reach the chart");
t.openEvidence({json.dumps(risk["insight_id"])});
assert.match(content.innerHTML, /Market demand may be weakening/);
assert.match(content.innerHTML, /market-2026-07-20/);
'''
    _run_market_monitor_behavior(body)
