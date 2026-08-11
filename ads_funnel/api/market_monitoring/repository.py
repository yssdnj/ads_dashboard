"""MySQL storage and read APIs for the market monitor.

The monitor uses a separate database (`ads_funnel_market`) so market data can
grow independently from the advertising funnel raw/report tables.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import bindparam, text
from sqlalchemy.sql.elements import TextClause

from . import schema
from .constants import DEFAULT_MARKET_ID, MARKET_DB_NAME
from .keyword_catalog import KeywordCatalogRow, validate_keyword_catalog

get_market_engine = schema.get_market_engine
ensure_market_database = schema.ensure_market_database
init_market_db = schema.init_market_db


SOURCE_CATEGORY_BASIC = (
    "Pet Supplies > Dogs > Collars, Harnesses & Leashes > Leashes > Basic Leashes"
)
SOURCE_CATEGORY_TRAINING = (
    "Pet Supplies > Dogs > Training & Behavior Aids > Training Leashes"
)

SEED_ASINS = [
    ("B091TMYKHF", SOURCE_CATEGORY_BASIC, False),
    ("B09T3KS313", SOURCE_CATEGORY_BASIC, False),
    ("B000LQ62Y6", SOURCE_CATEGORY_BASIC, False),
    ("B0D6G27DNH", SOURCE_CATEGORY_BASIC, True),
    ("B0BXTYKPZH", SOURCE_CATEGORY_BASIC, False),
    ("B001B183A6", SOURCE_CATEGORY_BASIC, False),
    ("B0FDBLNLPN", SOURCE_CATEGORY_BASIC, False),
    ("B08Y5RYPTS", SOURCE_CATEGORY_TRAINING, False),
    ("B07D7P78SM", SOURCE_CATEGORY_TRAINING, False),
    ("B095775SWX", SOURCE_CATEGORY_TRAINING, False),
    ("B0DF6LHQZ6", SOURCE_CATEGORY_TRAINING, False),
    ("B0BMQQ74H2", SOURCE_CATEGORY_TRAINING, False),
    ("B08LZNXK49", SOURCE_CATEGORY_TRAINING, False),
    ("B0DC15HXYC", SOURCE_CATEGORY_TRAINING, False),
    ("B08PBG341W", SOURCE_CATEGORY_TRAINING, False),
    ("B0C3CDQGJQ", SOURCE_CATEGORY_TRAINING, False),
    ("B0D3Y5W2DL", SOURCE_CATEGORY_TRAINING, False),
    ("B0FQMKVD84", SOURCE_CATEGORY_TRAINING, False),
    ("B09VRX73RZ", SOURCE_CATEGORY_TRAINING, False),
    ("B0F5GT84DG", SOURCE_CATEGORY_TRAINING, False),
    ("B01G2Z4D72", SOURCE_CATEGORY_TRAINING, False),
    ("B0CQC5XVDH", SOURCE_CATEGORY_TRAINING, False),
    ("B08NZYQ3L5", SOURCE_CATEGORY_TRAINING, False),
    ("B09231799H", SOURCE_CATEGORY_TRAINING, False),
]

def seed_default_market_config() -> None:
    engine = get_market_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO market_config
                  (market_id, market_name, market_display_name, country, timezone,
                   default_collect_time, description, status, source_channel, source_provider)
                VALUES
                  (:market_id, :market_name, :display_name, 'US', 'America/New_York',
                   '17:00 Asia/Shanghai',
                   'Amazon US slip lead leash / slip leash / slip lead niche monitor',
                   'active', 'manual', 'user')
                ON DUPLICATE KEY UPDATE
                  market_name = VALUES(market_name),
                  market_display_name = VALUES(market_display_name),
                  country = VALUES(country),
                  timezone = VALUES(timezone),
                  default_collect_time = VALUES(default_collect_time),
                  description = VALUES(description),
                  status = VALUES(status),
                  updated_at = NOW()
                """
            ),
            {
                "market_id": DEFAULT_MARKET_ID,
                "market_name": "slip lead leash",
                "display_name": "Slip Lead Leash",
            },
        )
        for asin, category, is_own in SEED_ASINS:
            conn.execute(
                text(
                    """
                    INSERT INTO seed_asin_config
                      (market_id, asin, source_category, source_category_path,
                       is_seed_asin, is_own_product, notes, first_seen_date,
                       status, source_channel, source_provider)
                    VALUES
                      (:market_id, :asin, :category_name, :category_path,
                       1, :is_own, :notes, '2026-07-16',
                       'active', 'manual', 'user')
                    ON DUPLICATE KEY UPDATE
                      source_category = VALUES(source_category),
                      source_category_path = VALUES(source_category_path),
                      is_seed_asin = VALUES(is_seed_asin),
                      is_own_product = VALUES(is_own_product),
                      status = VALUES(status),
                      updated_at = NOW()
                    """
                ),
                {
                    "market_id": DEFAULT_MARKET_ID,
                    "asin": asin,
                    "category_name": category.split(" > ")[-1],
                    "category_path": category,
                    "is_own": 1 if is_own else 0,
                    "notes": "User-confirmed 2026-07-16 category Top100 seed ASIN.",
                },
            )


