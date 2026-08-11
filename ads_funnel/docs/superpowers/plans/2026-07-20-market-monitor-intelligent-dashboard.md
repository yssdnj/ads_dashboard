# Market Monitor Intelligent Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current four-tier, manually seeded market-monitor UI with a package-structured three-tier keyword catalog, evidence-backed dashboard APIs, and a linked daily-monitoring frontend.

**Architecture:** Move market-monitor responsibilities into `ads_funnel.api.market_monitoring`, split by schema, repository, keyword catalog, collector, analytics, service, and router. Keep the three existing flat modules as compatibility facades while callers migrate. Split Market Insights CSS and JavaScript out of `template.html`, then inline those assets during HTML generation so exported reports remain standalone.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, MySQL, openpyxl, pytest, vanilla JavaScript, Chart.js, HTML/CSS.

## Global Constraints

- The source-of-truth workbook is `ads_funnel/docs/US_Dog Slip Leads_关键词洞察列表_2026-06_高中低关键词.xlsx`.
- Import exactly 256 keywords: `high=77`, `mid=40`, `low=139`.
- Store workbook `类目相关度` as `relevance_weight`; do not substitute hard-coded tier weights.
- Remove `strong` from active configuration, API validation, UI filters, and active aggregations.
- Mark legacy keywords outside the workbook inactive; never delete historical snapshots.
- Own-product comparison supports zero to three competitors.
- Default baselines are previous complete day and latest seven complete days.
- FastAPI must never call MCP or provider APIs.
- Preserve standalone HTML export behavior.
- Do not modify `legacy/`.
- Do not commit or push unless the user explicitly requests it.
- Every implementation task receives one spec-compliance review and one code-quality review before being marked complete.

## Package and File Map

```text
ads_funnel/api/market_monitoring/
├── __init__.py          public constants and package version
├── constants.py         market id, tiers, source workbook metadata
├── schema.py            database creation and idempotent migrations
├── repository.py        SQL reads and keyword-catalog transaction
├── keyword_catalog.py   workbook parsing, normalization, validation
├── collector.py         raw/daily persistence functions
├── analytics.py         pure baseline, anomaly, confidence calculations
├── service.py           command-center and comparison payload assembly
└── router.py            FastAPI request validation and response mapping

ads_funnel/api/market_monitor_db.py          compatibility re-export
ads_funnel/api/market_monitor_collector.py   compatibility re-export
ads_funnel/api/market_monitor.py             compatibility router re-export

ads_funnel/frontend/static/market-monitor.css
ads_funnel/frontend/static/market-monitor.js
ads_funnel/scripts/market_monitor_import_keywords.py
```

Responsibility rule: SQL belongs in `schema.py` or `repository.py`; calculations
belong in `analytics.py`; endpoint composition belongs in `service.py`; HTTP
validation belongs in `router.py`; DOM rendering belongs in
`market-monitor.js`. No task adds new market-monitor business logic to
`main.py`, `export.py`, or inline `<script>` blocks.

---

### Task 1: Establish the `market_monitoring` Package and Compatibility Facades

**Files:**
- Create: `ads_funnel/api/market_monitoring/__init__.py`
- Create: `ads_funnel/api/market_monitoring/constants.py`
- Create: `ads_funnel/api/market_monitoring/schema.py`
- Create: `ads_funnel/api/market_monitoring/repository.py`
- Create: `ads_funnel/api/market_monitoring/collector.py`
- Create: `ads_funnel/api/market_monitoring/router.py`
- Modify: `ads_funnel/api/market_monitor_db.py`
- Modify: `ads_funnel/api/market_monitor_collector.py`
- Modify: `ads_funnel/api/market_monitor.py`
- Test: `ads_funnel/tests/test_market_monitor_package.py`

**Interfaces:**
- Produces: `market_monitoring.repository` with the existing public DB function names.
- Produces: `market_monitoring.collector` with the existing collector public function names.
- Produces: `market_monitoring.router.router`.
- Compatibility facades preserve all current imports while implementation moves.

- [ ] **Step 1: Write a failing package-contract test**

```python
from ads_funnel.api import market_monitor, market_monitor_collector, market_monitor_db
from ads_funnel.api.market_monitoring import collector, repository, router


def test_compatibility_modules_export_package_objects():
    assert market_monitor.router is router.router
    assert market_monitor_db.list_keywords is repository.list_keywords
    assert market_monitor_db.get_market_engine is repository.get_market_engine
    assert market_monitor_collector.persist_daily_source_batch is collector.persist_daily_source_batch
```

- [ ] **Step 2: Run the package-contract test and verify failure**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_package.py -q
```

Expected: collection fails because `ads_funnel.api.market_monitoring` does not exist.

- [ ] **Step 3: Create constants and package exports**

```python
# ads_funnel/api/market_monitoring/constants.py
from pathlib import Path

MARKET_DB_NAME = "ads_funnel_market"
DEFAULT_MARKET_ID = "slip_lead_leash"
RELEVANCE_LEVELS = ("high", "mid", "low")
RELEVANCE_ORDER_SQL = "FIELD(relevance_level, 'high', 'mid', 'low')"
KEYWORD_SOURCE_PROVIDER = "xydc"
KEYWORD_SOURCE_VERSION = "2026-06"
KEYWORD_WORKBOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "US_Dog Slip Leads_关键词洞察列表_2026-06_高中低关键词.xlsx"
)
EXPECTED_KEYWORD_COUNTS = {"high": 77, "mid": 40, "low": 139}
```

```python
# ads_funnel/api/market_monitoring/__init__.py
from .constants import DEFAULT_MARKET_ID, MARKET_DB_NAME, RELEVANCE_LEVELS

__all__ = ["DEFAULT_MARKET_ID", "MARKET_DB_NAME", "RELEVANCE_LEVELS"]
```

- [ ] **Step 4: Move implementations without changing behavior**

Move the full contents of `market_monitor_db.py` into `repository.py`, the full
contents of `market_monitor_collector.py` into `collector.py`, and the router
contents of `market_monitor.py` into `router.py`. Update relative imports to:

```python
from .constants import DEFAULT_MARKET_ID, MARKET_DB_NAME
from . import repository
```

Move `_schema_statements`, database creation, and initialization helpers from
`repository.py` into `schema.py`. `repository.py` must expose wrappers so the
existing public API remains stable:

```python
from . import schema

