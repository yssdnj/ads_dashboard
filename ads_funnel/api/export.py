"""
export.py — 从数据库数据生成各种导出格式
"""

import json, re, io
from pathlib import Path

TEMPLATE_PATH = Path(__file__).parent.parent / 'template.html'

# 默认 ACoS 目标值（与模板一致）
DEFAULT_TARGETS = {
    'SL': 35, 'DL': 35, 'Toy': 35, 'ToyDH': 40,
    'DSL2': 40, 'MFL': 40, 'SFM': 40, 'ShortL': 40, 'WB': 40, 'Other': 40
}

NAV_BAR = ''  # 已移除顶部导航栏


def _build_html(data: dict, title: str, acos_targets: dict,
                report_id: int = None) -> str:
    """
    将 data 注入 template.html，生成完整 HTML 字符串。
    report_id != None 时添加导航栏（Web App 模式）。
    """
    tmpl = TEMPLATE_PATH.read_text(encoding='utf-8')

    weeks    = data.get('wk', [])
    wk_dates = data.get('wk_dates', {})
    targets  = {**DEFAULT_TARGETS, **acos_targets}

    raw_json     = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    targets_js   = json.dumps(targets, ensure_ascii=False)
    weeks_js     = json.dumps(weeks,   ensure_ascii=False)
    wk_dates_js  = json.dumps(wk_dates, ensure_ascii=False)

    # 1. 注入 RAW 数据
    tmpl = tmpl.replace('const RAW = __RAW_JSON__;',
                        f'const RAW = {raw_json};', 1)

    # 2. 注入 WEEKS / WK_DATES
    tmpl = re.sub(r"const WEEKS\s*=\s*\[.*?\];",
                  f"const WEEKS = {weeks_js};", tmpl, count=1)
    tmpl = re.sub(r"const WK_DATES\s*=\s*\{.*?\};",
                  f"const WK_DATES = {wk_dates_js};", tmpl, count=1)

    # 3. 注入 ACoS 目标值（替换默认值 + 移除 localStorage 读取）
    tmpl = re.sub(
        r"let acosTargets\s*=\s*\{[^}]+\};",
        f"let acosTargets = {targets_js};",
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
