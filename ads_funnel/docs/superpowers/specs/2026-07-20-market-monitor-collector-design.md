# Market Monitor Collector Design

Date: 2026-07-20

## Goal

Add a collector persistence layer for `ads_funnel_market` so Codex-driven MCP collection can record every run, raw request, raw response, normalized daily snapshots, and generated daily reports.

The collector does not make FastAPI request handlers call MCP. MCP calls happen in a Codex/operator workflow after the required disclosure and confirmation. The Python collector module provides durable write APIs used after MCP responses are available.

## Runtime Boundary

Allowed:

- Create collector run records.
- Record MCP/API/manual/CSV request parameters.
- Record raw MCP/API responses.
- Upsert parent-child variation maps.
- Upsert market, ASIN, keyword, and ASIN-keyword daily snapshots.
- Save daily report results.

Not allowed:

- Calling MCP from FastAPI endpoints.
- Calling SP-API.
- Scheduling recurring jobs in this implementation.
- Building frontend pages.

## First Implementation

Add:

```text
ads_funnel/api/market_monitor_collector.py
```

The module should expose:

```text
create_run(...)
finish_run(...)
record_raw_request(...)
record_raw_response(...)
upsert_listing_variations(...)
upsert_market_snapshot(...)
upsert_asin_snapshots(...)
upsert_keyword_snapshots(...)
upsert_asin_keyword_snapshots(...)
save_daily_report(...)
```

Each write must be idempotent for the same primary key or logical unique key. Re-running a collector for the same `target_data_date` should replace daily snapshot rows for that date/market instead of appending duplicate facts.

## Source Traceability

Every raw request and response stores:

```text
source_channel
source_provider
source_tool
```

Every normalized snapshot row stores:

```text
source_channel
source_provider
source_tool
source_request_id
```

## Collection Workflow

Manual/Codex workflow:

```text
1. create_run(market_id, run_date, collect_time_bj)
2. For each planned MCP/API call:
   - record_raw_request(...)
   - call MCP outside FastAPI
   - record_raw_response(...)
3. Normalize responses in Codex/operator workflow.
4. Write listing map and daily snapshot rows.
5. Save daily_report_result.
6. finish_run(status='success', target_data_date=...)
```

If source data is incomplete, the run should finish as `partial` and still retain raw logs.

## Validation

- Create a test run.
- Record one raw request and response.
- Upsert one listing variation.
- Upsert one row into each daily snapshot table.
- Save one daily report.
- Re-run the same writes and verify no duplicate snapshot rows are created.
- Verify FastAPI read APIs can read the written rows.
