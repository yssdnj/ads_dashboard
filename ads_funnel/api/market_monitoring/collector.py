"""Write helpers for market monitor collection runs.

This module persists data that was collected by an external Codex/MCP workflow.
It intentionally does not call MCP or SP-API directly.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import repository as db
from .constants import RELEVANCE_LEVELS


_SUPPORTED_NORMALIZED_SECTIONS = frozenset(
    {
        "listing_variations",
        "market_snapshot",
        "asin_snapshots",
        "keyword_snapshots",
        "asin_keyword_snapshots",
        "keyword_asin_competition",
    }
)
_ROW_REQUIRED_FIELDS = {
    "listing_variations": {
        "parent_asin", "child_asin", "is_seed_child", "is_representative_child",
        "first_seen_date", "last_seen_date",
    },
    "market_snapshot": {
        "total_traffic", "weighted_traffic", "organic_traffic", "ad_traffic",
        "high_relevance_traffic", "mid_relevance_traffic", "low_relevance_traffic",
        "top_asin_share", "top_keyword_share", "listing_count", "asin_count",
        "keyword_count",
    },
    "asin_snapshots": {
        "parent_asin", "child_asin", "brand", "title", "price", "rating", "reviews",
        "total_traffic", "organic_traffic", "ad_traffic", "weighted_traffic",
        "high_keyword_share", "mid_keyword_share", "low_keyword_share",
        "rank_in_market", "is_own_product",
    },
    "keyword_snapshots": {
        "keyword", "relevance_level", "relevance_weight", "search_volume", "aba_rank",
        "cpc", "competitive_difficulty", "click_conversion_rate", "total_market_traffic",
        "market_traffic_share",
    },
    "asin_keyword_snapshots": {
        "parent_asin", "child_asin", "keyword", "relevance_level", "relevance_weight",
        "keyword_traffic", "asin_keyword_share", "organic_rank", "ad_rank",
        "organic_traffic", "ad_traffic",
    },
    "keyword_asin_competition": {
        "keyword", "relevance_level", "relevance_weight", "parent_asin", "child_asin",
        "brand", "title", "competition_rank", "keyword_traffic", "keyword_traffic_share",
        "organic_rank", "ad_rank", "organic_traffic", "ad_traffic", "is_seed_asin",
        "is_own_product",
    },
}


@contextmanager
def _transaction(connection: Connection | None = None):
    """Use a caller transaction or open exactly one transaction for a public write."""
    if connection is not None:
        yield connection
        return
    with db.get_market_engine().begin() as conn:
        yield conn


def create_run(
    market_id: str,
    run_date: str,
    collect_time_bj: str,
    timezone_basis: str = "America/New_York",
    *,
    connection: Connection | None = None,
) -> str:
    run_id = uuid.uuid4().hex
    with _transaction(connection) as conn:
        _insert_run(
            conn,
            run_id=run_id,
            market_id=market_id,
            run_date=run_date,
            collect_time_bj=collect_time_bj,
            timezone_basis=timezone_basis,
        )
    return run_id


def _insert_run(
    conn: Connection,
    *,
    run_id: str,
    market_id: str,
    run_date: str,
    collect_time_bj: str,
    timezone_basis: str,
) -> None:
    conn.execute(
            text(
                """
                INSERT INTO market_run_log
                  (run_id, market_id, run_date, collect_time_bj, timezone_basis,
                   status, started_at, created_at)
                VALUES
                  (:run_id, :market_id, :run_date, :collect_time_bj, :timezone_basis,
                   'running', NOW(), NOW())
                """
            ),
            {
                "run_id": run_id,
                "market_id": market_id,
                "run_date": run_date,
                "collect_time_bj": collect_time_bj,
                "timezone_basis": timezone_basis,
            },
        )


def finish_run(
    run_id: str,
    status: str,
    target_data_date: str | None = None,
    data_available_through: str | None = None,
    source_summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    *,
    connection: Connection | None = None,
) -> None:
    with _transaction(connection) as conn:
        conn.execute(
            text(
                """
                UPDATE market_run_log
                SET status = :status,
                    target_data_date = :target_data_date,
                    data_available_through = :data_available_through,
                    source_summary_json = :source_summary_json,
                    error_message = :error_message,
                    finished_at = NOW()
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "target_data_date": target_data_date,
                "data_available_through": data_available_through,
                "source_summary_json": _json_or_none(source_summary),
                "error_message": error_message,
            },
        )


