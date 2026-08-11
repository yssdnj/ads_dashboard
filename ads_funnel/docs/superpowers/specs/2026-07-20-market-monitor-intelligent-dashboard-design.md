# Market Monitor Intelligent Dashboard Design

Date: 2026-07-20

## Goal

Upgrade the existing Market Insights tab from a collection of KPI cards, charts,
and tables into a decision-oriented monitoring workspace. The primary workflow is:

```text
Daily command center
  -> detect an anomaly or opportunity
  -> inspect evidence
  -> drill into market / ASIN / keyword analysis
  -> compare own product with competitors
  -> produce an evidence-backed action recommendation
```

The default audience is an operator who checks the market every day and needs to
understand what changed, why it changed, and what to investigate next.

## Confirmed Product Decisions

- Default entry: daily monitoring.
- Default comparison baselines: yesterday and the latest seven-day average.
- Intelligent output level: anomaly, possible cause, evidence, confidence, and
  recommended action.
- ASIN comparison: own product plus at most three competitors.
- Metric navigation: two levels, first analysis scenario and then metric.
- Homepage layout: command center.
- Anomaly detail layout: anomaly inbox / evidence drawer.
- Deep-analysis layout: comparison canvas.
- Color direction: blue analysis system.

## Information Architecture

### 1. Daily Command Center

The command center answers four questions in order:

1. Is the latest data complete and trustworthy?
2. What changed?
3. What is likely to have caused the change?
4. Where should the user investigate or act next?

It contains:

- global context and data-freshness bar;
- primary KPIs;
- linked trend analysis;
- daily intelligent summary;
- anomaly event stream;
- opportunity ranking;
- entry points into market, ASIN, and keyword analysis.

### 2. Anomaly Inbox and Evidence Drawer

Anomalies are ordered by impact and confidence. Users may filter by market,
ASIN, competitor, or keyword. Selecting an anomaly opens an evidence drawer
without immediately leaving the command center.

The drawer shows:

- conclusion and severity;
- triggering metric;
- comparison baseline;
- duration and direction of the change;
- contributing ASINs or keywords;
- source coverage and confidence;
- suggested next action;
- deep-analysis link that preserves the current context.

### 3. Deep Comparison

Deep comparison contains four analysis scenarios:

- market share;
- keyword competition;
- advertising competition;
- growth quality.

Within a scenario, users switch between only the metrics relevant to that
scenario. The comparison set is always the own product plus zero to three
competitors. A saved competitor preset may populate these objects, but users can
replace them.

The page contains:

- aligned multi-object trend chart;
- yesterday and seven-day-average comparisons;
- fixed colors per comparison object;
- object comparison table;
- contribution / gap explanation;
- keyword opportunity matrix;
- action-priority grouping.

## Global Context and Interaction

The following context is shared across the command center, evidence drawer, and
deep-analysis pages:

- market;
- latest available data date;
- selected date range;
- comparison baselines;
- own ASIN;
- selected competitors;
- analysis scenario;
- selected metric;
- keyword relevance filter.

Drilling down inherits the current context. Users must not reselect date,
market, or comparison objects after every navigation.

All visible widgets react to global filters. Chart hover aligns values for all
objects on the same date. Clicking an anomaly marker opens its evidence drawer.

## Daily Command Center Content

### Data Quality and Freshness

Always show:

- target data date;
- data available through date;
- collection time and timezone;
- providers used;
- provider coverage;
- missing data types;
- completeness score.

If the latest complete data date is older than expected, label the page as stale.
Do not present missing collection data as a market decline.

### KPI Groups

The initial KPI groups are:

- market size / total traffic;
- own traffic share;
- core-keyword visibility;
- competitive pressure.

Each KPI includes:

- current value;
- change from yesterday;
- deviation from the latest seven-day average;
- short trend direction;
- semantic status when supported by evidence.

An increase or decrease is not inherently good or bad. Status depends on the
metric definition.

### Trend Analysis

The main chart supports:

- 7-day, 30-day, and custom ranges;
- scenario then metric switching;
- own product plus up to three competitors;
- seven-day moving average;
- anomaly markers;
- collection or source-change markers;
- same-date hover comparison.

### Intelligent Summary

Each insight separates:

- **Fact:** directly calculated from stored data.
- **Inference:** a possible explanation supported by linked evidence.
- **Recommendation:** an action the operator may consider.

Every inference and recommendation includes its evidence and confidence. When
evidence is insufficient, the UI shows only the fact.

## Keyword Source of Truth

The only keyword membership and relevance source for this market is:

```text
ads_funnel/docs/US_Dog Slip Leads_关键词洞察列表_2026-06_高中低关键词.xlsx
```

The `关键词列表` sheet contains 256 keywords:

| Source value | Stored level | Count | Share |
|---|---:|---:|---:|
| 高相关 | `high` | 77 | 30.1% |
| 中相关 | `mid` | 40 | 15.6% |
| 低相关 | `low` | 139 | 54.3% |

Rules:

- Remove the previous `strong` tier.
- Do not merge the Excel list with previously supplied or manually seeded
  keywords.
- Upsert all 256 Excel keywords into `keyword_pool_config`.
- Mark existing keywords that are not in the Excel list as `inactive`; do not
  physically delete them.
- Store source provider `xydc` and source version month `2026-06`.
- Store the workbook's `类目相关度` value as `relevance_weight`; do not replace it
  with a hard-coded tier weight.
