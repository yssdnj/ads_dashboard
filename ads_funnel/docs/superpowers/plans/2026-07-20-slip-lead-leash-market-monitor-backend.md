# Slip Lead Leash Market Monitor Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first backend slice of the slip lead leash market monitor to `ads_funnel`.

**Architecture:** Use a separate MySQL database `ads_funnel_market`. Add `api/market_monitor_db.py` for schema, seeding, and queries. Add `api/market_monitor.py` as a FastAPI router and include it from `api/main.py`. Do not call MCP from FastAPI.

**Tech Stack:** FastAPI, SQLAlchemy, PyMySQL, MySQL, pytest.

## Global Constraints

- Develop on branch `dev`.
- Store docs under `ads_funnel/docs/superpowers`.
- New runtime database is `ads_funnel_market`.
- FastAPI only reads stored data; MCP collection is external.
- No SP-API calls.
- No git commit or push unless explicitly requested.

---

### Task 1: Market Database Module

**Files:**
- Create: `ads_funnel/api/market_monitor_db.py`
- Test: `ads_funnel/tests/test_market_monitor_db.py`

**Interfaces:**
- `get_market_engine()`
- `init_market_db()`
- `seed_default_market_config()`
- `list_markets()`
- `get_available_dates(market_id)`
- `get_market_trend(market_id, date_from=None, date_to=None)`
- `list_asins(market_id)`
- `get_asin_trend(market_id, child_asin, date_from=None, date_to=None)`
- `list_keywords(market_id, relevance_level=None)`
- `get_keyword_trend(market_id, keyword, date_from=None, date_to=None)`
- `get_asin_keyword_composition(market_id, child_asin, date=None)`
- `get_keyword_asin_competition(market_id, keyword, date=None, limit=50)`
- `get_comparison(market_id, left_asin, right_asin=None, date_from=None, date_to=None)`
- `get_daily_reports(market_id, report_date=None)`
- `list_run_logs(market_id, limit=30)`

### Task 2: FastAPI Router

**Files:**
- Create: `ads_funnel/api/market_monitor.py`
- Modify: `ads_funnel/api/main.py`
- Test: `ads_funnel/tests/test_market_monitor_api.py`

**Routes:**
- `GET /api/market-monitor/health`
- `GET /api/market-monitor/markets`
- `GET /api/market-monitor/dates`
- `GET /api/market-monitor/market-trend`
- `GET /api/market-monitor/asins`
- `GET /api/market-monitor/asin-trend`
- `GET /api/market-monitor/keywords`
- `GET /api/market-monitor/keyword-trend`
- `GET /api/market-monitor/asin-keywords`
- `GET /api/market-monitor/keyword-competition`
- `GET /api/market-monitor/comparison`
- `GET /api/market-monitor/reports`
- `GET /api/market-monitor/runs`

### Task 3: Verification

Run:

```powershell
python -m pytest ads_funnel/tests/test_market_monitor_db.py ads_funnel/tests/test_market_monitor_api.py -q
python -m pytest ads_funnel/tests -q
```

Expected:

- New market database initializes.
- Default `slip_lead_leash` config exists.
- Seed ASINs include `B0D6G27DNH` with `is_own_product = 1`.
- Keyword pool contains all four relevance levels.
- FastAPI routes return JSON without calling MCP.