def sync_keyword_catalog(
    market_id: str,
    rows: Iterable[KeywordCatalogRow],
    source_version: str,
) -> dict[str, int]:
    """Atomically replace one market's active catalog with validated source rows."""
    catalog_rows = list(rows)
    counts = validate_keyword_catalog(catalog_rows)
    active_keywords = [row.keyword for row in catalog_rows]

    with get_market_engine().begin() as conn:
        for row in catalog_rows:
            conn.execute(
                text(
                    """
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
                      translation = VALUES(translation),
                      relevance_level = VALUES(relevance_level),
                      relevance_weight = VALUES(relevance_weight),
                      source = VALUES(source),
                      source_version_month = VALUES(source_version_month),
                      status = 'active',
                      source_channel = VALUES(source_channel),
                      source_provider = VALUES(source_provider),
                      keyword_rank = VALUES(keyword_rank),
                      search_volume = VALUES(search_volume),
                      category_search_volume = VALUES(category_search_volume),
                      cpc = VALUES(cpc),
                      cpc_range = VALUES(cpc_range),
                      click_conversion_rate = VALUES(click_conversion_rate),
                      competitive_difficulty = VALUES(competitive_difficulty),
                      organic_scroll_rate = VALUES(organic_scroll_rate),
                      updated_at = NOW()
                    """
                ),
                {"market_id": market_id, "source_version": source_version, **row.__dict__},
            )

        deactivated = conn.execute(
            text(
                """
                UPDATE keyword_pool_config
                SET status = 'inactive', updated_at = NOW()
                WHERE market_id = :market_id
                  AND status = 'active'
                  AND keyword NOT IN :active_keywords
                """
            ).bindparams(bindparam("active_keywords", expanding=True)),
            {"market_id": market_id, "active_keywords": active_keywords},
        ).rowcount

    return {"active": len(catalog_rows), **counts, "deactivated": int(deactivated or 0)}




def list_markets() -> list[dict[str, Any]]:
    with get_market_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM market_config ORDER BY market_display_name")
        ).mappings().all()
    return [dict(row) for row in rows]


def get_available_dates(market_id: str = DEFAULT_MARKET_ID) -> dict[str, Any]:
    with get_market_engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS day_count
                FROM market_snapshot_daily
                WHERE market_id = :market_id
                """
            ),
            {"market_id": market_id},
        ).mappings().first()
    return _json_ready(dict(row or {}))


def get_market_trend(
    market_id: str = DEFAULT_MARKET_ID,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = _date_clauses(market_id, date_from, date_to)
    return _query_rows(
        f"""
        SELECT date, market_id, total_traffic, weighted_traffic, organic_traffic,
               ad_traffic, high_relevance_traffic, mid_relevance_traffic,
               low_relevance_traffic, top_asin_share, top_keyword_share,
               listing_count, asin_count, keyword_count, source_channel,
               source_provider, source_tool, source_request_id, created_at
        FROM market_snapshot_daily
        WHERE {' AND '.join(clauses)}
        ORDER BY date
        """,
        params,
    )


def list_asins(market_id: str = DEFAULT_MARKET_ID) -> list[dict[str, Any]]:
    """Return seed configuration rows for analysis-service ownership lookup."""
    with get_market_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.*, lm.parent_asin
                FROM seed_asin_config s
                LEFT JOIN listing_variation_map lm
                  ON lm.market_id=s.market_id AND lm.child_asin=s.asin
                WHERE s.market_id=:market_id AND s.status='active'
                ORDER BY s.is_own_product DESC, s.asin
                """
            ),
            {"market_id": market_id},
        ).mappings().all()
    return [_json_ready(dict(row)) for row in rows]