get_market_engine = schema.get_market_engine
ensure_market_database = schema.ensure_market_database
init_market_db = schema.init_market_db
```

- [ ] **Step 5: Replace flat modules with compatibility facades**

```python
# ads_funnel/api/market_monitor_db.py
"""Compatibility facade for ads_funnel.api.market_monitoring.repository."""
from .market_monitoring.repository import *  # noqa: F401,F403
```

```python
# ads_funnel/api/market_monitor_collector.py
"""Compatibility facade for ads_funnel.api.market_monitoring.collector."""
from .market_monitoring.collector import *  # noqa: F401,F403
```

```python
# ads_funnel/api/market_monitor.py
"""Compatibility facade for the market-monitor FastAPI router."""
from .market_monitoring.router import router

__all__ = ["router"]
```

- [ ] **Step 6: Run existing and package-contract tests**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_package.py ads_funnel/tests/test_market_monitor_db.py ads_funnel/tests/test_market_monitor_collector.py ads_funnel/tests/test_market_monitor_api.py -q
```

Expected: all previously passing tests still pass; package-contract test passes.

- [ ] **Step 7: Run the two required review gates**

Spec reviewer checks only file responsibility, compatibility, and absence of
behavior changes. Code-quality reviewer checks circular imports, wildcard facade
scope, import-time DB access, and module sizes. Apply all accepted findings and
rerun Step 6.

---

### Task 2: Parse and Validate the Xydc Keyword Catalog

**Files:**
- Create: `ads_funnel/api/market_monitoring/keyword_catalog.py`
- Test: `ads_funnel/tests/test_market_monitor_keyword_catalog.py`

**Interfaces:**
- Produces: `KeywordCatalogRow`.
- Produces: `load_keyword_catalog(path: Path) -> list[KeywordCatalogRow]`.
- Produces: `validate_keyword_catalog(rows) -> dict[str, int]`.
- Consumed by Task 3 import transaction and CLI.

- [ ] **Step 1: Write parser and validation tests**

```python
from pathlib import Path

import pytest

from ads_funnel.api.market_monitoring.keyword_catalog import (
    CatalogValidationError,
    KeywordCatalogRow,
    load_keyword_catalog,
    normalize_keyword,
    validate_keyword_catalog,
)

WORKBOOK = Path(__file__).parents[1] / "docs" / "US_Dog Slip Leads_关键词洞察列表_2026-06_高中低关键词.xlsx"


def test_source_workbook_has_confirmed_three_tier_distribution():
    rows = load_keyword_catalog(WORKBOOK)
    assert len(rows) == 256
    assert validate_keyword_catalog(rows) == {"high": 77, "mid": 40, "low": 139}
    first = rows[0]
    assert first.keyword == "slip leads for dogs"
    assert first.relevance_level == "high"
    assert first.relevance_weight == pytest.approx(0.8888)


def test_normalize_keyword_collapses_case_and_whitespace():
    assert normalize_keyword("  Slip   Lead ") == "slip lead"


def test_duplicate_normalized_keyword_fails_validation():
    row = KeywordCatalogRow("slip lead", "牵引绳", "high", 0.925, None, None, None, None, None, None, None, None)
    with pytest.raises(CatalogValidationError, match="duplicate keyword: slip lead"):
        validate_keyword_catalog([row, row], expected_counts=None)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_keyword_catalog.py -q
```

Expected: import error for missing `keyword_catalog` module.

- [ ] **Step 3: Implement typed parsing and strict validation**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook

from .constants import EXPECTED_KEYWORD_COUNTS, RELEVANCE_LEVELS

SOURCE_TO_LEVEL = {"高相关": "high", "中相关": "mid", "低相关": "low"}


class CatalogValidationError(ValueError):
    pass


@dataclass(frozen=True)
class KeywordCatalogRow:
    keyword: str
    translation: str | None
    relevance_level: str
    relevance_weight: float
    keyword_rank: int | None
    search_volume: float | None
    category_search_volume: float | None
    cpc: float | None
    cpc_range: str | None
    click_conversion_rate: float | None
    competitive_difficulty: float | None
    organic_scroll_rate: float | None


