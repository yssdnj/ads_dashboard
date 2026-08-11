# Slip Lead Leash Market Monitor Design

Date: 2026-07-20

## Goal

Build a daily market monitoring system for the Amazon US `slip lead leash` niche inside `ads_funnel`. The system records the ASINs and keywords that represent the niche, collects daily market/ASIN/keyword/ASIN-keyword data through MCP or future APIs, stores raw and cleaned data in MySQL, and exposes the stored data through FastAPI/frontend pages.

## Architecture

Use a separate MySQL database:

```text
ads_funnel_market
```

Data flow:

```text
Daily MCP/API collector
  -> ads_funnel_market raw logs
  -> ads_funnel_market normalized daily snapshots
  -> ads_funnel_market result tables

ads_funnel FastAPI
  -> reads ads_funnel_market
  -> returns JSON APIs

ads_funnel frontend
  -> renders market trend, ASIN trend, keyword relevance, and comparison pages
```

FastAPI request handlers must not directly call MCP. MCP collection is a separate collector/Codex task that writes MySQL. The backend only queries stored data.

## Collection Time

Default collection time is `17:00 Asia/Shanghai`.

Each run records `run_date`, `collect_time_bj`, `target_data_date`, `data_available_through`, and `timezone_basis`. The collector chooses the latest complete source data date rather than assuming yesterday is complete.

## Market Definition

Market id:

```text
slip_lead_leash
```

Aliases:

- `slip lead leash`
- `slip leash`
- `slip lead`

Country: `US`

Own product: `B0D6G27DNH`

Seed ASINs come from the user-confirmed `2026-07-16` Amazon Top100 category screening. Seed child ASINs remain visible, sibling variants must be included, and parent Listing is used for market-share deduplication.

## Seed ASINs

Category: `Pet Supplies > Dogs > Collars, Harnesses & Leashes > Leashes > Basic Leashes`

```text
B091TMYKHF
B09T3KS313
B000LQ62Y6
B0D6G27DNH
B0BXTYKPZH
B001B183A6
B0FDBLNLPN
```

Category: `Pet Supplies > Dogs > Training & Behavior Aids > Training Leashes`

```text
B08Y5RYPTS
B07D7P78SM
B095775SWX
B0DF6LHQZ6
B0BMQQ74H2
B08LZNXK49
B0DC15HXYC
B08PBG341W
B0C3CDQGJQ
B0D3Y5W2DL
B0FQMKVD84
B09VRX73RZ
B0F5GT84DG
B01G2Z4D72
B0CQC5XVDH
B08NZYQ3L5
B09231799H
```

## Keyword Relevance

Each relevance tier should eventually keep Top20 monitored keywords.

Initial tiers:

- `strong`: explicit slip lead / slip leash intent.
- `high`: highly overlapping training leash, rope lead, and no-pull leash intent.
- `mid`: broad leash terms that can contain slip lead demand but are not specific.
- `low`: adjacent, substitute, brand, or low-fit pet gear terms such as harness, collar, hands-free leash, and training tools.

Initial weights:

```text
strong = 1.00
high   = 0.75
mid    = 0.35
low    = 0.10
```

## Source Fields

Use source fields consistently:

```text
source_channel   -- mcp / api / manual / csv
source_provider  -- xydc / sif / sorftime / amazon_ads / sp_api / user
source_tool      -- get_asin_keywords / get_keyword_info / manual_seed / csv_import, etc.
source_request_id
```

Rules:

- Raw log tables require `source_channel`, `source_provider`, and `source_tool`.
- Daily snapshot tables keep all source fields.
- Mapping tables keep all source fields.
- Config tables keep `source_channel` and `source_provider`; `source_tool` is optional.
- Result tables trace source through `run_id`.

## First Implementation Tables

Create these 13 tables:

```text
market_config
seed_asin_config
keyword_pool_config
listing_variation_map
market_run_log
raw_request_log
raw_response_log
market_snapshot_daily
asin_snapshot_daily
keyword_snapshot_daily
asin_keyword_snapshot_daily
keyword_asin_competition_daily
daily_report_result
```

`keyword_asin_competition_daily` stores the ASIN matrix returned by keyword
reverse-ASIN analysis: keyword, relevance tier, parent/child ASIN, brand, title,
keyword traffic, keyword traffic share, organic rank, ad rank, seed/own flags,
and source trace fields. This powers keyword-level competitor comparison pages.

Later tables:

```text
asin_category_map
keyword_relevance_map
daily_insight_result
daily_alert_result
```

## Backend Scope

Add backend module(s) under `ads_funnel/api` that:

- Create and initialize `ads_funnel_market`.
- Seed default market config, seed ASINs, and initial keyword pool.
- Return market list and config.
- Return available dates.
- Return market daily trends.
- Return ASIN list and ASIN daily trends.
- Return keyword list and keyword daily trends.
- Return ASIN-keyword composition.
- Return keyword-level ASIN competition rows.
- Return ASIN vs ASIN and ASIN vs market comparison payloads.
- Return daily reports and run logs.

## Frontend Scope

First generic pages:

- Market overview.
- ASIN comparison.
- Keyword relevance dashboard.
- ASIN keyword composition.
- Daily report viewer.

Do not build a dedicated `B0D6G27DNH` page in the first version. Store `is_own_product = 1` so it can become a default filter or preset view.
