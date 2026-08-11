from pathlib import Path
from math import inf, nan

import pytest
from openpyxl import Workbook

from ads_funnel.api.market_monitoring.keyword_catalog import (
    CatalogValidationError,
    KeywordCatalogRow,
    KEYWORD_COLUMN,
    RELEVANCE_WEIGHT_COLUMN,
    REQUIRED_COLUMNS,
    SHEET_NAME,
    load_keyword_catalog,
    normalize_keyword,
    validate_keyword_catalog,
)


WORKBOOK = (
    Path(__file__).parents[1]
    / "docs"
    / "US_Dog Slip Leads_关键词洞察列表_2026-06_高中低关键词.xlsx"
)


def _catalog_row(**overrides):
    values = {
        "keyword": "slip lead",
        "translation": "牵引绳",
        "relevance_level": "high",
        "relevance_weight": 0.925,
        "keyword_rank": None,
        "search_volume": None,
        "category_search_volume": None,
        "cpc": None,
        "cpc_range": None,
        "click_conversion_rate": None,
        "competitive_difficulty": None,
        "organic_scroll_rate": None,
    }
    values.update(overrides)
    return KeywordCatalogRow(**values)


def _write_catalog_workbook(path: Path, **overrides):
    values = {
        KEYWORD_COLUMN: "slip lead",
        "翻译": "牵引绳",
        "关键词排名": 1,
        "月搜索量": 100,
        "相关性": "高相关",
        RELEVANCE_WEIGHT_COLUMN: 0.9,
        "类目相关搜索量": 100,
        "CPC建议竞价($)": 0.5,
        "建议竞价范围($)": "0.40 - 0.60",
        "点击转化率(均值)": 0.1,
        "竞争难度": 50,
        "自然位滚动率": 1.5,
    }
    values.update(overrides)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    sheet.append(REQUIRED_COLUMNS)
    sheet.append([values[column] for column in REQUIRED_COLUMNS])
    workbook.save(path)
    workbook.close()


def test_missing_authoritative_workbook_has_actionable_provisioning_error(tmp_path):
    missing = tmp_path / "missing-keyword-catalog.xlsx"

    with pytest.raises(
        CatalogValidationError,
        match=r"Keyword source workbook is missing.*--workbook",
    ):
        load_keyword_catalog(missing)


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
    row = _catalog_row(keyword=" Slip   Lead ")
    duplicate = _catalog_row(keyword="slip lead")

    with pytest.raises(CatalogValidationError, match="duplicate keyword: slip lead"):
        validate_keyword_catalog([row, duplicate], expected_counts=None)


@pytest.mark.parametrize("weight", ["0.5", None, nan, inf])
def test_invalid_external_relevance_weight_fails_validation(weight):
    with pytest.raises(
        CatalogValidationError,
        match=r"invalid 类目相关度 for slip lead",
    ):
        validate_keyword_catalog(
            [_catalog_row(relevance_weight=weight)], expected_counts=None
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [(RELEVANCE_WEIGHT_COLUMN, True), ("月搜索量", False)],
)
def test_loader_rejects_boolean_numeric_values(tmp_path, column, value):
    workbook_path = tmp_path / "catalog.xlsx"
    _write_catalog_workbook(workbook_path, **{column: value})

    with pytest.raises(CatalogValidationError, match=rf"invalid {column} for slip lead"):
        load_keyword_catalog(workbook_path)