- Preserve the source classification; daily performance must not automatically
  rewrite relevance.
- A future replacement workbook may update membership and classification, but
  the import must record its version date.
- Duplicate normalized keywords or invalid relevance values fail validation and
  produce an import report instead of silently guessing.

### Keyword UI

The keyword dashboard provides:

- all / high / mid / low filters with counts;
- search and multi-condition filtering;
- sortable detail table;
- monthly trend switching for search volume, category-related search volume,
  competitive difficulty, and organic-scroll rate;
- relevance-layer aggregate trends;
- keyword opportunity matrix;
- per-keyword history expansion.

The detail table can expose available workbook fields including keyword,
translation, keyword rank, monthly search volume, category relevance,
category-related search volume, suggested CPC and range, average click conversion
rate, competitive difficulty, and organic-scroll rate.

## Database Design

### Configuration

`keyword_pool_config` stores the active source-of-truth keyword pool:

- `market_id`;
- `keyword`;
- `relevance_level` (`high`, `mid`, or `low`);
- `relevance_weight`, populated from the workbook's `类目相关度`;
- `status`;
- source channel / provider / tool;
- source version month;
- created and updated timestamps.

Keywords outside the latest source workbook become inactive so historical
snapshots remain referentially meaningful.

### Daily Facts

The following daily tables continue to store keyword and relevance values with
the captured metrics:

- `keyword_snapshot_daily`;
- `asin_keyword_snapshot_daily`;
- `keyword_asin_competition_daily`.

Historical daily rows keep the relevance captured for that date. Updating the
current configuration must not rewrite past classifications.

Existing columns and aggregations tied to `strong` must be migrated to the
three-tier model. API validation and ordering must accept only `high`, `mid`, and
`low` after migration.

## Analysis Layer

The backend computes and returns presentation-ready analysis rather than making
the frontend reimplement business rules.

Required derived values include:

- prior-day difference and percentage change;
- difference from latest seven-day average;
- consecutive-direction duration;
- anomaly score;
- data completeness;
- competitor contribution to a gap;
- keyword contribution to a gap;
- confidence score;
- action-priority score.

Confidence considers at least data completeness, agreement between available
providers, and persistence of the observed trend. Recommendation priority uses
impact, actionability, and confidence.

## Color System

Use the existing blue product identity as the base and refine it into a
restrained analysis palette.

- Primary interaction and current selection: blue.
- Own product: stable, darkest blue series.
- Competitors: fixed violet, orange, and cyan series; colors do not change after
  sorting.
- High relevance: teal-green label.
- Mid relevance: amber label.
- Low relevance: neutral blue-gray label.
- Risk: red, only for semantic risk.
- Opportunity / confirmed positive state: green.

Low relevance is not an error and must not be red. Color never carries meaning
alone; pair it with text, direction, icon, or label. Maintain accessible contrast
for normal text and interactive states.

## Loading, Empty, and Error States

- Use section skeletons while data loads; do not block the entire dashboard with
  one central spinner.
- A missing metric affects only its component.
- Empty states explain which filter or missing collection caused the absence.
- Partial provider failure displays a degraded-data state and lowers confidence.
- Stale data retains the last complete view with a prominent freshness warning.
- API errors preserve current filters and provide a retry action.
- Invalid keyword imports do not partially replace the active keyword pool.

## Testing and Acceptance Criteria

### Keyword Import

- Import exactly 256 normalized keywords.
- Validate counts: high 77, mid 40, low 139.
- Reject invalid relevance values.
- Report normalized duplicates.
- Mark non-workbook legacy keywords inactive.
- Preserve historical daily rows.
- Record `xydc` and version month `2026-06`.
- Preserve each keyword's workbook `类目相关度` as its relevance weight.

### Backend and Analysis

- API filters accept only high, mid, and low.
- No active aggregation depends on `strong` after migration.
- Yesterday comparison handles a missing previous date.
- Seven-day average uses available complete dates and reports sample size.
- Repeated collection is idempotent.
- Partial provider failure lowers completeness and confidence.
- Contributions reconcile to the displayed gap within documented rounding.

### Frontend

- Global filters update all dependent sections.
- Drill-down preserves context.
- Own product plus three competitors renders consistently across chart and table.
- Scenario selection exposes only relevant metrics.
- Chart hover aligns all visible objects by date.
- Empty, stale, loading, partial, and error states are legible.
- Status remains understandable without color.
- Desktop, narrow desktop, tablet, and small-screen layouts avoid clipping or
  unreadable tables.

## Out of Scope

- Automatically changing keyword relevance based on daily performance.
- Treating generated recommendations as automatic execution instructions.
- Comparing more than three competitors in the primary trend chart.
- Deleting historical keyword or snapshot records when the active workbook
  changes.
- Replacing the existing collector with frontend-triggered source calls.
## 2026-07-21 Confirmed UI Additions

- Monitoring objects are parent-grouped representative child ASINs, not a flat
  list of every market child. Prefer own/representative flags and then latest
  complete-day traffic when choosing the main children.
- The selector is a keyboard-accessible custom listbox whose selected value and
  options show product thumbnail, child ASIN, parent ASIN, and traffic context.
  Product image URLs are persisted from the collection source; missing values
  render a local placeholder and are never inferred from an undocumented CDN.
- Keyword relevance uses only the confirmed June 2026 Xiyou Excel catalog and
  has exactly three views: high, mid, and low. High is the default. Legacy
  `strong` and an aggregate `all` relevance view are excluded.