def record_raw_request(
    run_id: str,
    market_id: str,
    source_channel: str,
    source_provider: str,
    source_tool: str,
    request_params: dict[str, Any],
    purpose: str | None = None,
    request_id: str | None = None,
    *,
    connection: Connection | None = None,
) -> str:
    request_id = request_id or uuid.uuid4().hex
    with _transaction(connection) as conn:
        conn.execute(
            text(
                """
                INSERT INTO raw_request_log
                  (request_id, run_id, market_id, source_channel, source_provider,
                   source_tool, request_params_json, purpose)
                VALUES
                  (:request_id, :run_id, :market_id, :source_channel, :source_provider,
                   :source_tool, :request_params_json, :purpose)
                ON DUPLICATE KEY UPDATE
                  request_params_json = VALUES(request_params_json),
                  purpose = VALUES(purpose)
                """
            ),
            {
                "request_id": request_id,
                "run_id": run_id,
                "market_id": market_id,
                "source_channel": source_channel,
                "source_provider": source_provider,
                "source_tool": source_tool,
                "request_params_json": json.dumps(request_params, ensure_ascii=False),
                "purpose": purpose,
            },
        )
    return request_id


def record_raw_response(
    request_id: str,
    run_id: str,
    market_id: str,
    source_channel: str,
    source_provider: str,
    source_tool: str,
    response_status: str,
    response_json: dict[str, Any] | list[Any] | None = None,
    cost_credits: float | None = None,
    error_message: str | None = None,
    response_id: str | None = None,
    *,
    connection: Connection | None = None,
) -> str:
    response_id = response_id or uuid.uuid4().hex
    with _transaction(connection) as conn:
        conn.execute(
            text(
                """
                INSERT INTO raw_response_log
                  (response_id, request_id, run_id, market_id, source_channel,
                   source_provider, source_tool, response_status, response_json,
                   cost_credits, error_message)
                VALUES
                  (:response_id, :request_id, :run_id, :market_id, :source_channel,
                   :source_provider, :source_tool, :response_status, :response_json,
                   :cost_credits, :error_message)
                ON DUPLICATE KEY UPDATE
                  response_status = VALUES(response_status),
                  response_json = VALUES(response_json),
                  cost_credits = VALUES(cost_credits),
                  error_message = VALUES(error_message)
                """
            ),
            {
                "response_id": response_id,
                "request_id": request_id,
                "run_id": run_id,
                "market_id": market_id,
                "source_channel": source_channel,
                "source_provider": source_provider,
                "source_tool": source_tool,
                "response_status": response_status,
                "response_json": _json_or_none(response_json),
                "cost_credits": cost_credits,
                "error_message": error_message,
            },
        )
    return response_id