def normalize_keyword(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def load_keyword_catalog(path: Path) -> list[KeywordCatalogRow]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook["关键词列表"]
    headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    index = {name: position for position, name in enumerate(headers)}
    required = {"关键词 (数据来源于西柚洞察)", "翻译", "关键词排名", "月搜索量", "相关性", "类目相关度", "类目相关搜索量", "CPC建议竞价($)", "建议竞价范围($)", "点击转化率(均值)", "竞争难度", "自然位滚动率"}
    missing = sorted(required - set(index))
    if missing:
        raise CatalogValidationError(f"missing columns: {', '.join(missing)}")
    rows: list[KeywordCatalogRow] = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        keyword = normalize_keyword(values[index["关键词 (数据来源于西柚洞察)"]])
        if not keyword:
            continue
        source_level = str(values[index["相关性"]] or "").strip()
        if source_level not in SOURCE_TO_LEVEL:
            raise CatalogValidationError(f"invalid relevance for {keyword}: {source_level}")
        weight_value = values[index["类目相关度"]]
        if weight_value is None:
            raise CatalogValidationError(f"missing 类目相关度 for {keyword}")
        rows.append(KeywordCatalogRow(
            keyword=keyword,
            translation=values[index["翻译"]],
            relevance_level=SOURCE_TO_LEVEL[source_level],
            relevance_weight=float(weight_value),
            keyword_rank=values[index["关键词排名"]],
            search_volume=values[index["月搜索量"]],
            category_search_volume=values[index["类目相关搜索量"]],
            cpc=values[index["CPC建议竞价($)"]],
            cpc_range=values[index["建议竞价范围($)"]],
            click_conversion_rate=values[index["点击转化率(均值)"]],
            competitive_difficulty=values[index["竞争难度"]],
            organic_scroll_rate=values[index["自然位滚动率"]],
        ))
    validate_keyword_catalog(rows)
    return rows


def validate_keyword_catalog(rows: Iterable[KeywordCatalogRow], expected_counts=EXPECTED_KEYWORD_COUNTS) -> dict[str, int]:
    counts = {level: 0 for level in RELEVANCE_LEVELS}
    seen: set[str] = set()
    for row in rows:
        if row.keyword in seen:
            raise CatalogValidationError(f"duplicate keyword: {row.keyword}")
        seen.add(row.keyword)
        if row.relevance_level not in counts:
            raise CatalogValidationError(f"invalid relevance: {row.relevance_level}")
        if not 0 <= row.relevance_weight <= 1:
            raise CatalogValidationError(f"invalid 类目相关度 for {row.keyword}: {row.relevance_weight}")
        counts[row.relevance_level] += 1
    if expected_counts is not None and counts != expected_counts:
        raise CatalogValidationError(f"unexpected relevance counts: {counts}")
    return counts
```

- [ ] **Step 4: Run parser tests**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_keyword_catalog.py -q
```

Expected: 3 tests pass and the source workbook reads 256 rows.

- [ ] **Step 5: Run both review gates and rerun Step 4**

Spec reviewer checks exact workbook, counts, tier mapping, and `类目相关度`.
Code-quality reviewer checks workbook closure, numeric coercion, duplicate
normalization, error messages, and import-time side effects.

---

### Task 3: Add Idempotent Three-Tier Schema Migration and Catalog Import

**Files:**
- Modify: `ads_funnel/api/market_monitoring/schema.py`
- Modify: `ads_funnel/api/market_monitoring/repository.py`
- Create: `ads_funnel/scripts/market_monitor_import_keywords.py`
- Test: `ads_funnel/tests/test_market_monitor_keyword_import.py`
- Modify: `ads_funnel/tests/test_market_monitor_db.py`

**Interfaces:**
- Produces: `repository.sync_keyword_catalog(market_id, rows, source_version) -> dict`.
- Produces CLI `python ads_funnel/scripts/market_monitor_import_keywords.py --dry-run`.
- `sync_keyword_catalog` is one transaction: validate, upsert, deactivate missing, return counts.

- [ ] **Step 1: Write integration tests for import and historical preservation**

```python
def test_sync_keyword_catalog_upserts_three_tiers_and_deactivates_legacy(monkeypatch):
    rows = load_keyword_catalog(KEYWORD_WORKBOOK_PATH)
    result = repository.sync_keyword_catalog(DEFAULT_MARKET_ID, rows, "2026-06")
    assert result == {"active": 256, "high": 77, "mid": 40, "low": 139, "deactivated": result["deactivated"]}
    active = repository.list_keywords(DEFAULT_MARKET_ID)
    assert len(active) == 256
    assert {row["relevance_level"] for row in active} == {"high", "mid", "low"}
    assert next(row for row in active if row["keyword"] == "slip lead")["relevance_weight"] == pytest.approx(0.925)


def test_catalog_sync_does_not_update_historical_snapshot_relevance():
    before = repository.get_keyword_trend(DEFAULT_MARKET_ID, "slip lead", "2026-07-20", "2026-07-20")
    repository.sync_keyword_catalog(DEFAULT_MARKET_ID, load_keyword_catalog(KEYWORD_WORKBOOK_PATH), "2026-06")
    after = repository.get_keyword_trend(DEFAULT_MARKET_ID, "slip lead", "2026-07-20", "2026-07-20")
    assert after == before
```

- [ ] **Step 2: Run tests and verify schema/import failure**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_keyword_import.py -q
```

Expected: failure because `source_version_month` and `sync_keyword_catalog` do not exist.

- [ ] **Step 3: Add idempotent schema migration**

After base tables are created, call `apply_migrations(conn)`. The migration must
query `information_schema.COLUMNS` before adding fields:

```python
def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=:schema AND TABLE_NAME=:table AND COLUMN_NAME=:column
    """), {"schema": MARKET_DB_NAME, "table": table, "column": column}).scalar())


def apply_migrations(conn) -> None:
    additions = {
        "source_version_month": "VARCHAR(7) NULL",
        "translation": "VARCHAR(512) NULL",
        "keyword_rank": "INT NULL",
        "search_volume": "DOUBLE NULL",
        "category_search_volume": "DOUBLE NULL",
        "cpc": "DOUBLE NULL",
        "cpc_range": "VARCHAR(64) NULL",
        "click_conversion_rate": "DOUBLE NULL",
        "competitive_difficulty": "DOUBLE NULL",
        "organic_scroll_rate": "DOUBLE NULL",
    }
    for column, ddl in additions.items():
        if not _column_exists(conn, "keyword_pool_config", column):
            conn.execute(text(f"ALTER TABLE keyword_pool_config ADD COLUMN `{column}` {ddl}"))
```

Do not drop legacy `strong_*` fact columns in this migration. They remain as
zero-valued compatibility columns until a separately approved destructive schema
cleanup.

- [ ] **Step 4: Implement the transactional catalog sync**

```python
def sync_keyword_catalog(market_id, rows, source_version: str) -> dict[str, int]:
    counts = validate_keyword_catalog(rows)
    active_keywords = [row.keyword for row in rows]
    with get_market_engine().begin() as conn:
        for row in rows:
            conn.execute(text("""
                INSERT INTO keyword_pool_config
                  (market_id, keyword, translation, relevance_level, relevance_weight,
                   source, source_version_month, is_core_keyword, status,
                   source_channel, source_provider, keyword_rank, search_volume,
                   category_search_volume, cpc, cpc_range, click_conversion_rate,
                   competitive_difficulty, organic_scroll_rate)
                VALUES
                  (:market_id, :keyword, :translation, :relevance_level, :relevance_weight,
                   'xydc_keyword_insights_export', :source_version, 0, 'active',
                   'xlsx', 'xydc', :keyword_rank, :search_volume,
                   :category_search_volume, :cpc, :cpc_range, :click_conversion_rate,
                   :competitive_difficulty, :organic_scroll_rate)
                ON DUPLICATE KEY UPDATE
                  translation=VALUES(translation), relevance_level=VALUES(relevance_level),
                  relevance_weight=VALUES(relevance_weight), source=VALUES(source),
                  source_version_month=VALUES(source_version_month), status='active',
                  source_channel=VALUES(source_channel), source_provider=VALUES(source_provider),
                  keyword_rank=VALUES(keyword_rank), search_volume=VALUES(search_volume),
                  category_search_volume=VALUES(category_search_volume), cpc=VALUES(cpc),
                  cpc_range=VALUES(cpc_range), click_conversion_rate=VALUES(click_conversion_rate),
                  competitive_difficulty=VALUES(competitive_difficulty),
                  organic_scroll_rate=VALUES(organic_scroll_rate), updated_at=NOW()
            """), {"market_id": market_id, "source_version": source_version, **row.__dict__})
        deactivated = conn.execute(text("""
            UPDATE keyword_pool_config
            SET status='inactive', updated_at=NOW()
            WHERE market_id=:market_id AND status='active'
              AND keyword NOT IN :active_keywords
        """).bindparams(bindparam("active_keywords", expanding=True)), {
            "market_id": market_id,
            "active_keywords": active_keywords,
        }).rowcount
    return {"active": len(rows), **counts, "deactivated": int(deactivated or 0)}
```

- [ ] **Step 5: Add dry-run and apply CLI**

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=KEYWORD_WORKBOOK_PATH)
    parser.add_argument("--market-id", default=DEFAULT_MARKET_ID)
    parser.add_argument("--source-version", default=KEYWORD_SOURCE_VERSION)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rows = load_keyword_catalog(args.workbook)
    counts = validate_keyword_catalog(rows)
    if args.dry_run:
        print(json.dumps({"active": len(rows), **counts}, ensure_ascii=False))
        return 0
    schema.init_market_db(seed_defaults=False)
    print(json.dumps(repository.sync_keyword_catalog(args.market_id, rows, args.source_version), ensure_ascii=False))
    return 0
```

- [ ] **Step 6: Verify dry-run, apply, and idempotency**

Run:

```powershell
python ads_funnel/scripts/market_monitor_import_keywords.py --dry-run
python ads_funnel/scripts/market_monitor_import_keywords.py
python ads_funnel/scripts/market_monitor_import_keywords.py
python -m pytest ads_funnel/tests/test_market_monitor_keyword_import.py ads_funnel/tests/test_market_monitor_db.py -q
```

Expected dry-run JSON: `{"active":256,"high":77,"mid":40,"low":139}`.
Both apply runs succeed; second run has `deactivated=0`; tests pass.

- [ ] **Step 7: Run both review gates and rerun Step 6 tests**

Spec reviewer checks transactional replacement, inactive legacy behavior, source
metadata, and historical preservation. Code-quality reviewer checks SQL injection,
expanding bind parameters, transaction rollback, idempotency, and CLI exit paths.

---

### Task 4: Migrate Collector, Query, and API Contracts to Three Tiers

**Files:**
- Modify: `ads_funnel/api/market_monitoring/repository.py`
- Modify: `ads_funnel/api/market_monitoring/collector.py`
- Modify: `ads_funnel/api/market_monitoring/router.py`
- Modify: `ads_funnel/scripts/market_monitor_collect_daily.py`
- Modify: `ads_funnel/tests/test_market_monitor_api.py`
- Modify: `ads_funnel/tests/test_market_monitor_collector.py`
- Modify: `ads_funnel/tests/test_market_monitor_daily_script.py`

**Interfaces:**
- `/api/market-monitor/keywords?relevance_level=high|mid|low`.
- Collection plan uses high keywords as core auxiliary calls; no `strong` branch.
- Legacy fact columns remain present but are written as zero.

- [ ] **Step 1: Replace test fixtures and add invalid-tier API test**

Change all test snapshot rows from `strong` to the workbook-backed tier and
weight, for example `slip lead -> high, 0.925`. Add:

```python
def test_keywords_rejects_removed_strong_tier(client):
    response = client.get("/api/market-monitor/keywords", params={"relevance_level": "strong"})
    assert response.status_code == 422


def test_keywords_accepts_three_active_tiers(client):
    for level in ("high", "mid", "low"):
        assert client.get("/api/market-monitor/keywords", params={"relevance_level": level}).status_code == 200
```

- [ ] **Step 2: Run targeted tests and verify failure**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_api.py ads_funnel/tests/test_market_monitor_collector.py ads_funnel/tests/test_market_monitor_daily_script.py -q
```

Expected: removed-tier test fails because router still accepts `strong`.

- [ ] **Step 3: Update query ordering and router validation**

```python
relevance_level: str | None = Query(default=None, pattern="^(high|mid|low)$")
```

```sql
ORDER BY FIELD(relevance_level, 'high', 'mid', 'low'),
         search_volume IS NULL, search_volume DESC, keyword
```

- [ ] **Step 4: Update collection-plan selection**

Replace `strong_keywords` with:

```python
high_keywords = [row["keyword"] for row in active_keywords if row["relevance_level"] == "high"]
auxiliary_calls = _auxiliary_calls(country, core_asins, high_keywords)
```

When writing market and ASIN snapshot compatibility fields, always set:

```python
row.setdefault("strong_relevance_traffic", 0)
row.setdefault("strong_keyword_share", 0)
```

No API response or active calculation may display those fields.

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_api.py ads_funnel/tests/test_market_monitor_collector.py ads_funnel/tests/test_market_monitor_daily_script.py -q
```

Expected: all pass with no fixture containing `relevance_level="strong"`.

- [ ] **Step 6: Run both review gates and rerun Step 5**

Spec reviewer searches active code and fixtures for `strong`. Code-quality
reviewer checks backward-compatible writes and avoids destructive DDL.

---

### Task 5: Implement Pure Baseline, Anomaly, Confidence, and Priority Analytics

**Files:**
- Create: `ads_funnel/api/market_monitoring/analytics.py`
- Test: `ads_funnel/tests/test_market_monitor_analytics.py`

**Interfaces:**
- Produces: `MetricComparison` and `Insight` dataclasses.
- Produces: `compare_metric(rows, metric, current_date=None) -> MetricComparison`.
- Produces: `score_confidence(completeness, provider_agreement, persistence_days) -> float`.
- Produces: `build_insight(insight_id, severity, scope, fact, confidence, evidence, inference=None, recommendation=None) -> Insight`.

- [ ] **Step 1: Write pure unit tests**

```python
def test_compare_metric_uses_previous_complete_day_and_prior_seven_rows():
    rows = [{"date": f"2026-07-{day:02d}", "value": value, "is_complete": True}
            for day, value in zip(range(11, 20), [80, 90, 100, 110, 100, 90, 100, 105, 120])]
    result = compare_metric(rows, "value")
    assert result.current == 120
    assert result.previous == 105
    assert result.previous_change == pytest.approx(15 / 105)
    assert result.baseline_sample_size == 7
    assert result.seven_day_average == pytest.approx((90 + 100 + 110 + 100 + 90 + 100 + 105) / 7)


def test_compare_metric_skips_incomplete_days():
    rows = [
        {"date": "2026-07-18", "value": 100, "is_complete": True},
        {"date": "2026-07-19", "value": 0, "is_complete": False},
        {"date": "2026-07-20", "value": 120, "is_complete": True},
    ]
    assert compare_metric(rows, "value").previous == 100


def test_confidence_is_bounded_and_reduced_by_partial_data():
    assert score_confidence(1, 1, 3) == 1
    assert 0 < score_confidence(0.5, 0.5, 1) < 0.5
```

- [ ] **Step 2: Run tests and verify module failure**

Run: `python -m pytest ads_funnel/tests/test_market_monitor_analytics.py -q`

Expected: missing-module failure.

- [ ] **Step 3: Implement pure dataclasses and functions**

```python
@dataclass(frozen=True)
class MetricComparison:
    current: float | None
    previous: float | None
    previous_change: float | None
    seven_day_average: float | None
    seven_day_deviation: float | None
    baseline_sample_size: int
    persistence_days: int


def _ratio_change(current, baseline):
    if current is None or baseline in (None, 0):
        return None
    return (current - baseline) / abs(baseline)


def compare_metric(rows, metric: str, current_date: str | None = None) -> MetricComparison:
    complete = [row for row in rows if row.get("is_complete", True) and row.get(metric) is not None]
    if current_date:
        complete = [row for row in complete if str(row["date"]) <= current_date]
    complete.sort(key=lambda row: str(row["date"]))
    if not complete:
        return MetricComparison(None, None, None, None, None, 0, 0)
    current = float(complete[-1][metric])
    previous = float(complete[-2][metric]) if len(complete) > 1 else None
    baseline_rows = complete[max(0, len(complete) - 8):-1]
    average = sum(float(row[metric]) for row in baseline_rows) / len(baseline_rows) if baseline_rows else None
    direction = 0 if previous is None else (1 if current > previous else -1 if current < previous else 0)
    persistence = 0
    for left, right in zip(reversed(complete[:-1]), reversed(complete[1:])):
        step = 1 if float(right[metric]) > float(left[metric]) else -1 if float(right[metric]) < float(left[metric]) else 0
        if step != direction or direction == 0:
            break
        persistence += 1
    return MetricComparison(current, previous, _ratio_change(current, previous), average,
                            _ratio_change(current, average), len(baseline_rows), persistence)


def score_confidence(completeness: float, provider_agreement: float, persistence_days: int) -> float:
    persistence = min(max(persistence_days, 0) / 3, 1)
    return round(min(max(0.5 * completeness + 0.3 * provider_agreement + 0.2 * persistence, 0), 1), 3)
```

- [ ] **Step 4: Add `Insight` with fact/inference/recommendation separation**

```python
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


def build_insight(*, insight_id, severity, scope, fact, confidence, evidence,
                  inference=None, recommendation=None) -> Insight:
    if confidence < 0.6:
        inference = None
        recommendation = None
    return Insight(insight_id, severity, scope, fact, inference, recommendation,
                   confidence, tuple(evidence))
```

- [ ] **Step 5: Run unit tests and both review gates**

Run: `python -m pytest ads_funnel/tests/test_market_monitor_analytics.py -q`

Expected: all pass. Reviewers check complete-day semantics, zero baselines,
seven-row window, confidence bounds, and suppression of unsupported advice.

---

### Task 6: Build Dashboard and Comparison Services with Stable DTOs

**Files:**
- Create: `ads_funnel/api/market_monitoring/service.py`
- Modify: `ads_funnel/api/market_monitoring/repository.py`
- Modify: `ads_funnel/api/market_monitoring/router.py`
- Test: `ads_funnel/tests/test_market_monitor_service.py`
- Modify: `ads_funnel/tests/test_market_monitor_api.py`

**Interfaces:**
- `GET /api/market-monitor/dashboard`.
- `GET /api/market-monitor/comparison` accepts repeated `competitor_asin`, max 3.
- `GET /api/market-monitor/keyword-summary` returns counts and three-tier aggregates.

- [ ] **Step 1: Write service and API contract tests**

```python
def test_dashboard_payload_has_stable_sections(monkeypatch):
    payload = service.get_dashboard(DEFAULT_MARKET_ID, date_to="2026-07-20")
    assert set(payload) == {"context", "data_quality", "kpis", "trend", "insights", "opportunities"}
    assert payload["context"]["baselines"] == ["previous_complete_day", "latest_7_complete_days"]


def test_comparison_rejects_more_than_three_competitors(client):
    response = client.get("/api/market-monitor/comparison", params=[
        ("market_id", DEFAULT_MARKET_ID), ("own_asin", "B0D6G27DNH"),
        ("competitor_asin", "A1"), ("competitor_asin", "A2"),
        ("competitor_asin", "A3"), ("competitor_asin", "A4"),
    ])
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests and verify missing-service failure**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_service.py ads_funnel/tests/test_market_monitor_api.py -q
```

- [ ] **Step 3: Add repository batch queries**

Add one query per payload section, not one query per ASIN. Required repository
signatures:

```python
def get_asin_trends(market_id: str, child_asins: list[str], date_from=None, date_to=None) -> list[dict]:
    if not child_asins:
        return []
    clauses, params = _date_clauses(market_id, date_from, date_to)
    params["child_asins"] = child_asins
    return _query_rows(text(f"""
        SELECT * FROM asin_snapshot_daily
        WHERE {' AND '.join(clauses)} AND child_asin IN :child_asins
        ORDER BY date, child_asin
    """).bindparams(bindparam("child_asins", expanding=True)), params)


def get_latest_run_quality(market_id: str, date: str | None = None) -> dict:
    params = {"market_id": market_id}
    date_clause = ""
    if date:
        date_clause = "AND target_data_date <= :date"
        params["date"] = date
    rows = _query_rows(f"""
        SELECT run_id, status, collect_time_bj, target_data_date,
               data_available_through, source_summary_json, error_message
        FROM market_run_log
        WHERE market_id=:market_id {date_clause}
        ORDER BY target_data_date DESC, created_at DESC LIMIT 1
    """, params)
    return rows[0] if rows else {}


def get_keyword_level_summary(market_id: str) -> list[dict]:
    return _query_rows("""
        SELECT relevance_level, COUNT(*) AS keyword_count,
               SUM(COALESCE(search_volume, 0)) AS search_volume,
               SUM(COALESCE(category_search_volume, 0)) AS category_search_volume,
               AVG(relevance_weight) AS average_relevance_weight
        FROM keyword_pool_config
        WHERE market_id=:market_id AND status='active'
        GROUP BY relevance_level
        ORDER BY FIELD(relevance_level, 'high', 'mid', 'low')
    """, {"market_id": market_id})


def get_gap_contributors(market_id: str, own_asin: str, competitor_asins: list[str], date: str) -> list[dict]:
    if not competitor_asins:
        return []
    params = {"market_id": market_id, "date": date, "own_asin": own_asin,
              "competitor_asins": competitor_asins}
    return _query_rows(text("""
        SELECT own.keyword, own.relevance_level,
               own.keyword_traffic AS own_traffic,
               competitor.child_asin AS competitor_asin,
               competitor.keyword_traffic AS competitor_traffic,
               competitor.keyword_traffic - own.keyword_traffic AS traffic_gap,
               own.organic_rank AS own_organic_rank,
               competitor.organic_rank AS competitor_organic_rank,
               own.ad_rank AS own_ad_rank,
               competitor.ad_rank AS competitor_ad_rank
        FROM asin_keyword_snapshot_daily own
        JOIN asin_keyword_snapshot_daily competitor
          ON competitor.market_id=own.market_id AND competitor.date=own.date
         AND competitor.keyword=own.keyword
        WHERE own.market_id=:market_id AND own.date=:date
          AND own.child_asin=:own_asin
          AND competitor.child_asin IN :competitor_asins
        ORDER BY traffic_gap DESC
    """).bindparams(bindparam("competitor_asins", expanding=True)), params)


def _query_rows(statement: str | TextClause, params: dict) -> list[dict]:
    schema.init_market_db(seed_defaults=True)
    sql = text(statement) if isinstance(statement, str) else statement
    with get_market_engine().connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [_json_ready(dict(row)) for row in rows]
```

Use SQLAlchemy expanding binds for ASIN lists. Never interpolate ASINs into SQL.

- [ ] **Step 4: Assemble stable service DTOs**

```python
SCENARIO_METRICS = {
    "market_share": ("total_traffic", "market_share", "rank_in_market"),
    "keyword_competition": ("keyword_traffic", "organic_rank", "keyword_traffic_share"),
    "ad_competition": ("ad_traffic", "ad_rank", "ad_traffic_share"),
    "growth_quality": ("weighted_traffic", "organic_traffic", "ad_traffic"),
}


def validate_comparison(own_asin: str, competitors: list[str]) -> list[str]:
    unique = [asin for asin in dict.fromkeys(competitors) if asin and asin != own_asin]
    if len(unique) > 3:
        raise ValueError("at most three competitor ASINs are allowed")
    return unique
```

`get_dashboard` returns ISO dates, explicit `null` for unavailable comparisons,
`baseline_sample_size`, and evidence IDs. It must not return HTML.

- [ ] **Step 5: Add router endpoints**

```python
@router.get("/dashboard")
def dashboard(market_id: str = db.DEFAULT_MARKET_ID, date_from: str | None = None, date_to: str | None = None):
    return service.get_dashboard(market_id, date_from, date_to)


@router.get("/comparison")
def comparison(market_id: str, own_asin: str, competitor_asin: list[str] = Query(default=[]),
               scenario: str = Query(default="market_share", pattern="^(market_share|keyword_competition|ad_competition|growth_quality)$"),
               metric: str | None = None, date_from: str | None = None, date_to: str | None = None):
    if len(competitor_asin) > 3:
        raise HTTPException(status_code=422, detail="at most three competitor ASINs are allowed")
    return service.get_comparison(market_id, own_asin, competitor_asin, scenario, metric, date_from, date_to)
```

- [ ] **Step 6: Run service/API tests and both review gates**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_service.py ads_funnel/tests/test_market_monitor_api.py -q
```

Reviewers check query count, DTO stability, max-three enforcement, evidence
traceability, null behavior, and separation of repository/service/router.

---

### Task 7: Extract Market-Monitor Frontend Assets Without Breaking Exports

**Files:**
- Create: `ads_funnel/frontend/static/market-monitor.css`
- Create: `ads_funnel/frontend/static/market-monitor.js`
- Modify: `ads_funnel/template.html`
- Modify: `ads_funnel/api/export.py`
- Create: `ads_funnel/tests/test_market_monitor_assets.py`

**Interfaces:**
- `template.html` contains `/* __MARKET_MONITOR_CSS__ */` and `/* __MARKET_MONITOR_JS__ */` markers.
- `export._build_html()` inlines both assets.
- Browser receives no extra runtime dependency and exported HTML stays standalone.

- [ ] **Step 1: Write failing asset-inlining tests**

```python
def test_export_inlines_market_monitor_assets():
    html = export._build_html(MINIMAL_REPORT, "test", {}, None)
    assert "__MARKET_MONITOR_CSS__" not in html
    assert "__MARKET_MONITOR_JS__" not in html
    assert "window.MarketMonitor" in html
    assert ".mi-command-center" in html
    assert '<script src="/static/market-monitor.js"' not in html
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m pytest ads_funnel/tests/test_market_monitor_assets.py -q`

- [ ] **Step 3: Extract current Market Insights CSS and JavaScript**

Move every `.mi-*` rule into `market-monitor.css`. Move the `MI` state and every
`mi*` function into `market-monitor.js`. Expose only:

```javascript
window.MarketMonitor = Object.freeze({
  load,
  selectAsin,
  selectKeyword,
  setScenario,
  setMetric,
  setRange,
  toggleCompetitor,
});
```

The extracted module owns no other report tabs and does not redefine global
helpers used by Overview/L1/L2/L3.

- [ ] **Step 4: Add inline markers and export substitution**

```html
<style>
/* __MARKET_MONITOR_CSS__ */
</style>
```

```html
<script>
/* __MARKET_MONITOR_JS__ */
</script>
```

```python
MARKET_MONITOR_CSS_PATH = Path(__file__).parent.parent / "frontend" / "static" / "market-monitor.css"
MARKET_MONITOR_JS_PATH = Path(__file__).parent.parent / "frontend" / "static" / "market-monitor.js"


def _inline_market_monitor_assets(template: str) -> str:
    return (template
            .replace("/* __MARKET_MONITOR_CSS__ */", MARKET_MONITOR_CSS_PATH.read_text(encoding="utf-8"))
            .replace("/* __MARKET_MONITOR_JS__ */", MARKET_MONITOR_JS_PATH.read_text(encoding="utf-8")))
```

Call `_inline_market_monitor_assets(tmpl)` before RAW/report placeholder
replacement.

- [ ] **Step 5: Run asset and existing export tests**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_assets.py ads_funnel/tests/test_export.py -q
```

If `test_export.py` is not present, run all discovered tests matching export:

```powershell
python -m pytest ads_funnel/tests -q -k export
```

Expected: markers are absent from output; generated HTML contains CSS and JS.

- [ ] **Step 6: Run both review gates**

Spec reviewer checks standalone export and unchanged other tabs. Code-quality
reviewer checks global leakage, asset encoding, cache/read behavior, and missing
file failure clarity.

---

### Task 8: Build the Command Center, Evidence Drawer, and Three-Tier Keyword UI

**Files:**
- Modify: `ads_funnel/api/market_monitoring/schema.py`
- Modify: `ads_funnel/api/market_monitoring/collector.py`
- Modify: `ads_funnel/api/market_monitoring/repository.py`
- Modify: `ads_funnel/api/market_monitoring/router.py`
- Modify: `ads_funnel/template.html`
- Modify: `ads_funnel/frontend/static/market-monitor.css`
- Modify: `ads_funnel/frontend/static/market-monitor.js`
- Create: `ads_funnel/tests/test_market_monitor_frontend.py`

**Interfaces:**
- Consumes Task 6 `/dashboard`, `/comparison`, and `/keyword-summary` DTOs.
- Uses confirmed palette: blue interaction, teal high, amber mid, blue-gray low,
  red risk, green opportunity.
- The monitoring-object control groups children by parent ASIN and shows only
  representative/main children, prioritizing own/representative flags and then
  latest complete-day traffic. Each option shows a persisted `image_url`
  thumbnail, child ASIN, parent ASIN, and traffic context; missing images use a
  deterministic local placeholder rather than a guessed third-party CDN URL.
- `asin_snapshot_daily.image_url` is nullable and idempotently migrated. The
  collector persists a source-provided image URL and repository DTOs expose it.
- Keyword data is exclusively the confirmed June 2026 Excel catalog. The UI
  presents exactly three relevance levels (`high`, `mid`, `low`), defaults to
  `high`, and never requests or renders legacy `strong` data.

- [ ] **Step 1: Add structural frontend tests**

```python
def test_market_monitor_template_has_command_center_regions():
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    for element_id in (
        "mi-context", "mi-data-quality", "mi-kpis", "mi-trend",
        "mi-insights", "mi-anomalies", "mi-opportunities",
        "mi-evidence-drawer", "mi-comparison", "mi-keyword-dashboard",
    ):
        assert f'id="{element_id}"' in html


def test_market_monitor_css_defines_accessible_semantic_tokens():
    css = CSS_PATH.read_text(encoding="utf-8")
    for token in ("--mi-primary", "--mi-own", "--mi-high", "--mi-mid", "--mi-low", "--mi-risk", "--mi-opportunity"):
        assert token in css
```

- [ ] **Step 2: Run structural tests and verify failure**

Run: `python -m pytest ads_funnel/tests/test_market_monitor_frontend.py -q`

- [ ] **Step 3: Replace current Market Insights markup with semantic regions**

The tab contains one sticky context bar followed by command-center regions, a
hidden evidence `<aside>`, comparison section, and keyword dashboard. Use buttons
for scene/metric choices and real labels for form controls. Do not add nested
card grids or modal dialogs.

- [ ] **Step 4: Implement the confirmed color system**

```css
.mi-root{
  --mi-primary:#356ae6;
  --mi-own:#1f4fae;
  --mi-competitor-1:#7656c8;
  --mi-competitor-2:#c76b1b;
  --mi-competitor-3:#168aa3;
  --mi-high:#12634d;
  --mi-high-bg:#dff4ee;
  --mi-mid:#815711;
  --mi-mid-bg:#fff0cf;
  --mi-low:#526174;
  --mi-low-bg:#e9edf3;
  --mi-risk:#b43f35;
  --mi-opportunity:#167052;
  --mi-ink:#172033;
  --mi-muted:#64748b;
  --mi-surface:#ffffff;
  --mi-canvas:#f4f7fb;
}
```

No colored side stripes, gradient text, glass effects, or red low-relevance
badges. Focus rings use `--mi-primary` and remain visible in dark mode.

- [ ] **Step 5: Implement state, linked filters, and render functions**

```javascript
const state = {
  marketId: "slip_lead_leash",
  dateFrom: null,
  dateTo: null,
  rangePreset: "30d",
  ownAsin: "B0D6G27DNH",
  competitors: [],
  scenario: "market_share",
  metric: "total_traffic",
  relevance: "high",
  dashboard: null,
  comparison: null,
};

const SCENARIO_METRICS = {
  market_share: ["total_traffic", "market_share", "rank_in_market"],
  keyword_competition: ["keyword_traffic", "organic_rank", "keyword_traffic_share"],
  ad_competition: ["ad_traffic", "ad_rank", "ad_traffic_share"],
  growth_quality: ["weighted_traffic", "organic_traffic", "ad_traffic"],
};
```

Implement bounded functions `loadDashboard`, `loadComparison`, `renderContext`,
`renderDataQuality`, `renderKpis`, `renderTrend`, `renderInsights`,
`renderAnomalies`, `renderOpportunities`, `renderEvidenceDrawer`,
`renderComparison`, and `renderKeywordDashboard`. Each render function owns one
DOM region and handles its own loading, empty, partial, and error state.

- [ ] **Step 6: Implement chart and table interaction**

- Own series always uses `--mi-own`; competitor index maps to fixed competitor
  tokens and does not change after sorting.
- Chart interaction uses `mode: 'index', intersect: false`.
- Previous-day and seven-day-average values appear in tooltip content.
- Clicking an anomaly marker calls `openEvidence(insightId)`.
- Keyword relevance filters are `high`, `mid`, and `low` with counts, traffic
  share, and trend. There is no `all` or `strong` relevance tab.
- Tables keep headers visible, support horizontal overflow on narrow screens, and
  do not truncate numeric values.

- [ ] **Step 7: Add responsive and reduced-motion behavior**

```css
@media (max-width:980px){
  .mi-command-grid,.mi-analysis-grid{grid-template-columns:1fr}
  .mi-kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:620px){
  .mi-kpi-grid{grid-template-columns:1fr}
  .mi-context-controls{align-items:stretch;flex-direction:column}
}
@media (prefers-reduced-motion:reduce){
  .mi-root *{scroll-behavior:auto!important;transition-duration:.01ms!important;animation-duration:.01ms!important}
}
```

- [ ] **Step 8: Run structural tests and both review gates**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_frontend.py ads_funnel/tests/test_market_monitor_assets.py -q
```

Spec reviewer checks every confirmed region and interaction. Code-quality
reviewer checks DOM escaping, fetch cancellation/race behavior, chart destruction,
keyboard access, contrast, responsive layout, and global namespace isolation.

---

### Task 9: End-to-End Runtime Verification and Documentation

**Files:**
- Modify: `ads_funnel/docs/market_monitor_daily_collection.md`
- Modify: `AGENTS.md` only if verified architecture facts have changed
- Test: all Market Monitor tests and live FastAPI/browser path

**Interfaces:**
- Verifies real MySQL, real source workbook import, live API JSON, live rendered UI.

- [ ] **Step 1: Run the complete Market Monitor test suite**

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_package.py ads_funnel/tests/test_market_monitor_keyword_catalog.py ads_funnel/tests/test_market_monitor_keyword_import.py ads_funnel/tests/test_market_monitor_db.py ads_funnel/tests/test_market_monitor_collector.py ads_funnel/tests/test_market_monitor_daily_script.py ads_funnel/tests/test_market_monitor_analytics.py ads_funnel/tests/test_market_monitor_service.py ads_funnel/tests/test_market_monitor_api.py ads_funnel/tests/test_market_monitor_assets.py ads_funnel/tests/test_market_monitor_frontend.py -q
```

Expected: all tests pass with zero warnings caused by Market Monitor code.

- [ ] **Step 2: Run the full repository suite**

Run:

```powershell
python -m pytest ads_funnel/tests -q
```

Expected: all tests pass. Any pre-existing unrelated failure is recorded with its
exact command and output; Market Monitor changes must not add a new failure.

- [ ] **Step 3: Apply and verify the keyword catalog against MySQL**

Run:

```powershell
python ads_funnel/scripts/market_monitor_import_keywords.py
python -c "from ads_funnel.api.market_monitoring import repository as r; rows=r.list_keywords(); from collections import Counter; print(len(rows), Counter(x['relevance_level'] for x in rows))"
```

Expected: `256 Counter({'low': 139, 'high': 77, 'mid': 40})`.

- [ ] **Step 4: Start FastAPI and exercise live endpoints**

Run from repository root:

```powershell
python -m uvicorn ads_funnel.api.main:app --host 127.0.0.1 --port 5001
```

Verify these real requests return HTTP 200 and valid JSON:

```text
/api/market-monitor/keywords?relevance_level=high
/api/market-monitor/keyword-summary
/api/market-monitor/dashboard?market_id=slip_lead_leash
/api/market-monitor/comparison?market_id=slip_lead_leash&own_asin=B0D6G27DNH&competitor_asin=B08Y5RYPTS&scenario=market_share
```

Verify `relevance_level=strong` returns HTTP 422.

- [ ] **Step 5: Verify the live browser path**

Open `http://127.0.0.1:5001/`, select Market Insights, and verify:

- data freshness and completeness are visible;
- previous-day and seven-day-average values render;
- own ASIN plus three competitors can be selected but a fourth cannot;
- scene changes reset invalid metrics;
- anomaly opens the evidence drawer;
- high/mid/low counts are 77/40/139;
- stale, empty, partial, and API-error fixtures render locally;
- 1440px, 980px, 620px, and 390px widths have no clipped controls;
- keyboard focus is visible and all meaning survives grayscale inspection;
- browser console has no uncaught errors.

- [ ] **Step 6: Verify standalone export**

Generate an exported HTML report through the existing export endpoint, open the
saved file without the FastAPI server, and confirm Market Monitor CSS/JS are
embedded and no `/static/market-monitor.*` network request occurs.

- [ ] **Step 7: Update collection documentation**

Document:

- source workbook and version;
- dry-run/apply commands;
- exact 77/40/139 counts;
- inactive legacy behavior;
- three-tier collector behavior;
- package map;
- dashboard endpoints;
- troubleshooting for invalid workbook, stale data, and partial providers.

- [ ] **Step 8: Run final two-stage review and verification-before-completion**

Final spec reviewer checks the implementation against every section of
`2026-07-20-market-monitor-intelligent-dashboard-design.md`. Final code-quality
reviewer checks package boundaries, compatibility facades, migration safety,
query counts, frontend isolation, and test quality. Apply accepted findings, rerun
Steps 1-6, and only then claim completion.
