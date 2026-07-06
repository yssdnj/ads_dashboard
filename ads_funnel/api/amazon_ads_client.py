from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "http://localhost:5010"
TIMEOUT_SECONDS = 20
MAX_QPS = 2
MIN_REQUEST_INTERVAL_SECONDS = 1 / MAX_QPS
MAX_CAMPAIGN_PAGES = 30
MAX_TOTAL_CALLS = 31


class AmazonAdsApiError(RuntimeError):
    pass


class AmazonAdsRateLimitError(AmazonAdsApiError):
    pass


@dataclass(frozen=True)
class AmazonAdsMetadata:
    campaigns: list[dict[str, Any]]
    portfolios: list[dict[str, Any]]
    api_calls_used: int
    campaign_pages_fetched: int
    rate_limit_qps: int


class RateLimitedAmazonAdsSession:
    def __init__(self):
        self.calls_used = 0
        self._last_call_at = 0.0

    def post_json(self, path: str, country_code: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.calls_used >= MAX_TOTAL_CALLS:
            raise AmazonAdsRateLimitError(
                f"Amazon Ads 数据同步已达到单次调用上限 {MAX_TOTAL_CALLS}，"
                "请稍后重试或调高分页上限。"
            )
        now = time.monotonic()
        wait = MIN_REQUEST_INTERVAL_SECONDS - (now - self._last_call_at)
        if wait > 0:
            time.sleep(wait)
        self._last_call_at = time.monotonic()
        self.calls_used += 1
        return _post_json(path, country_code, body)


def map_bidding_strategy(value: str) -> str:
    value = str(value or "").strip()
    return {
        "AUTO_FOR_SALES": "UP",
        "LEGACY_FOR_SALES": "DW",
        "MANUAL": "FX",
    }.get(value, value)


def map_placement_percentages(placements: list[dict[str, Any]] | None) -> dict[str, int]:
    out = {"top_pct": 0, "rest_pct": 0, "pp_pct": 0}
    key_map = {
        "PLACEMENT_TOP": "top_pct",
        "PLACEMENT_REST_OF_SEARCH": "rest_pct",
        "PLACEMENT_PRODUCT_PAGE": "pp_pct",
    }
    for item in placements or []:
        key = key_map.get(str(item.get("placement", "")).strip())
        if not key:
            continue
        try:
            out[key] = int(float(item.get("percentage") or 0))
        except (TypeError, ValueError):
            out[key] = 0
    return out


def normalize_campaign(raw: dict[str, Any]) -> dict[str, Any]:
    dynamic = raw.get("dynamicBidding") or {}
    return {
        "campaign_name": str(raw.get("name") or "").strip(),
        "campaign_id": str(raw.get("campaignId") or "").strip(),
        "portfolio_id": str(raw.get("portfolioId") or "").strip(),
        "bidding_strategy": map_bidding_strategy(dynamic.get("strategy", "")),
        **map_placement_percentages(dynamic.get("placementBidding") or []),
    }


def normalize_portfolio(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "portfolio_id": str(
            raw.get("portfolioId") or raw.get("portfolio_id") or raw.get("id") or ""
        ).strip(),
        "portfolio_name": str(raw.get("name") or raw.get("portfolioName") or "").strip(),
    }


def _post_json(path: str, country_code: str, body: dict[str, Any]) -> dict[str, Any]:
    req = Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Country-Code": country_code},
        method="POST",
    )
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise AmazonAdsApiError(
            "Amazon Ads API 服务不可用，请先启动 D:\\python\\amazon_ads_api 后再点击数据同步。"
        ) from exc
    if "error" in payload:
        raise AmazonAdsApiError(str(payload["error"]))
    return payload


def fetch_sp_campaigns(
    country_code: str, session: RateLimitedAmazonAdsSession
) -> tuple[list[dict[str, Any]], int]:
    campaigns: list[dict[str, Any]] = []
    next_token = ""
    pages = 0
    while True:
        if pages >= MAX_CAMPAIGN_PAGES:
            raise AmazonAdsRateLimitError(
                f"Campaign 分页达到上限 {MAX_CAMPAIGN_PAGES} 页，"
                "已停止同步以控制接口调用次数。"
            )
        body: dict[str, Any] = {"state_filter": "ENABLED,PAUSED", "max_results": 100}
        if next_token:
            body["next_token"] = next_token
        payload = session.post_json("/api/sp/campaigns/list", country_code, body)
        pages += 1
        data = payload.get("data") or {}
        campaigns.extend(normalize_campaign(c) for c in data.get("campaigns", []))
        next_token = data.get("nextToken") or ""
        if not next_token:
            break
    return [c for c in campaigns if c["campaign_id"] and c["campaign_name"]], pages


def fetch_portfolios(
    country_code: str, session: RateLimitedAmazonAdsSession
) -> list[dict[str, str]]:
    payload = session.post_json("/api/portfolios/list", country_code, {})
    data = payload.get("data") or []
    if isinstance(data, dict):
        data = data.get("portfolios", [])
    portfolios = [normalize_portfolio(p) for p in data]
    return [p for p in portfolios if p["portfolio_id"] and p["portfolio_name"]]


def fetch_metadata(country_code: str) -> AmazonAdsMetadata:
    session = RateLimitedAmazonAdsSession()
    campaigns, pages = fetch_sp_campaigns(country_code, session)
    portfolios = fetch_portfolios(country_code, session)
    return AmazonAdsMetadata(
        campaigns=campaigns,
        portfolios=portfolios,
        api_calls_used=session.calls_used,
        campaign_pages_fetched=pages,
        rate_limit_qps=MAX_QPS,
    )