def list_monitoring_asins(market_id: str = DEFAULT_MARKET_ID) -> list[dict[str, Any]]:
    """Return parent-grouped representative children for the monitoring UI."""
    with get_market_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                WITH effective_complete_date AS (
                  SELECT COALESCE(
                    MAX(data_available_through),
                    (SELECT MAX(date) FROM asin_snapshot_daily WHERE market_id=:market_id)
                  ) AS snapshot_date
                  FROM market_run_log
                  WHERE market_id=:market_id
                    AND status IN ('success', 'partial_success')
                    AND data_available_through IS NOT NULL
                ),
                candidate_children AS (
                  SELECT lm.market_id, lm.parent_asin, lm.child_asin AS asin,
                         lm.is_seed_child, lm.is_representative_child,
                         seed.is_own_product, COALESCE(seed.status, 'active') AS status
                  FROM listing_variation_map lm
                  LEFT JOIN seed_asin_config seed
                    ON seed.market_id=lm.market_id AND seed.asin=lm.child_asin
                  WHERE lm.market_id=:market_id
                  UNION ALL
                  SELECT seed.market_id, seed.asin, seed.asin,
                         1, 0, seed.is_own_product, seed.status
                  FROM seed_asin_config seed
                  WHERE seed.market_id=:market_id
                    AND NOT EXISTS (
                      SELECT 1 FROM listing_variation_map lm
                      WHERE lm.market_id=seed.market_id AND lm.child_asin=seed.asin
                    )
                ),
                latest_dates AS (
                  SELECT snapshot.child_asin, MAX(snapshot.date) AS snapshot_date
                  FROM asin_snapshot_daily snapshot
                  CROSS JOIN effective_complete_date complete
                  WHERE snapshot.market_id=:market_id
                    AND (complete.snapshot_date IS NULL OR snapshot.date<=complete.snapshot_date)
                  GROUP BY snapshot.child_asin
                ),
                latest_snapshots AS (
                  SELECT snapshot.*
                  FROM asin_snapshot_daily snapshot
                  JOIN latest_dates latest
                    ON latest.child_asin=snapshot.child_asin
                   AND latest.snapshot_date=snapshot.date
                  WHERE snapshot.market_id=:market_id
                )
                SELECT child.market_id, child.parent_asin, child.asin,
                       child.is_seed_child, child.is_representative_child,
                       COALESCE(child.is_own_product, snap.is_own_product, 0) AS is_own_product,
                       child.status,
                       snap.date AS latest_data_date,
                       snap.total_traffic AS latest_total_traffic,
                       snap.image_url, snap.brand, snap.title, snap.rank_in_market
                FROM candidate_children child
                LEFT JOIN latest_snapshots snap ON snap.child_asin=child.asin
                """
            ),
            {"market_id": market_id},
        ).mappings().all()
    return _select_monitoring_objects(
        [_json_ready(dict(row)) for row in rows if row.get("status") == "active"]
    )


def _select_monitoring_objects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose representative children for each parent without flattening variants."""
    by_asin: dict[str, dict[str, Any]] = {}
    for original in rows:
        row = dict(original)
        asin = str(row.get("asin") or row.get("child_asin") or "").strip().upper()
        if not asin:
            continue
        row["asin"] = asin
        row["child_asin"] = asin
        row["parent_asin"] = str(row.get("parent_asin") or asin).strip().upper()
        existing = by_asin.get(asin)
        if existing is None or _monitoring_priority(row) < _monitoring_priority(existing):
            by_asin[asin] = row

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in by_asin.values():
        grouped.setdefault(row["parent_asin"], []).append(row)

    selected: list[dict[str, Any]] = []
    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: (
            -int(any(row.get("is_own_product") for row in item[1])),
            item[0],
        ),
    )
    for _parent_asin, children in ordered_groups:
        representatives = [
            row
            for row in children
            if row.get("is_own_product") or row.get("is_representative_child")
        ]
        selected.extend(
            sorted(representatives, key=_monitoring_child_priority)
            if representatives
            else sorted(children, key=_monitoring_child_priority)[:1]
        )
    return selected


