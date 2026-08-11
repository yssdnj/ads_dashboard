"""FastAPI routes for the market monitor dashboard."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from . import repository as db
from . import service

router = APIRouter(prefix="/api/market-monitor", tags=["market-monitor"])


@router.get("/health")
def health():
    return {"status": "ok", "database": db.MARKET_DB_NAME, "market_id": db.DEFAULT_MARKET_ID}


@router.get("/markets")
def markets():
    return {"markets": db.list_markets()}


@router.get("/dates")
def dates(market_id: str = db.DEFAULT_MARKET_ID):
    return {"market_id": market_id, "dates": db.get_available_dates(market_id)}


@router.get("/date-context")
def date_context(market_id: str = db.DEFAULT_MARKET_ID):
    return service.get_date_context(market_id)


@router.get("/market-trend")
def market_trend(
    market_id: str = db.DEFAULT_MARKET_ID,
    date_from: str | None = None,
    date_to: str | None = None,
):
    return {"market_id": market_id, "rows": db.get_market_trend(market_id, date_from, date_to)}


@router.get("/dashboard")
def dashboard(
    market_id: str = db.DEFAULT_MARKET_ID,
    date_from: date | None = None,
    date_to: date | None = None,
):
    start, end = _date_range(date_from, date_to)
    return service.get_dashboard(market_id, start, end)


@router.get("/asins")
def asins(market_id: str = db.DEFAULT_MARKET_ID):
    return {"market_id": market_id, "asins": db.list_monitoring_asins(market_id)}


@router.get("/asin-trend")
def asin_trend(
    child_asin: str,
    market_id: str = db.DEFAULT_MARKET_ID,
    date_from: str | None = None,
    date_to: str | None = None,
):
    child_asin = child_asin.strip().upper()
    if not child_asin:
        raise HTTPException(400, "child_asin is required")
    return {
        "market_id": market_id,
        "child_asin": child_asin,
        "rows": db.get_asin_trend(market_id, child_asin, date_from, date_to),
    }


@router.get("/asin-snapshots")
def asin_snapshots(
    market_id: str = db.DEFAULT_MARKET_ID,
    date: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
):
    return {
        "market_id": market_id,
        "date": date,
        "asins": db.get_asin_snapshots(market_id, date, limit),
    }


@router.get("/keywords")
def keywords(
    market_id: str = db.DEFAULT_MARKET_ID,
    relevance_level: str | None = Query(default=None, pattern="^(high|mid|low)$"),
    date_from: date | None = None,
    date_to: date | None = None,
):
    start, end = _date_range(date_from, date_to)
    return service.get_keywords(market_id, relevance_level, start, end)


@router.get("/keyword-summary")
def keyword_summary(
    market_id: str = db.DEFAULT_MARKET_ID,
    date_from: date | None = None,
    date_to: date | None = None,
):
    start, end = _date_range(date_from, date_to)
    return service.get_keyword_summary(market_id, start, end)


@router.get("/keyword-trend")
def keyword_trend(
    keyword: str,
    market_id: str = db.DEFAULT_MARKET_ID,
    date_from: str | None = None,
    date_to: str | None = None,
):
    keyword = keyword.strip()
    if not keyword:
        raise HTTPException(400, "keyword is required")
    return {
        "market_id": market_id,
        "keyword": keyword,
        "rows": db.get_keyword_trend(market_id, keyword, date_from, date_to),
    }


@router.get("/asin-keywords")
def asin_keywords(
    child_asin: str,
    market_id: str = db.DEFAULT_MARKET_ID,
    date: str | None = None,
):
    child_asin = child_asin.strip().upper()
    if not child_asin:
        raise HTTPException(400, "child_asin is required")
    return {
        "market_id": market_id,
        "child_asin": child_asin,
        "date": date,
        "keywords": db.get_asin_keyword_composition(market_id, child_asin, date),
    }


@router.get("/keyword-competition")
def keyword_competition(
    keyword: str,
    market_id: str = db.DEFAULT_MARKET_ID,
    date: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    keyword = keyword.strip()
    if not keyword:
        raise HTTPException(400, "keyword is required")
    return {
        "market_id": market_id,
        "keyword": keyword,
        "date": date,
        "asins": db.get_keyword_asin_competition(market_id, keyword, date, limit),
    }


@router.get("/comparison")
def comparison(
    market_id: str,
    own_asin: str,
    competitor_asin: list[str] = Query(default=[]),
    scenario: str = Query(
        default="market_share",
        pattern="^(market_share|keyword_competition|ad_competition|growth_quality)$",
    ),
    metric: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    start, end = _date_range(date_from, date_to)
    try:
        return service.get_comparison(
            market_id,
            own_asin,
            competitor_asin,
            scenario,
            metric,
            start,
            end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _date_range(
    date_from: date | None,
    date_to: date | None,
) -> tuple[str | None, str | None]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
        )
    return (
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
    )


@router.get("/reports")
def reports(market_id: str = db.DEFAULT_MARKET_ID, report_date: str | None = None):
    return {"market_id": market_id, "reports": db.get_daily_reports(market_id, report_date)}


@router.get("/runs")
def runs(market_id: str = db.DEFAULT_MARKET_ID, limit: int = Query(default=30, ge=1, le=200)):
    return {"market_id": market_id, "runs": db.list_run_logs(market_id, limit)}

