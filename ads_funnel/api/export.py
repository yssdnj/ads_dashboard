"""
export.py — 从数据库数据生成各种导出格式
"""

import json, re, io
from pathlib import Path

TEMPLATE_PATH = Path(__file__).parent.parent / 'template.html'
MARKET_MONITOR_CSS_PATH = (
    Path(__file__).parent.parent / 'frontend' / 'static' / 'market-monitor.css'
)
MARKET_MONITOR_JS_PATH = (
    Path(__file__).parent.parent / 'frontend' / 'static' / 'market-monitor.js'
)
MARKET_MONITOR_CSS_MARKER = '/* __MARKET_MONITOR_CSS__ */'
MARKET_MONITOR_JS_MARKER = '/* __MARKET_MONITOR_JS__ */'

# ── 国家代码映射（收口：前后端统一从此处取值）────────────────────────────────
_COUNTRY_CODE_MAP: dict[str, str] = {
    '美国': 'US', 'United States': 'US', 'US': 'US',
    '英国': 'UK', 'United Kingdom': 'UK', 'UK': 'UK',
    '德国': 'DE', 'Germany': 'DE', 'DE': 'DE',
    '法国': 'FR', 'France': 'FR', 'FR': 'FR',
    '加拿大': 'CA', 'Canada': 'CA', 'CA': 'CA',
    '日本': 'JP', 'Japan': 'JP', 'JP': 'JP',
    '意大利': 'IT', 'Italy': 'IT', 'IT': 'IT',
    '西班牙': 'ES', 'Spain': 'ES', 'ES': 'ES',
}


def country_code(country: str) -> str:
    """将任意国家表示（中文/英文/ISO码）转为两字母代码，未匹配返回空字符串。"""
    return _COUNTRY_CODE_MAP.get(country.strip(), '') if country else ''

# 默认 ACoS 目标值（与模板一致）
DEFAULT_TARGETS = {
    'SL': 35, 'DL': 35, 'Toy': 35, 'ToyDH': 40,
    'DSL2': 40, 'MFL': 40, 'SFM': 40, 'ShortL': 40, 'WB': 40, 'Other': 40
}

NAV_BAR = ''  # 已移除顶部导航栏


class TemplateAssetMarkerError(ValueError):
    """Raised when template.html violates the frontend asset marker contract."""


def _json_for_script(value, *, compact: bool = False) -> str:
    """Serialize JSON without allowing data to terminate an HTML script tag."""
    kwargs = {'ensure_ascii': False}
    if compact:
        kwargs['separators'] = (',', ':')
    serialized = json.dumps(value, **kwargs)
    return serialized.translate(str.maketrans({
        '<': r'\u003c',
        '>': r'\u003e',
        '&': r'\u0026',
        '\u2028': r'\u2028',
        '\u2029': r'\u2029',
    }))


def _inline_market_monitor_assets(template: str) -> str:
    """Inline Market Monitor assets so web and exported reports share one build."""
    for marker in (MARKET_MONITOR_CSS_MARKER, MARKET_MONITOR_JS_MARKER):
        marker_count = template.count(marker)
        if marker_count != 1:
            raise TemplateAssetMarkerError(
                f'template.html must contain exactly one {marker} marker; '
                f'found {marker_count}'
            )
    return (
        template
        .replace(
            MARKET_MONITOR_CSS_MARKER,
            MARKET_MONITOR_CSS_PATH.read_text(encoding='utf-8'),
            1,
        )
        .replace(
            MARKET_MONITOR_JS_MARKER,
            MARKET_MONITOR_JS_PATH.read_text(encoding='utf-8'),
            1,
        )
    )