def upsert_listing_variations(
    rows: list[dict[str, Any]], *, connection: Connection | None = None
) -> None:
    if not rows:
        return
    with _transaction(connection) as conn:
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO listing_variation_map
                      (market_id, parent_asin, child_asin, is_seed_child,
                       is_representative_child, first_seen_date, last_seen_date,
                       source_channel, source_provider, source_tool, source_request_id)
                    VALUES
                      (:market_id, :parent_asin, :child_asin, :is_seed_child,
                       :is_representative_child, :first_seen_date, :last_seen_date,
                       :source_channel, :source_provider, :source_tool, :source_request_id)
                    ON DUPLICATE KEY UPDATE
                      is_seed_child = VALUES(is_seed_child),
                      is_representative_child = VALUES(is_representative_child),
                      last_seen_date = VALUES(last_seen_date),
                      source_channel = VALUES(source_channel),
                      source_provider = VALUES(source_provider),
                      source_tool = VALUES(source_tool),
                      source_request_id = VALUES(source_request_id),
                      updated_at = NOW()
                    """
                ),
                row,
            )


def replace_market_snapshot(
    row: dict[str, Any], *, connection: Connection | None = None
) -> None:
    row = dict(row)
    row["strong_relevance_traffic"] = 0
    with _transaction(connection) as conn:
        conn.execute(
            text(
                """
                REPLACE INTO market_snapshot_daily
                  (date, market_id, total_traffic, weighted_traffic, organic_traffic,
                   ad_traffic, strong_relevance_traffic, high_relevance_traffic,
                   mid_relevance_traffic, low_relevance_traffic, top_asin_share,
                   top_keyword_share, listing_count, asin_count, keyword_count,
                   source_channel, source_provider, source_tool, source_request_id)
                VALUES
                  (:date, :market_id, :total_traffic, :weighted_traffic, :organic_traffic,
                   :ad_traffic, :strong_relevance_traffic, :high_relevance_traffic,
                   :mid_relevance_traffic, :low_relevance_traffic, :top_asin_share,
                   :top_keyword_share, :listing_count, :asin_count, :keyword_count,
                   :source_channel, :source_provider, :source_tool, :source_request_id)
                """
            ),
            row,
        )


def replace_asin_snapshots(
    market_id: str, date: str, rows: list[dict[str, Any]], *,
    connection: Connection | None = None,
) -> None:
    _replace_daily_rows("asin_snapshot_daily", market_id, date, rows, _insert_asin_snapshot, connection)


def upsert_asin_snapshots(
    rows: list[dict[str, Any]], *, connection: Connection | None = None
) -> None:
    _upsert_daily_rows(rows, _insert_asin_snapshot, connection)


def replace_keyword_snapshots(
    market_id: str, date: str, rows: list[dict[str, Any]], *,
    connection: Connection | None = None,
) -> None:
    _validate_snapshot_relevance_levels(rows)
    _replace_daily_rows("keyword_snapshot_daily", market_id, date, rows, _insert_keyword_snapshot, connection)


def upsert_keyword_snapshots(
    rows: list[dict[str, Any]], *, connection: Connection | None = None
) -> None:
    _validate_snapshot_relevance_levels(rows)
    _upsert_daily_rows(rows, _insert_keyword_snapshot, connection)


def replace_asin_keyword_snapshots(
    market_id: str, date: str, rows: list[dict[str, Any]], *,
    connection: Connection | None = None,
) -> None:
    _validate_snapshot_relevance_levels(rows)
    _replace_daily_rows("asin_keyword_snapshot_daily", market_id, date, rows, _insert_asin_keyword_snapshot, connection)


def upsert_asin_keyword_snapshots(
    rows: list[dict[str, Any]], *, connection: Connection | None = None
) -> None:
    _validate_snapshot_relevance_levels(rows)
    _upsert_daily_rows(rows, _insert_asin_keyword_snapshot, connection)


def replace_keyword_asin_competition(
    market_id: str, date: str, rows: list[dict[str, Any]], *,
    connection: Connection | None = None,
) -> None:
    _validate_snapshot_relevance_levels(rows)
    _replace_daily_rows("keyword_asin_competition_daily", market_id, date, rows, _insert_keyword_asin_competition, connection)


def upsert_keyword_asin_competition(
    rows: list[dict[str, Any]], *, connection: Connection | None = None
) -> None:
    _validate_snapshot_relevance_levels(rows)
    _upsert_daily_rows(rows, _insert_keyword_asin_competition, connection)


def persist_daily_source_batch(
    *,
    market_id: str,
    run_date: str,
    collect_time_bj: str,
    source_channel: str,
    source_provider: str,
    source_tool: str,
    request_params: dict[str, Any],
    response_json: dict[str, Any] | list[Any] | None = None,
    target_data_date: str | None = None,
    data_available_through: str | None = None,
    response_status: str = "success",
    cost_credits: float | None = None,
    timezone_basis: str = "America/New_York",
    purpose: str | None = None,
    normalized: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
    write_mode: str = "upsert",
    request_id: str | None = None,
    response_id: str | None = None,
) -> dict[str, Any]:
    """Persist one provider batch without coupling storage to MCP/API clients.

    `source_channel` should describe how data was collected, such as `mcp`,
    `api`, `manual`, or `csv`. `normalized` may include any of these keys:
    `listing_variations`, `market_snapshot`, `asin_snapshots`,
    `keyword_snapshots`, `asin_keyword_snapshots`, and
    `keyword_asin_competition`.
    """
    run_id = uuid.uuid4().hex
    saved_report_id: int | None = None
    normalized_payload = {} if normalized is None else normalized
    request_identifier = request_id or uuid.uuid4().hex
    response_identifier = response_id or uuid.uuid4().hex
    try:
        _validate_daily_batch(
            market_id=market_id,
            date=target_data_date or run_date,
            request_params=request_params,
            response_json=response_json,
            normalized=normalized_payload,
            report=report,
            write_mode=write_mode,
        )
        final_status = "success" if response_status == "success" else "partial_success"
        with db.get_market_engine().begin() as conn:
            _insert_run(
                conn,
                run_id=run_id,
                market_id=market_id,
                run_date=run_date,
                collect_time_bj=collect_time_bj,
                timezone_basis=timezone_basis,
            )
            request_identifier = record_raw_request(
                run_id=run_id,
                market_id=market_id,
                source_channel=source_channel,
                source_provider=source_provider,
                source_tool=source_tool,
                request_params=request_params,
                purpose=purpose,
                request_id=request_identifier,
                connection=conn,
            )
            response_identifier = record_raw_response(
                request_id=request_identifier,
                run_id=run_id,
                market_id=market_id,
                source_channel=source_channel,
                source_provider=source_provider,
                source_tool=source_tool,
                response_status=response_status,
                response_json=response_json,
                cost_credits=cost_credits,
                response_id=response_identifier,
                connection=conn,
            )
            _persist_normalized_sections(
                market_id=market_id,
                date=target_data_date or run_date,
                source_channel=source_channel,
                source_provider=source_provider,
                source_tool=source_tool,
                source_request_id=request_identifier,
                normalized=normalized_payload,
                write_mode=write_mode,
                connection=conn,
            )
            if report is not None:
                report_row = dict(report)
                report_row.setdefault("run_id", run_id)
                report_row.setdefault("market_id", market_id)
                report_row.setdefault("report_date", run_date)
                report_row.setdefault("target_data_date", target_data_date)
                saved_report_id = save_daily_report(report_row, connection=conn)
            finish_run(
                run_id,
                final_status,
                target_data_date=target_data_date,
                data_available_through=data_available_through,
                source_summary={
                    "source_channel": source_channel,
                    "source_provider": source_provider,
                    "source_tool": source_tool,
                    "write_mode": write_mode,
                    "sections": sorted(normalized_payload.keys()),
                },
                connection=conn,
            )
        return {
            "run_id": run_id,
            "request_id": request_identifier,
            "response_id": response_identifier,
            "report_id": saved_report_id,
            "status": final_status,
        }
    except Exception as exc:
        _record_failed_run(
            run_id=run_id,
            market_id=market_id,
            run_date=run_date,
            collect_time_bj=collect_time_bj,
            timezone_basis=timezone_basis,
            target_data_date=target_data_date,
            data_available_through=data_available_through,
            error_message=str(exc),
        )
        raise


def save_daily_report(
    row: dict[str, Any], *, connection: Connection | None = None
) -> int:
    with _transaction(connection) as conn:
        conn.execute(
            text(
                """
                DELETE FROM daily_report_result
                WHERE market_id = :market_id
                  AND report_date = :report_date
                  AND report_type = :report_type
                """
            ),
            {
                "market_id": row["market_id"],
                "report_date": row["report_date"],
                "report_type": row["report_type"],
            },
        )
        result = conn.execute(
            text(
                """
                INSERT INTO daily_report_result
                  (run_id, market_id, report_date, target_data_date, report_type,
                   title, summary_md, report_md, report_html)
                VALUES
                  (:run_id, :market_id, :report_date, :target_data_date, :report_type,
                   :title, :summary_md, :report_md, :report_html)
                """
            ),
            row,
        )
        return int(result.lastrowid)


def _validate_daily_batch(
    *,
    market_id: str,
    date: str,
    request_params: dict[str, Any],
    response_json: dict[str, Any] | list[Any] | None,
    normalized: dict[str, Any],
    report: dict[str, Any] | None,
    write_mode: str,
) -> None:
    """Validate the complete payload before opening its snapshot transaction."""
    if write_mode not in {"upsert", "replace_day"}:
        raise ValueError("write_mode must be 'upsert' or 'replace_day'")
    if not isinstance(request_params, dict):
        raise ValueError("request_params must be an object")
    if not isinstance(normalized, dict):
        raise ValueError("normalized must be an object")
    json.dumps(request_params, ensure_ascii=False)
    if response_json is not None:
        json.dumps(response_json, ensure_ascii=False)
    unknown = sorted(set(normalized) - _SUPPORTED_NORMALIZED_SECTIONS)
    if unknown:
        raise ValueError(f"unsupported normalized sections: {', '.join(unknown)}")

    for section, value in normalized.items():
        if section == "market_snapshot":
            if value is None:
                if write_mode != "replace_day":
                    raise ValueError(
                        "market_snapshot may be null only for replace_day deletion"
                    )
                continue
            rows = [value]
        else:
            if not isinstance(value, list):
                raise ValueError(f"normalized.{section} must be a list")
            rows = value
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"normalized.{section}[{index}] must be an object")
            if row.get("market_id") not in (None, market_id):
                raise ValueError(f"normalized.{section}[{index}] has conflicting market_id")
            if section != "listing_variations" and row.get("date") not in (None, date):
                raise ValueError(f"normalized.{section}[{index}] has conflicting date")
            missing = sorted(_ROW_REQUIRED_FIELDS[section] - set(row))
            if missing:
                raise ValueError(
                    f"normalized.{section}[{index}] missing fields: {', '.join(missing)}"
                )
        if section in {
            "keyword_snapshots",
            "asin_keyword_snapshots",
            "keyword_asin_competition",
        }:
            _validate_snapshot_relevance_levels([dict(row) for row in rows])
    if report is not None:
        if not isinstance(report, dict):
            raise ValueError("report must be an object")
        missing_report = sorted(
            {"report_type", "title", "summary_md", "report_md", "report_html"}
            - set(report)
        )
        if missing_report:
            raise ValueError(f"report missing fields: {', '.join(missing_report)}")


def _record_failed_run(
    *,
    run_id: str,
    market_id: str,
    run_date: str,
    collect_time_bj: str,
    timezone_basis: str,
    target_data_date: str | None,
    data_available_through: str | None,
    error_message: str,
) -> None:
    """Record only failure metadata after the batch transaction has rolled back."""
    with db.get_market_engine().begin() as conn:
        _insert_run(
            conn,
            run_id=run_id,
            market_id=market_id,
            run_date=run_date,
            collect_time_bj=collect_time_bj,
            timezone_basis=timezone_basis,
        )
        finish_run(
            run_id,
            "failed",
            target_data_date=target_data_date,
            data_available_through=data_available_through,
            error_message=error_message,
            connection=conn,
        )


def _replace_daily_rows(
    table: str,
    market_id: str,
    date: str,
    rows: list[dict[str, Any]],
    inserter,
    connection: Connection | None = None,
) -> None:
    with _transaction(connection) as conn:
        conn.execute(
            text(f"DELETE FROM {table} WHERE market_id = :market_id AND date = :date"),
            {"market_id": market_id, "date": date},
        )
        for row in rows:
            inserter(conn, row)


def _upsert_daily_rows(
    rows: list[dict[str, Any]], inserter, connection: Connection | None = None
) -> None:
    if not rows:
        return
    with _transaction(connection) as conn:
        for row in rows:
            inserter(conn, row)


def _persist_normalized_sections(
    *,
    market_id: str,
    date: str,
    source_channel: str,
    source_provider: str,
    source_tool: str,
    source_request_id: str,
    normalized: dict[str, Any],
    write_mode: str,
    connection: Connection | None = None,
) -> None:
    source_defaults = {
        "date": date,
        "market_id": market_id,
        "source_channel": source_channel,
        "source_provider": source_provider,
        "source_tool": source_tool,
        "source_request_id": source_request_id,
    }
    listing_defaults = {
        "market_id": market_id,
        "source_channel": source_channel,
        "source_provider": source_provider,
        "source_tool": source_tool,
        "source_request_id": source_request_id,
    }

    if "listing_variations" in normalized and normalized["listing_variations"]:
        upsert_listing_variations(
            _with_defaults(normalized["listing_variations"], listing_defaults),
            connection=connection,
        )

    if "market_snapshot" in normalized:
        market_snapshot = normalized["market_snapshot"]
        if market_snapshot is not None:
            replace_market_snapshot(
                _with_defaults([market_snapshot], source_defaults)[0],
                connection=connection,
            )
        elif write_mode == "replace_day":
            with _transaction(connection) as conn:
                conn.execute(
                    text(
                        "DELETE FROM market_snapshot_daily "
                        "WHERE market_id=:market_id AND date=:date"
                    ),
                    {"market_id": market_id, "date": date},
                )

    for section, replace_func, upsert_func in (
        ("asin_snapshots", replace_asin_snapshots, upsert_asin_snapshots),
        ("keyword_snapshots", replace_keyword_snapshots, upsert_keyword_snapshots),
        ("asin_keyword_snapshots", replace_asin_keyword_snapshots, upsert_asin_keyword_snapshots),
        ("keyword_asin_competition", replace_keyword_asin_competition, upsert_keyword_asin_competition),
    ):
        if section not in normalized:
            continue
        _persist_daily_rows(
            market_id,
            date,
            _with_defaults(normalized[section], source_defaults),
            replace_func,
            upsert_func,
            write_mode,
            connection,
        )


def _persist_daily_rows(
    market_id: str,
    date: str,
    rows: list[dict[str, Any]],
    replace_func,
    upsert_func,
    write_mode: str,
    connection: Connection | None = None,
) -> None:
    if write_mode == "replace_day":
        replace_func(market_id, date, rows, connection=connection)
    elif rows:
        upsert_func(rows, connection=connection)


def _with_defaults(rows: list[dict[str, Any]], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    enriched = []
    for row in rows:
        item = dict(defaults)
        item.update(row)
        enriched.append(item)
    return enriched


def _validate_snapshot_relevance_levels(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        relevance_level = row.get("relevance_level")
        if relevance_level not in RELEVANCE_LEVELS:
            raise ValueError(f"invalid relevance_level: {relevance_level}")


def _insert_asin_snapshot(conn, row: dict[str, Any]) -> None:
    row = dict(row)
    row["strong_keyword_share"] = 0
    row.setdefault("image_url", None)
    conn.execute(
        text(
            """
            REPLACE INTO asin_snapshot_daily
              (date, market_id, parent_asin, child_asin, brand, title, image_url, price,
               rating, reviews, total_traffic, organic_traffic, ad_traffic,
               weighted_traffic, strong_keyword_share, high_keyword_share,
               mid_keyword_share, low_keyword_share, rank_in_market,
               is_own_product, source_channel, source_provider, source_tool,
               source_request_id)
            VALUES
              (:date, :market_id, :parent_asin, :child_asin, :brand, :title, :image_url, :price,
               :rating, :reviews, :total_traffic, :organic_traffic, :ad_traffic,
               :weighted_traffic, :strong_keyword_share, :high_keyword_share,
               :mid_keyword_share, :low_keyword_share, :rank_in_market,
               :is_own_product, :source_channel, :source_provider, :source_tool,
               :source_request_id)
            """
        ),
        row,
    )


def _insert_keyword_snapshot(conn, row: dict[str, Any]) -> None:
    conn.execute(
        text(
            """
            REPLACE INTO keyword_snapshot_daily
              (date, market_id, keyword, relevance_level, relevance_weight,
               search_volume, aba_rank, cpc, competitive_difficulty,
               click_conversion_rate, total_market_traffic, market_traffic_share,
               source_channel, source_provider, source_tool, source_request_id)
            VALUES
              (:date, :market_id, :keyword, :relevance_level, :relevance_weight,
               :search_volume, :aba_rank, :cpc, :competitive_difficulty,
               :click_conversion_rate, :total_market_traffic, :market_traffic_share,
               :source_channel, :source_provider, :source_tool, :source_request_id)
            """
        ),
        row,
    )


def _insert_asin_keyword_snapshot(conn, row: dict[str, Any]) -> None:
    conn.execute(
        text(
            """
            REPLACE INTO asin_keyword_snapshot_daily
              (date, market_id, parent_asin, child_asin, keyword, relevance_level,
               relevance_weight, keyword_traffic, asin_keyword_share,
               organic_rank, ad_rank, organic_traffic, ad_traffic,
               source_channel, source_provider, source_tool, source_request_id)
            VALUES
              (:date, :market_id, :parent_asin, :child_asin, :keyword, :relevance_level,
               :relevance_weight, :keyword_traffic, :asin_keyword_share,
               :organic_rank, :ad_rank, :organic_traffic, :ad_traffic,
               :source_channel, :source_provider, :source_tool, :source_request_id)
            """
        ),
        row,
    )


def _insert_keyword_asin_competition(conn, row: dict[str, Any]) -> None:
    conn.execute(
        text(
            """
            REPLACE INTO keyword_asin_competition_daily
              (date, market_id, keyword, relevance_level, relevance_weight,
               parent_asin, child_asin, brand, title, competition_rank,
               keyword_traffic, keyword_traffic_share, organic_rank, ad_rank,
               organic_traffic, ad_traffic, is_seed_asin, is_own_product,
               source_channel, source_provider, source_tool, source_request_id)
            VALUES
              (:date, :market_id, :keyword, :relevance_level, :relevance_weight,
               :parent_asin, :child_asin, :brand, :title, :competition_rank,
               :keyword_traffic, :keyword_traffic_share, :organic_rank, :ad_rank,
               :organic_traffic, :ad_traffic, :is_seed_asin, :is_own_product,
               :source_channel, :source_provider, :source_tool, :source_request_id)
            """
        ),
        row,
    )


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)

