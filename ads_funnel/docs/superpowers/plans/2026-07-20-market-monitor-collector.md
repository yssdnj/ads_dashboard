# Market Monitor Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement collector persistence helpers for writing MCP/API collection runs into `ads_funnel_market`.

**Architecture:** Add `ads_funnel/api/market_monitor_collector.py` as a write layer on top of `market_monitor_db`. Keep MCP execution outside FastAPI and outside the collector module.

**Tech Stack:** Python, SQLAlchemy, MySQL, pytest.

## Global Constraints

- Work on branch `dev`.
- Do not call MCP from FastAPI.
- Do not call SP-API.
- Do not schedule automation yet.
- Do not commit or push unless explicitly requested.

---

### Task 1: Collector Write Helpers

**Files:**
- Create: `ads_funnel/api/market_monitor_collector.py`
- Test: `ads_funnel/tests/test_market_monitor_collector.py`

**Interfaces:**
- `create_run(market_id, run_date, collect_time_bj, timezone_basis='America/New_York') -> str`
- `finish_run(run_id, status, target_data_date=None, data_available_through=None, source_summary=None, error_message=None) -> None`
- `record_raw_request(run_id, market_id, source_channel, source_provider, source_tool, request_params, purpose=None, request_id=None) -> str`
- `record_raw_response(request_id, run_id, market_id, source_channel, source_provider, source_tool, response_status, response_json=None, cost_credits=None, error_message=None, response_id=None) -> str`
- `upsert_listing_variations(rows) -> None`
- `replace_market_snapshot(row) -> None`
- `replace_asin_snapshots(market_id, date, rows) -> None`
- `upsert_asin_snapshots(rows) -> None`
- `replace_keyword_snapshots(market_id, date, rows) -> None`
- `upsert_keyword_snapshots(rows) -> None`
- `replace_asin_keyword_snapshots(market_id, date, rows) -> None`
- `upsert_asin_keyword_snapshots(rows) -> None`
- `replace_keyword_asin_competition(market_id, date, rows) -> None`
- `upsert_keyword_asin_competition(rows) -> None`
- `persist_daily_source_batch(...) -> dict`
- `save_daily_report(row) -> int`

`persist_daily_source_batch` is the source-agnostic write entrypoint. MCP,
future API, manual, and CSV collectors should pass:

- `source_channel`: `mcp` / `api` / `manual` / `csv`
- `source_provider`: `xydc` / `sif` / `sorftime` / future API provider
- `source_tool`: provider tool or endpoint name
- `request_params`: exact request parameters
- `response_json`: raw provider response or response summary
- `normalized`: cleaned rows grouped by section

Supported `normalized` sections:

```text
listing_variations
market_snapshot
asin_snapshots
keyword_snapshots
asin_keyword_snapshots
keyword_asin_competition
```

Supported write modes:

- `upsert`: default; safe for API/MCP partial writes by ASIN or keyword.
- `replace_day`: use only when the caller has a complete daily dataset for a table.

### Task 2: Verification

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_collector.py ads_funnel/tests/test_market_monitor_db.py ads_funnel/tests/test_market_monitor_api.py -q
python -m pytest ads_funnel/tests -q
```

Expected:

- Collector writes raw logs and snapshots.
- Re-running same daily writes replaces facts without duplicates.
- Existing read APIs can read collector-written rows.