def _monitoring_child_priority(row: dict[str, Any]) -> tuple[Any, ...]:
    traffic = row.get("latest_total_traffic")
    try:
        numeric_traffic = float(traffic or 0)
    except (TypeError, ValueError):
        numeric_traffic = 0.0
    return (
        -int(bool(row.get("is_own_product"))),
        -int(bool(row.get("is_representative_child"))),
        -numeric_traffic,
        str(row.get("asin") or ""),
    )


def _monitoring_priority(row: dict[str, Any]) -> tuple[Any, ...]:
    traffic = row.get("latest_total_traffic")
    try:
        numeric_traffic = float(traffic or 0)
    except (TypeError, ValueError):
        numeric_traffic = 0.0
    return (
        -int(bool(row.get("is_own_product"))),
        str(row.get("parent_asin") or row.get("asin") or ""),
        -int(bool(row.get("is_representative_child"))),
        -numeric_traffic,
        str(row.get("asin") or ""),
    )


def get_asin_trend(
    market_id: str,
    child_asin: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = _date_clauses(market_id, date_from, date_to)
    clauses.append("child_asin = :child_asin")
    params["child_asin"] = child_asin
    return _query_rows(
        f"""
        SELECT date, market_id, parent_asin, child_asin, brand, title, image_url, price,
               rating, reviews, total_traffic, organic_traffic, ad_traffic,
               weighted_traffic, high_keyword_share, mid_keyword_share,
               low_keyword_share, rank_in_market, is_own_product,
               source_channel, source_provider, source_tool, source_request_id,
               created_at
        FROM asin_snapshot_daily
        WHERE {' AND '.join(clauses)}
        ORDER BY date
        """,
        params,
    )


def get_asin_trends(
    market_id: str,
    child_asins: list[str],
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Return daily rows for all requested ASINs in one expanding-bind query."""
    if not child_asins:
        return []
    clauses, params = _date_clauses(market_id, date_from, date_to)
    params["child_asins"] = child_asins
    return _query_rows(
        text(
            f"""
            SELECT date, market_id, parent_asin, child_asin, brand, title, image_url, price,
                   rating, reviews, total_traffic, organic_traffic, ad_traffic,
                   weighted_traffic, high_keyword_share, mid_keyword_share,
                   low_keyword_share, rank_in_market, is_own_product,
                   source_channel, source_provider, source_tool, source_request_id,
                   created_at
            FROM asin_snapshot_daily
            WHERE {' AND '.join(clauses)} AND child_asin IN :child_asins
            ORDER BY date, child_asin
            """
        ).bindparams(bindparam("child_asins", expanding=True)),
        params,
    )


def get_asin_snapshots(
    market_id: str,
    date: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"market_id": market_id, "limit": int(limit)}
    if date:
        date_filter = "AND date = :date"
        params["date"] = date
    else:
        date_filter = (
            "AND date = (SELECT MAX(date) FROM asin_snapshot_daily "
            "WHERE market_id = :market_id)"
        )
    return _query_rows(
        f"""
        SELECT date, market_id, parent_asin, child_asin, brand, title, image_url, price,
               rating, reviews, total_traffic, organic_traffic, ad_traffic,
               weighted_traffic, high_keyword_share, mid_keyword_share,
               low_keyword_share, rank_in_market, is_own_product,
               source_channel, source_provider, source_tool, source_request_id,
               created_at
        FROM asin_snapshot_daily
        WHERE market_id = :market_id
        {date_filter}
        ORDER BY rank_in_market IS NULL, rank_in_market ASC, total_traffic DESC
        LIMIT :limit
        """,
        params,
    )


def list_keywords(
    market_id: str = DEFAULT_MARKET_ID,
    relevance_level: str | None = None,
) -> list[dict[str, Any]]:
    clauses = [
        "market_id = :market_id",
        "status = 'active'",
        "relevance_level IN ('high', 'mid', 'low')",
    ]
    params: dict[str, Any] = {"market_id": market_id}
    if relevance_level:
        clauses.append("relevance_level = :relevance_level")
        params["relevance_level"] = relevance_level
    return _query_rows(
        f"""
        SELECT *
        FROM keyword_pool_config
        WHERE {' AND '.join(clauses)}
        ORDER BY FIELD(relevance_level, 'high', 'mid', 'low'),
                 search_volume IS NULL, search_volume DESC, keyword
        """,
        params,
    )


def get_keyword_trend(
    market_id: str,
    keyword: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = _date_clauses(market_id, date_from, date_to)
    clauses.append("keyword = :keyword")
    clauses.append("relevance_level IN ('high', 'mid', 'low')")
    params["keyword"] = keyword
    return _query_rows(
        f"""
        SELECT *
        FROM keyword_snapshot_daily
        WHERE {' AND '.join(clauses)}
        ORDER BY date
        """,
        params,
    )


def get_asin_keyword_composition(
    market_id: str,
    child_asin: str,
    date: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"market_id": market_id, "child_asin": child_asin}
    date_filter = ""
    if date:
        date_filter = "AND date = :date"
        params["date"] = date
    else:
        date_filter = (
            "AND date = (SELECT MAX(date) FROM asin_keyword_snapshot_daily "
            "WHERE market_id = :market_id AND child_asin = :child_asin "
            "AND relevance_level IN ('high', 'mid', 'low'))"
        )
    return _query_rows(
        f"""
        SELECT *
        FROM asin_keyword_snapshot_daily
        WHERE market_id = :market_id AND child_asin = :child_asin
          AND relevance_level IN ('high', 'mid', 'low')
        {date_filter}
        ORDER BY keyword_traffic DESC
        """,
        params,
    )


def get_keyword_asin_competition(
    market_id: str,
    keyword: str,
    date: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "market_id": market_id,
        "keyword": keyword,
        "limit": int(limit),
    }
    if date:
        date_filter = "AND date = :date"
        params["date"] = date
    else:
        date_filter = (
            "AND date = (SELECT MAX(date) FROM keyword_asin_competition_daily "
            "WHERE market_id = :market_id AND keyword = :keyword "
            "AND relevance_level IN ('high', 'mid', 'low'))"
        )
    return _query_rows(
        f"""
        SELECT *
        FROM keyword_asin_competition_daily
        WHERE market_id = :market_id AND keyword = :keyword
          AND relevance_level IN ('high', 'mid', 'low')
        {date_filter}
        ORDER BY keyword_traffic DESC, competition_rank ASC
        LIMIT :limit
        """,
        params,
    )


def get_latest_run_quality(
    market_id: str,
    date: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"market_id": market_id}
    date_clause = ""
    if date:
        date_clause = "AND latest.target_data_date <= :date"
        params["date"] = date
    rows = _query_rows(
        f"""
        SELECT latest.run_id, latest.status, latest.collect_time_bj,
               latest.target_data_date, latest.data_available_through,
               latest.source_summary_json, latest.error_message,
               (
                   SELECT MAX(complete.data_available_through)
                   FROM market_run_log complete
                   WHERE complete.market_id = latest.market_id
                     AND complete.status IN ('success', 'partial_success')
                     AND complete.data_available_through IS NOT NULL
                     {f"AND complete.target_data_date <= :date" if date else ""}
               ) AS effective_data_available_through
        FROM market_run_log latest
        WHERE latest.market_id = :market_id {date_clause}
        ORDER BY latest.target_data_date DESC, latest.created_at DESC
        LIMIT 1
        """,
        params,
    )
    return rows[0] if rows else {}


def get_keyword_level_summary(market_id: str) -> list[dict[str, Any]]:
    return _query_rows(
        """
        SELECT relevance_level, COUNT(*) AS keyword_count,
               SUM(COALESCE(search_volume, 0)) AS search_volume,
               SUM(COALESCE(category_search_volume, 0)) AS category_search_volume,
               AVG(relevance_weight) AS average_relevance_weight
        FROM keyword_pool_config
        WHERE market_id = :market_id AND status = 'active'
          AND relevance_level IN ('high', 'mid', 'low')
        GROUP BY relevance_level
        ORDER BY FIELD(relevance_level, 'high', 'mid', 'low')
        """,
        {"market_id": market_id},
    )


def get_keyword_level_trend(
    market_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate historical keyword traffic by the confirmed relevance tiers."""
    clauses, params = _date_clauses(market_id, date_from, date_to)
    clauses.append("relevance_level IN ('high', 'mid', 'low')")
    return _query_rows(
        f"""
        SELECT date, relevance_level,
               COUNT(DISTINCT keyword) AS keyword_count,
               SUM(COALESCE(keyword_traffic, 0)) AS keyword_traffic
        FROM asin_keyword_snapshot_daily
        WHERE {' AND '.join(clauses)}
        GROUP BY date, relevance_level
        ORDER BY date, FIELD(relevance_level, 'high', 'mid', 'low')
        """,
        params,
    )


def get_gap_contributors(
    market_id: str,
    own_asin: str,
    competitor_asins: list[str],
    date: str,
) -> list[dict[str, Any]]:
    """Return keyword gaps against every selected competitor in one query."""
    if not competitor_asins:
        return []
    params = {
        "market_id": market_id,
        "date": date,
        "own_asin": own_asin,
        "competitor_asins": competitor_asins,
    }
    return _query_rows(
        text(
            """
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
              ON competitor.market_id = own.market_id
             AND competitor.date = own.date
             AND competitor.keyword = own.keyword
            WHERE own.market_id = :market_id AND own.date = :date
              AND own.child_asin = :own_asin
              AND own.relevance_level IN ('high', 'mid', 'low')
              AND competitor.relevance_level IN ('high', 'mid', 'low')
              AND competitor.child_asin IN :competitor_asins
            ORDER BY traffic_gap DESC
            """
        ).bindparams(bindparam("competitor_asins", expanding=True)),
        params,
    )


def get_comparison(
    market_id: str,
    left_asin: str,
    right_asin: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    selected_asins = [left_asin, *([right_asin] if right_asin else [])]
    rows = get_asin_trends(market_id, selected_asins, date_from, date_to)
    left = [row for row in rows if row.get("child_asin") == left_asin]
    right = (
        [row for row in rows if row.get("child_asin") == right_asin]
        if right_asin
        else []
    )
    market = get_market_trend(market_id, date_from, date_to)
    return {"left_asin": left_asin, "right_asin": right_asin, "left": left, "right": right, "market": market}


def get_daily_reports(
    market_id: str = DEFAULT_MARKET_ID,
    report_date: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["market_id = :market_id"]
    params: dict[str, Any] = {"market_id": market_id}
    if report_date:
        clauses.append("report_date = :report_date")
        params["report_date"] = report_date
    return _query_rows(
        f"""
        SELECT *
        FROM daily_report_result
        WHERE {' AND '.join(clauses)}
        ORDER BY report_date DESC, id DESC
        LIMIT 30
        """,
        params,
    )


def list_run_logs(market_id: str = DEFAULT_MARKET_ID, limit: int = 30) -> list[dict[str, Any]]:
    return _query_rows(
        """
        SELECT *
        FROM market_run_log
        WHERE market_id = :market_id
        ORDER BY created_at DESC
        LIMIT :limit
        """,
        {"market_id": market_id, "limit": int(limit)},
    )


def _date_clauses(
    market_id: str,
    date_from: str | None,
    date_to: str | None,
) -> tuple[list[str], dict[str, Any]]:
    clauses = ["market_id = :market_id"]
    params: dict[str, Any] = {"market_id": market_id}
    if date_from:
        clauses.append("date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append("date <= :date_to")
        params["date_to"] = date_to
    return clauses, params


def _query_rows(
    statement: str | TextClause,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    sql = text(statement) if isinstance(statement, str) else statement
    with get_market_engine().connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [_json_ready(dict(row)) for row in rows]


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value