def _build_html(data: dict, title: str, acos_targets: dict,
                report_id: int = None) -> str:
    """
    将 data 注入 template.html，生成完整 HTML 字符串。
    report_id != None 时添加导航栏（Web App 模式）。
    """
    tmpl = _inline_market_monitor_assets(TEMPLATE_PATH.read_text(encoding='utf-8'))

    weeks    = data.get('wk', [])
    wk_dates = data.get('wk_dates', {})
    targets  = {**DEFAULT_TARGETS, **acos_targets}

    raw_json     = _json_for_script(data, compact=True)
    targets_js   = _json_for_script(targets)
    weeks_js     = _json_for_script(weeks)
    wk_dates_js  = _json_for_script(wk_dates)

    # 0. 注入国家（中文名 + 两字母代码，收口于 export.country_code()）
    _country      = title.split()[0] if title else ''
    _country_code = country_code(_country)
    tmpl = tmpl.replace("'__REPORT_COUNTRY__'",      _json_for_script(_country),      1)
    tmpl = tmpl.replace("'__REPORT_COUNTRY_CODE__'", _json_for_script(_country_code), 1)

    # 1. 注入 RAW 数据
    tmpl = tmpl.replace('const RAW = __RAW_JSON__;',
                        f'const RAW = {raw_json};', 1)

    # 2. 注入 WEEKS / WK_DATES / WK_ISO_DATES
    tmpl = re.sub(
        r"const WEEKS\s*=\s*\[.*?\];",
        lambda _match: f"const WEEKS = {weeks_js};",
        tmpl,
        count=1,
    )
    tmpl = re.sub(
        r"const WK_DATES\s*=\s*\{.*?\};",
        lambda _match: f"const WK_DATES = {wk_dates_js};",
        tmpl,
        count=1,
    )
    wk_iso_dates_js = _json_for_script(data.get('wk_iso_dates', {}))
    tmpl = re.sub(
        r"const WK_ISO_DATES\s*=\s*\{.*?\};",
        lambda _match: f"const WK_ISO_DATES = {wk_iso_dates_js};",
        tmpl, count=1,
    )

    # 3. 注入 ACoS 目标值（替换默认值 + 移除 localStorage 读取）
    tmpl = re.sub(
        r"let acosTargets\s*=\s*\{[^}]+\};",
        lambda _match: f"let acosTargets = {targets_js};",
        tmpl, count=1
    )
    # 移除 localStorage 加载（已由数据库替代）
    tmpl = tmpl.replace(
        "try{const s=localStorage.getItem('acosTargets');if(s)acosTargets={...acosTargets,...JSON.parse(s)};}catch(e){}",
        "/* acos targets loaded from db */"
    )

    # 4. 标题固定为应用名，不随报告名变化
    tmpl = re.sub(r'<title>.*?</title>', '<title>广告数据 漏斗分析</title>', tmpl, count=1)

    # 5. Web App 导航栏
    if report_id is not None:
        nav = NAV_BAR.format(title=title, report_id=report_id)
        tmpl = tmpl.replace('<div id="topbar"', nav + '\n<div id="topbar"', 1)
        # 注入 report_id 供 JS 使用（未来扩展）
        tmpl = tmpl.replace(
            'const RAW =',
            f'const REPORT_ID = {report_id};\nconst RAW =',
            1
        )

    return tmpl


def to_html_webapp(data: dict, title: str, acos_targets: dict,
                   report_id: int) -> str:
    """生成 Web App 版（含导航栏）"""
    return _build_html(data, title, acos_targets, report_id=report_id)


def to_html_export(data: dict, title: str, acos_targets: dict) -> str:
    """生成可离线分享的独立 HTML"""
    return _build_html(data, title, acos_targets, report_id=None)


def to_csv(data: dict) -> str:
    """生成广告活动汇总 CSV"""
    lines = ['活动名称,广告组合,商品,类别,类型,状态,花费,销售额,点击,曝光,订单,ACoS%,ROAS,CTR%,CVR%,CPC']
    for c in data.get('camps', []):
        lines.append(','.join(str(x or '') for x in [
            c['n'], c['po'], c['p'], c['c'], c['ty'], c['st'],
            c.get('sp',''), c.get('sl',''), c.get('cl',''), c.get('im',''), c.get('or', c.get('or_','')),
            c.get('ac',''), c.get('ro',''), c.get('ct',''), c.get('cv',''), c.get('cp','')
        ]))
    return '\n'.join(lines)
