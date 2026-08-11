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
    / "US_Dog Slip Leads_\u5173\u952e\u8bcd\u6d1e\u5bdf\u5217\u8868_2026-06_\u9ad8\u4e2d\u4f4e\u5173\u952e\u8bcd.xlsx"
)
EXPECTED_KEYWORD_COUNTS = {"high": 77, "mid": 40, "low": 139}
