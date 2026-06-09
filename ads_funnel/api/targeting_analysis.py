"""
targeting_analysis.py
直接复制 run_targeting_structure_report_v2.py 的全部函数，
底部新增 get_asins_for_product() 和 run_analysis() 作为 API 适配层。
"""
from __future__ import annotations

import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from .gen_data import classify_port

warnings.filterwarnings("ignore")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 以下全部直接复制自 run_targeting_structure_report_v2.py，未做任何修改
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TARGETING_KEY = ["Campaign Name", "Ad Group Name", "Targeting", "Match Type"]

COUNTRY_MAP = {
    '美国': 'United States',
    '英国': 'United Kingdom',
    '德国': 'Germany',
    '法国': 'France',
    '日本': 'Japan',
    '加拿大': 'Canada',
    '意大利': 'Italy',
    '西班牙': 'Spain',
}


def daily_to_summary(df: pd.DataFrame) -> pd.DataFrame:
    """将每日推广商品报告（中文列名）聚合为摘要格式（等同于后台导出的汇总版）。
    已验证：聚合结果与后台直接导出的摘要文件数值完全一致（6709行，误差为0）。
    """
    # 彻底清洗列名：去除所有不可见字符 / 非标准空格 / BOM
    import unicodedata
    df = df.copy()
    df.columns = [
        unicodedata.normalize('NFKC', str(c)).strip()
        for c in df.columns
    ]
    rename_map = {
        '广告组合名称': 'Portfolio name',
        '货币': 'Currency',
        '广告活动名称': 'Campaign Name',
        '广告组名称': 'Ad Group Name',
        '零售商': 'Retailer',
        '国家/地区': 'Country',
        '广告SKU': 'Advertised SKU',
        '广告ASIN': 'Advertised ASIN',
        '展示量': 'Impressions',
        '点击量': 'Clicks',
        '花费': 'Spend',
        '7天总销售额': '7 Day Total Sales',
        '7天总订单数(#)': '7 Day Total Orders (#)',
        '7天总销售量(#)': '7 Day Total Units (#)',
        '7天内广告SKU销售量(#)': '7 Day Advertised SKU Units (#)',
        '7天内其他SKU销售量(#)': '7 Day Other SKU Units (#)',
        '7天内广告SKU销售额': '7 Day Advertised SKU Sales',
        '7天内其他SKU销售额': '7 Day Other SKU Sales',
    }
    df = df.rename(columns=rename_map)
    if 'Country' in df.columns:
        df['Country'] = df['Country'].map(COUNTRY_MAP).fillna(df['Country'])

    group_keys = [
        'Portfolio name', 'Currency', 'Campaign Name', 'Ad Group Name',
        'Retailer', 'Country', 'Advertised SKU', 'Advertised ASIN',
    ]
    sum_cols = [
        'Impressions', 'Clicks', 'Spend', '7 Day Total Sales',
        '7 Day Total Orders (#)', '7 Day Total Units (#)',
        '7 Day Advertised SKU Units (#)', '7 Day Other SKU Units (#)',
        '7 Day Advertised SKU Sales', '7 Day Other SKU Sales',
    ]
    group_keys = [k for k in group_keys if k in df.columns and df[k].notna().any()]
    sum_cols   = [k for k in sum_cols   if k in df.columns]
    agg = df.groupby(group_keys, dropna=False)[sum_cols].sum().reset_index()
    agg.insert(0, 'Start Date', df['日期'].min())
    agg.insert(1, 'End Date',   df['日期'].max())
    return agg


def load_frames(ap_path: Path, tar_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    ap_raw = pd.read_excel(ap_path)
    tar_raw = pd.read_excel(tar_path)
    ap_raw.columns = [c.strip() for c in ap_raw.columns]
    tar_raw.columns = [c.strip() for c in tar_raw.columns]

    # v2：自动识别每日格式（有 '日期' 列），转换为摘要格式后统一处理
    if '日期' in ap_raw.columns:
        ap_raw['日期'] = pd.to_datetime(ap_raw['日期'])
        ap_raw = daily_to_summary(ap_raw)

    ap_raw["Start Date"] = pd.to_datetime(ap_raw["Start Date"])
    ap_raw["End Date"] = pd.to_datetime(ap_raw["End Date"])
    tar_raw["Date"] = pd.to_datetime(tar_raw["Date"])
    return ap_raw, tar_raw


def get_label(row, target_acos: float, avg_clicks_per_order: float) -> str:
    if row["orders"] == 0:
        if row["clicks"] >= avg_clicks_per_order:
            return "🔴 高点击不出单"
        if row["clicks"] > 0:
            return "⚪ 低点击不出单"
        return "— 无点击"
    if pd.notna(row["acos"]) and row["acos"] > target_acos:
        return "⚠️ 高ACoS出单"
    return "✅ 低ACoS出单"


def get_action(row, target_acos: float, core_sales_share: float, core_keys: set) -> tuple[str, str, str, str]:
    label = row["label"]
    key = (
        row["Campaign Name"],
        row["Ad Group Name"],
        row["Targeting"],
        row["Match Type"],
    )
    is_core = key in core_keys or row["sales_share"] >= core_sales_share

    if "无点击" in label:
        return "观察", "0%", "$0.00", "无流量，检查出价是否过低"

    if "低ACoS出单" in label:
        if is_core:
            return "🔒 保护", "0%", "$0.00", "核心流量，维持出价"
        if pd.isna(row["acos"]):
            return "↗ 维持/提价", "+0%", "$0.00", "ACoS 数据缺失，维持出价"
        headroom = (target_acos - row["acos"]) / target_acos
        headroom = max(headroom, 0.0)
        if headroom < 0.10:
            pct, reason = 0,  f"ACoS({row['acos']*100:.1f}%)接近目标，维持出价"
        elif headroom < 0.20:
            pct, reason = 5,  f"ACoS低于目标{headroom*100:.0f}%，小幅提价"
        elif headroom < 0.50:
            pct, reason = 10, f"ACoS低于目标{headroom*100:.0f}%，中幅提价"
        else:
            pct, reason = 15, f"ACoS低于目标{headroom*100:.0f}%，大幅提价"
        cpc = row["cpc"] if pd.notna(row["cpc"]) else 0
        adj_dollar = max(cpc * pct / 100, 0.01) if pct > 0 else 0.0
        action_str = "↗ 提价" if pct > 0 else "↗ 维持"
        return action_str, f"+{pct}%", f"${adj_dollar:.2f}", reason

    if "高ACoS出单" in label:
        if is_core:
            return "🔒 保护", "0%", "$0.00", "核心流量，谨慎调整"
        ratio = row["acos"] / target_acos if pd.notna(row["acos"]) else 1
        if ratio <= 1.2:
            pct, reason = -5, f"ACoS超出目标{(ratio - 1) * 100:.0f}%，小幅降价"
        elif ratio <= 1.5:
            pct, reason = -10, f"ACoS超出目标{(ratio - 1) * 100:.0f}%，中幅降价"
        else:
            pct, reason = -15, f"ACoS严重偏高（{(ratio - 1) * 100:.0f}%），大幅降价"
        cpc = row["cpc"] if pd.notna(row["cpc"]) else 0
        adj_dollar = max(abs(cpc * pct / 100), 0.01)
        return "↘ 降价", f"{pct}%", f"${adj_dollar:.2f}", reason

    if "高点击不出单" in label:
        cpc = row["cpc"] if pd.notna(row["cpc"]) else 0
        adj_dollar = max(cpc * 0.20, 0.01)
        return (
            "⛔ 大幅降价/暂停",
            "-20%",
            f"${adj_dollar:.2f}",
            f'已积累{int(row["clicks"])}次点击仍无出单，转化严重不足',
        )

    if "低点击不出单" in label:
        cpc = row["cpc"] if pd.notna(row["cpc"]) else 0
        adj_dollar = max(cpc * 0.10, 0.01)
        return "↘ 降价", "-10%", f"${adj_dollar:.2f}", "无出单，降低无效花费"

    return "观察", "0%", "$0.00", ""


def layer_summary(agg: pd.DataFrame, label_kw: str) -> tuple:
    sub = agg[agg["label"].str.contains(label_kw)]
    return len(sub), sub["spend"].sum(), sub["sales"].sum(), sub["orders"].sum()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 适配层（新增）：接收 DataFrame，返回 dict 供 FastAPI 序列化
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CATALOG_PATH = Path(__file__).parent.parent.parent / 'data' / 'product_catalog.xlsx'
_CATALOG_TTL = 3600  # 缓存 1 小时

_catalog_df: pd.DataFrame | None = None
_catalog_loaded_at: float = 0.0


def get_asins_for_product(product_target: str) -> list[str]:
    """从 product_target 解析产品代码，查 product_catalog.xlsx 返回 ASIN 列表。"""
    global _catalog_df, _catalog_loaded_at
    if _catalog_df is None or (time.time() - _catalog_loaded_at) > _CATALOG_TTL:
        if not CATALOG_PATH.exists():
            raise FileNotFoundError(f'产品目录不存在: {CATALOG_PATH}')
        _catalog_df = pd.read_excel(CATALOG_PATH)
        _catalog_loaded_at = time.time()
    product_code = product_target.split('_')[0]
    return _catalog_df[_catalog_df['款号'] == product_code]['ASIN'].dropna().tolist()


def run_analysis(
    ap_df: pd.DataFrame,
    tar_df: pd.DataFrame,
    product_target: str,
    asins: list[str],
    target_acos: float,           # 小数，如 0.20
    avg_clicks_per_order: float,
    core_sales_share: float = 0.20,
    analysis_days: int = 0,       # 0 = 使用全部数据；>0 = 取末尾 N 天（原版默认 14）
) -> dict:
    """
    将 v2.py main() 的核心计算逻辑提取为函数，返回结构化 dict。
    DataFrame 由调用方（main.py）从上传文件中读取后传入。
    """
    # ── 预处理：复用 load_frames 中的列清洗 + daily→summary 逻辑
    ap_df  = ap_df.copy()
    tar_df = tar_df.copy()
    ap_df.columns  = [c.strip() for c in ap_df.columns]
    tar_df.columns = [c.strip() for c in tar_df.columns]

    if '日期' in ap_df.columns:
        ap_df['日期'] = pd.to_datetime(ap_df['日期'])
        ap_df = daily_to_summary(ap_df)
    elif 'Date' in ap_df.columns and 'Start Date' not in ap_df.columns:
        # 英文列名 daily 格式
        ap_df = ap_df.rename(columns={'Date': '日期'})
        ap_df['日期'] = pd.to_datetime(ap_df['日期'])
        ap_df = daily_to_summary(ap_df)

    ap_df['Start Date']  = pd.to_datetime(ap_df['Start Date'])
    ap_df['End Date']    = pd.to_datetime(ap_df['End Date'])
    tar_df['Date']       = pd.to_datetime(tar_df['Date'])

    # ── 过滤（复用 main() 中的 ap_filtered → cag_list → tar_filtered）
    ap_filtered = ap_df[ap_df['Advertised ASIN'].isin(asins)]
    if ap_filtered.empty:
        return {'error': f'推广商品报告中未找到 {product_target.split("_")[0]} 的 ASIN'}

    cag_list     = ap_filtered[['Campaign Name', 'Ad Group Name']].drop_duplicates()
    tar_filtered = tar_df.merge(cag_list, on=['Campaign Name', 'Ad Group Name'], how='inner')
    if tar_filtered.empty:
        return {'error': '投放报告中未找到匹配的广告活动，请确认两个文件对应同一账户'}

    # ── 分析窗口（复用 main() 逻辑）
    report_start = tar_filtered['Date'].min()
    report_end   = tar_filtered['Date'].max()

    if analysis_days > 0:
        data_end   = report_end
        data_start = data_end - pd.Timedelta(days=analysis_days - 1)
        tar_window = tar_filtered[tar_filtered['Date'] >= data_start]
    else:
        data_start = report_start
        data_end   = report_end
        tar_window = tar_filtered

    actual_days = (data_end - data_start).days + 1

    # ── Campaign → Portfolio 映射（前端层级树用）
    camp_portfolio: dict[str, str] = {}
    if 'Portfolio name' in tar_window.columns:
        camp_portfolio = (
            tar_window[['Campaign Name', 'Portfolio name']]
            .dropna(subset=['Portfolio name'])
            .drop_duplicates('Campaign Name')
            .set_index('Campaign Name')['Portfolio name']
            .to_dict()
        )

    # ── 聚合（直接复用 main() 的 agg 块）
    agg = (
        tar_window.groupby(TARGETING_KEY)
        .agg(
            impressions=('Impressions', 'sum'),
            clicks=('Clicks', 'sum'),
            orders=('7 Day Total Orders (#)', 'sum'),
            spend=('Spend', 'sum'),
            sales=('7 Day Total Sales', 'sum'),
        )
        .reset_index()
    )

    agg['acos'] = np.where(agg['sales'] > 0, agg['spend'] / agg['sales'], np.nan)
    agg['cvr']  = np.where(agg['clicks'] > 0, agg['orders'] / agg['clicks'], np.nan)
    agg['cpc']  = np.where(agg['clicks'] > 0, agg['spend'] / agg['clicks'], np.nan)

    total_spend  = float(agg['spend'].sum())
    total_sales  = float(agg['sales'].sum())
    total_orders = int(agg['orders'].sum())
    total_clicks = int(agg['clicks'].sum())
    avg_cvr      = total_orders / total_clicks if total_clicks > 0 else 0.0

    if total_sales <= 0:
        return {'error': '窗口内总销售额为 0，无法计算 ACoS，请检查 ASIN 与报告是否匹配'}

    agg['sales_share'] = agg['sales'] / total_sales
    agg['spend_share'] = agg['spend'] / total_spend if total_spend > 0 else 0.0

    # ── 核心流量（复用 main() 的 orders_rank → core_keys）
    orders_rank = agg.sort_values('orders', ascending=False).reset_index(drop=True)
    core_keys   = set(zip(
        orders_rank.head(10)['Campaign Name'],
        orders_rank.head(10)['Ad Group Name'],
        orders_rank.head(10)['Targeting'],
        orders_rank.head(10)['Match Type'],
    ))

    # ── 标签 & 动作（复用 main() 的 get_label / get_action）
    agg['label'] = agg.apply(lambda r: get_label(r, target_acos, avg_clicks_per_order), axis=1)
    actions = agg.apply(
        lambda r: pd.Series(
            get_action(r, target_acos, core_sales_share, core_keys),
            index=['action', 'adj_pct', 'adj_dollar', 'reason'],
        ), axis=1,
    )
    agg = pd.concat([agg, actions], axis=1)

    # is_core 标记（前端层级树用，原版 CSV 不含）
    agg['is_core'] = agg.apply(
        lambda r: (r['Campaign Name'], r['Ad Group Name'],
                   r['Targeting'], r['Match Type']) in core_keys
                  or r['sales_share'] >= core_sales_share,
        axis=1,
    )

    # ── 构建 CSV 列（完全复用 main() 的 out_cols + rename + sort）
    out_cols = TARGETING_KEY + [
        'impressions', 'clicks', 'orders', 'spend', 'sales',
        'acos', 'cvr', 'cpc', 'sales_share', 'spend_share',
        'label', 'action', 'adj_pct', 'adj_dollar', 'reason',
    ]
    csv_df = agg[out_cols].copy()
    csv_df['acos']        = (csv_df['acos'] * 100).round(1)
    csv_df['cvr']         = (csv_df['cvr']  * 100).round(2)
    csv_df['cpc']         = csv_df['cpc'].round(2)
    csv_df['sales_share'] = (csv_df['sales_share'] * 100).round(2)
    csv_df['spend_share'] = (csv_df['spend_share'] * 100).round(2)
    csv_df = csv_df.rename(columns={
        'acos': 'ACoS(%)', 'cvr': 'CVR(%)', 'cpc': 'CPC($)',
        'sales_share': '销售占比(%)', 'spend_share': '花费占比(%)',
    })
    csv_df = csv_df.sort_values('orders', ascending=False)

    # ── 构建 rows（前端渲染用，在 csv_df 基础上附加 category/portfolio/is_core）
    # 用 TARGETING_KEY 建索引，方便快速查 is_core
    agg_indexed = agg.set_index(TARGETING_KEY)

    rows = []
    for _, r in csv_df.iterrows():
        campaign  = r['Campaign Name']
        portfolio = camp_portfolio.get(campaign, '')
        _, category = classify_port(portfolio or campaign)
        key = (campaign, r['Ad Group Name'], r['Targeting'], r['Match Type'])
        try:
            is_core = bool(agg_indexed.at[key, 'is_core'])
        except KeyError:
            is_core = False
        rows.append({
            # CSV 列（与导出完全对应）
            'campaign':    campaign,
            'ad_group':    r['Ad Group Name'],
            'targeting':   r['Targeting'],
            'match_type':  r['Match Type'],
            'impressions': int(r['impressions']),
            'clicks':      int(r['clicks']),
            'orders':      int(r['orders']),
            'spend':       round(float(r['spend']), 2),
            'sales':       round(float(r['sales']), 2),
            'acos':        None if pd.isna(r['ACoS(%)']) else round(float(r['ACoS(%)']), 1),
            'cvr':         None if pd.isna(r['CVR(%)']) else round(float(r['CVR(%)']), 2),
            'cpc':         None if pd.isna(r['CPC($)']) else round(float(r['CPC($)']), 2),
            'sales_share': round(float(r['销售占比(%)']), 2),
            'spend_share': round(float(r['花费占比(%)']), 2),
            'label':       r['label'],
            'action':      r['action'],
            'adj_pct':     r['adj_pct'],
            'adj_dollar':  r['adj_dollar'],
            'reason':      r['reason'],
            # 前端层级树附加字段（不在原版 CSV 中）
            'category':    category,
            'portfolio':   portfolio,
            'is_core':     is_core,
        })

    # ── 分层汇总（复用 layer_summary）
    l_low   = layer_summary(agg, '低ACoS出单')
    l_high  = layer_summary(agg, '高ACoS出单')
    l_hcno  = layer_summary(agg, '高点击不出单')
    l_lcno  = layer_summary(agg, '低点击不出单')
    l_noclk = layer_summary(agg, '无点击')

    overall_acos = total_spend / total_sales

    return {
        'product_target': product_target,
        'report_start':   report_start.strftime('%Y-%m-%d'),
        'report_end':     report_end.strftime('%Y-%m-%d'),
        'summary': {
            'analysis_days': actual_days,
            'date_range':    f'{data_start.date()} → {data_end.date()}',
            'total_spend':   round(total_spend, 2),
            'total_sales':   round(total_sales, 2),
            'overall_acos':  round(overall_acos * 100, 1),
            'target_acos':   round(target_acos * 100, 1),
            'acos_gap':      round((overall_acos - target_acos) * 100, 1),
            'total_orders':  total_orders,
            'total_clicks':  total_clicks,
            'avg_cvr':       round(avg_cvr * 100, 2),
            'n_total':       len(agg),
            'n_effective':   int((agg['orders'] > 0).sum()),
        },
        'label_counts': agg['label'].value_counts().to_dict(),
        'layers': {
            'low_acos':            {'count': l_low[0],   'spend': round(l_low[1], 2),   'sales': round(l_low[2], 2),   'orders': int(l_low[3])},
            'high_acos':           {'count': l_high[0],  'spend': round(l_high[1], 2),  'sales': round(l_high[2], 2),  'orders': int(l_high[3])},
            'high_click_no_order': {'count': l_hcno[0],  'spend': round(l_hcno[1], 2),  'sales': round(l_hcno[2], 2),  'orders': int(l_hcno[3])},
            'low_click_no_order':  {'count': l_lcno[0],  'spend': round(l_lcno[1], 2),  'sales': round(l_lcno[2], 2),  'orders': int(l_lcno[3])},
            'no_click':            {'count': l_noclk[0], 'spend': round(l_noclk[1], 2), 'sales': round(l_noclk[2], 2), 'orders': int(l_noclk[3])},
        },
        'rows': rows,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 多轮分析（三个日历周窗口 + 投票 consensus）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_ADJ_LABELS = {'⚠️ 高ACoS出单', '🔴 高点击不出单'}


def _hit_desc(hit_rounds_str: str) -> str:
    """根据命中轮次字符串（如 'R1,R3,R5,R6'）生成可读的 reason 文案。"""
    if not hit_rounds_str:
        return ''
    rounds = [r.strip() for r in hit_rounds_str.split(',') if r.strip()]
    nums = sorted(int(r[1:]) for r in rounds if r.startswith('R') and r[1:].isdigit())
    n = len(nums)
    if n == 0:
        return ''
    has_r1   = 1 in nums
    is_consec = all(nums[i] == nums[i - 1] + 1 for i in range(1, len(nums)))
    if n >= 5:
        desc = '长期持续异常，优先处理'
    elif has_r1 and is_consec and n >= 3:
        desc = '近期持续异常，建议尽快处理'
    elif has_r1 and n >= 3:
        desc = '近期复发，历史有前例'
    elif has_r1:
        desc = '近期出现，观察是否持续'
    else:
        desc = '早期问题，近期已有改善迹象'
    return f'命中{n}轮 · {desc}'


def _parse_adj_pct(s) -> float | None:
    """'-10%' → -0.10，'+5%' → +0.05，'+0~5%' / '+0%' / None → None"""
    if s is None:
        return None
    s = str(s).strip()
    if '~' in s or s in ('', '0%', '+0%'):
        return None
    try:
        return float(s.replace('%', '').replace('+', '')) / 100
    except ValueError:
        return None


def _filter_by_week_range(
    tar_df: pd.DataFrame,
    ap_df:  pd.DataFrame,
    from_sunday: pd.Timestamp,
    to_sunday:   pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """截取 [from_sunday, to_sunday+6天] 范围内的行"""
    end = to_sunday + pd.Timedelta(days=6, hours=23, minutes=59, seconds=59)
    t = tar_df[tar_df['Date'].between(from_sunday, end)].copy()
    a = ap_df[ap_df['日期'].between(from_sunday, end)].copy()
    return t, a


def run_multi_round_analysis(
    tar_df:               pd.DataFrame,
    ap_df:                pd.DataFrame,
    week_sundays:         list,           # 升序 Timestamp 列表，最多 6 个
    product_target:       str,
    asins:                list[str],
    target_acos:          float,
    avg_clicks_per_order: float,
    core_sales_share:     float = 0.20,
) -> dict:
    """
    三轮多窗口分析 + 投票 consensus。

    轮次（日历周，周日开始）：
      R1 — 最近 1 周（week_sundays[-1]）
      R2 — 最近 3 周（week_sundays[-3:]）
      R3 — 全部（week_sundays[0]，最多 6 周）

    投票：targeting 三元组在 ≥2 轮中被打上调价标签 → 进入 consensus
          adj_pct 取各命中轮中绝对值最小（最保守）的

    consensus rows 字段：
      同 run_analysis() rows，metrics 来自 R3（最全），
      label 来自 R1（最新），adj_pct 取最保守值，
      额外字段 hit_rounds（如 'R1,R2,R3'）
    """
    n = len(week_sundays)
    to_sunday = week_sundays[-1]

    # 三轮起始周
    r_from = {
        'R1': week_sundays[-1],
        'R2': week_sundays[max(-3, -n)],
        'R3': week_sundays[0],
    }

    rounds: dict[str, dict] = {}
    for rname, from_sunday in r_from.items():
        t, a = _filter_by_week_range(tar_df, ap_df, from_sunday, to_sunday)
        week_end = to_sunday + pd.Timedelta(days=6)
        date_label = (
            f'{from_sunday.strftime("%m/%d")}-{week_end.strftime("%m/%d")}'
        )
        if t.empty or a.empty:
            rounds[rname] = {
                'rows': [], 'summary': {}, 'layers': {},
                'date_start': from_sunday.strftime('%Y-%m-%d'),
                'date_end':   week_end.strftime('%Y-%m-%d'),
                'week_label': date_label,
                'error': '该时间窗口内数据为空',
            }
            continue

        res = run_analysis(
            ap_df                = a,
            tar_df               = t,
            product_target       = product_target,
            asins                = asins,
            target_acos          = target_acos,
            avg_clicks_per_order = avg_clicks_per_order,
            core_sales_share     = core_sales_share,
            analysis_days        = 0,
        )
        rounds[rname] = {
            'rows':         res.get('rows', []),
            'summary':      res.get('summary', {}),
            'layers':       res.get('layers', {}),
            'label_counts': res.get('label_counts', {}),
            'date_start':   from_sunday.strftime('%Y-%m-%d'),
            'date_end':     week_end.strftime('%Y-%m-%d'),
            'week_label':   date_label,
            'error':        res.get('error'),
        }

    # ── 投票 consensus ──────────────────────────────────────────────────────────
    # key → {rname: adj_pct_decimal}
    vote: dict[tuple, dict] = {}
    r3_row_map: dict[tuple, dict] = {}
    r1_row_map: dict[tuple, dict] = {}

    for rname in ('R3', 'R2', 'R1'):
        for row in rounds[rname].get('rows', []):
            if row.get('label') not in _ADJ_LABELS:
                continue
            adj_dec = _parse_adj_pct(row.get('adj_pct'))
            if adj_dec is None:
                continue
            key = (row.get('campaign'), row.get('ad_group'), row.get('targeting'))
            if key not in vote:
                vote[key] = {}
            vote[key][rname] = adj_dec
            if rname == 'R3':
                r3_row_map[key] = row
            if rname == 'R1':
                r1_row_map[key] = row

    consensus_rows: list[dict] = []
    for key, hit_map in vote.items():
        if len(hit_map) < 2:
            continue

        # 唱票：票数最多的值；平票取绝对值最小（最保守）
        _vote_ctr = Counter(hit_map.values())
        _max_votes = max(_vote_ctr.values())
        best_adj = min(
            (v for v, c in _vote_ctr.items() if c == _max_votes),
            key=abs
        )
        hit_rounds_str = ','.join(sorted(hit_map.keys()))

        # metrics 取 R3，label 取 R1（最新状态），fallback 取任意命中轮
        base = r3_row_map.get(key) or r1_row_map.get(key) or next(
            (rounds[r]['rows'] for r in hit_map if rounds[r].get('rows')), [{}]
        )
        if isinstance(base, list):
            base = next((ro for ro in base
                         if (ro.get('campaign'), ro.get('ad_group'), ro.get('targeting')) == key),
                        {})
        row = dict(base)
        row['label']       = (r1_row_map.get(key) or base).get('label', base.get('label', ''))
        row['adj_pct']     = f'{int(best_adj * 100)}%'
        row['action']      = '↘ 降价'
        row['reason']      = _hit_desc(hit_rounds_str)
        row['hit_rounds']  = hit_rounds_str
        consensus_rows.append(row)

    r3 = rounds.get('R3', {})
    return {
        'R1':      rounds.get('R1', {}),
        'R2':      rounds.get('R2', {}),
        'R3':      r3,
        'consensus': {
            'rows':       consensus_rows,
            'count':      len(consensus_rows),
        },
        'product_target': product_target,
        'report_start':   r3.get('date_start', ''),
        'report_end':     r3.get('date_end',   ''),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 六轮分析（R1=1w … R6=6w，每轮递增一周 + 向好趋势排除）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_improving_6r(hit_set: set[str]) -> bool:
    """
    判断是否为向好趋势（不应降价）。
    - =3/6 命中且仅 {R4,R5,R6}：近 3 周干净，明显好转
    ≥5/6 命中时无条件返回 False（信号足够强）。
    """
    if len(hit_set) == 3 and hit_set == {'R4', 'R5', 'R6'}:
        return True
    return False


def run_multi_round_analysis_6r(
    tar_df:               pd.DataFrame,
    ap_df:                pd.DataFrame,
    week_sundays:         list,           # 升序 Timestamp 列表，最多 6 个
    product_target:       str,
    asins:                list[str],
    target_acos:          float,
    avg_clicks_per_order: float,
    core_sales_share:     float = 0.20,
    up_orders_threshold:  int   = 2,      # 提价 consensus 入围最低订单数
) -> dict:
    """
    六轮多窗口分析 + 投票 consensus（含向好趋势排除）。

    轮次（日历周，周日开始）：
      R1 — 最近 1 周  R2 — 最近 2 周  R3 — 最近 3 周
      R4 — 最近 4 周  R5 — 最近 5 周  R6 — 全部（最多 6 周）

    Consensus 规则：
      - ≥3/6 轮命中 → 候选
      - =3/6 且仅 {R4,R5,R6} 命中 → 排除（向好）
      - adj_pct 取各命中轮绝对值最小（最保守）
      - metrics 来自 R6，label 来自 R1
    """
    n = len(week_sundays)
    to_sunday = week_sundays[-1]

    # 六轮起始周（数据不足时退化到 week_sundays[0]，不报错）
    r_from = {
        'R1': week_sundays[-1],
        'R2': week_sundays[max(-2, -n)],
        'R3': week_sundays[max(-3, -n)],
        'R4': week_sundays[max(-4, -n)],
        'R5': week_sundays[max(-5, -n)],
        'R6': week_sundays[0],
    }

    # 去重：跳过与更长轮次（R6>R5>...）重叠的起始周，避免投票膨胀
    _seen_starts: set = set()
    _skip_rounds: set[str] = set()
    for rname in ('R6', 'R5', 'R4', 'R3', 'R2', 'R1'):
        fs = r_from[rname]
        if fs in _seen_starts:
            _skip_rounds.add(rname)
        else:
            _seen_starts.add(fs)

    rounds: dict[str, dict] = {}
    for rname, from_sunday in r_from.items():
        if rname in _skip_rounds:
            week_end = to_sunday + pd.Timedelta(days=6)
            rounds[rname] = {
                'rows': [], 'summary': {}, 'layers': {},
                'date_start': from_sunday.strftime('%Y-%m-%d'),
                'date_end':   week_end.strftime('%Y-%m-%d'),
                'week_label': f'{from_sunday.strftime("%m/%d")}-{week_end.strftime("%m/%d")}',
                'error': '与其他轮次时间窗口重叠（数据不足），已跳过',
            }
            continue
        t, a = _filter_by_week_range(tar_df, ap_df, from_sunday, to_sunday)
        week_end = to_sunday + pd.Timedelta(days=6)
        date_label = f'{from_sunday.strftime("%m/%d")}-{week_end.strftime("%m/%d")}'
        if t.empty or a.empty:
            rounds[rname] = {
                'rows': [], 'summary': {}, 'layers': {},
                'date_start': from_sunday.strftime('%Y-%m-%d'),
                'date_end':   week_end.strftime('%Y-%m-%d'),
                'week_label': date_label,
                'error': '该时间窗口内数据为空',
            }
            continue
        res = run_analysis(
            ap_df                = a,
            tar_df               = t,
            product_target       = product_target,
            asins                = asins,
            target_acos          = target_acos,
            avg_clicks_per_order = avg_clicks_per_order,
            core_sales_share     = core_sales_share,
            analysis_days        = 0,
        )
        rounds[rname] = {
            'rows':         res.get('rows', []),
            'summary':      res.get('summary', {}),
            'layers':       res.get('layers', {}),
            'label_counts': res.get('label_counts', {}),
            'date_start':   from_sunday.strftime('%Y-%m-%d'),
            'date_end':     week_end.strftime('%Y-%m-%d'),
            'week_label':   date_label,
            'error':        res.get('error'),
        }

    # ── 投票 consensus ──────────────────────────────────────────────────────────
    # vote[key][rname] = (adj_dec|None, raw_str)
    # 只要标签命中即计票，adj_pct=0% / None 不影响投票计数，仅影响降幅计算
    vote: dict[tuple, dict] = {}          # key → {rname: (adj_dec|None, raw_str)}
    r6_row_map: dict[tuple, dict] = {}    # metrics 来源
    r1_row_map: dict[tuple, dict] = {}    # label 来源

    for rname in ('R6', 'R5', 'R4', 'R3', 'R2', 'R1'):
        for row in rounds[rname].get('rows', []):
            if row.get('label') not in _ADJ_LABELS:
                continue
            adj_raw = str(row.get('adj_pct') or '').strip()
            adj_dec = _parse_adj_pct(adj_raw)
            action_raw = row.get('action', '')
            key = (row.get('campaign'), row.get('ad_group'), row.get('targeting'))
            if key not in vote:
                vote[key] = {}
            vote[key][rname] = (adj_dec, adj_raw or '0%', action_raw)
            if rname == 'R6':
                r6_row_map[key] = row
            if rname == 'R1':
                r1_row_map[key] = row

    consensus_rows: list[dict] = []
    for key, hit_map in vote.items():
        hit_set = set(hit_map.keys())

        # 门槛：≥3/6
        if len(hit_set) < 3:
            continue

        # 排除向好趋势
        if _is_improving_6r(hit_set):
            continue

        # 最保守 adj_pct：仅从非 None（有效降幅）的轮次中取，同时记录来源轮的 action
        valid_adjs = [
            (rn, dec, raw, act)
            for rn, (dec, raw, act) in hit_map.items()
            if dec is not None
        ]
        if valid_adjs:
            # 唱票：按 adj_pct 整数值（避免浮点噪声）统计票数
            _dec_ctr = Counter(round(dec * 100) for _, dec, _, _ in valid_adjs)
            _max_v   = max(_dec_ctr.values())
            # 票数最多的值（平票取绝对值最小）
            best_dec_int = min(
                (d for d, c in _dec_ctr.items() if c == _max_v),
                key=abs
            )
            # 从 valid_adjs 中找对应轮次（取第一个匹配）
            best_rn, best_adj_dec, _, best_action = next(
                (x for x in valid_adjs if round(x[1] * 100) == best_dec_int),
                valid_adjs[0]
            )
            best_adj_str = f'{int(best_adj_dec * 100)}%'
        else:
            # 所有轮次 adj_pct 均为 0%：取任意命中轮的 action，保持 0%
            any_rn = next(iter(sorted(hit_map.keys())))
            _, _, best_action = hit_map[any_rn]
            best_adj_str = '0%'

        hit_rounds_str = ','.join(sorted(hit_map.keys()))
        # 各轮 adj_pct 明细，格式：R1:-10%,R2:0%,...
        hit_rounds_adj = ','.join(
            f'{r}:{hit_map[r][1]}' for r in sorted(hit_map.keys())
        )

        # metrics 取 R6，label 取 R1，fallback 任意命中轮
        base = r6_row_map.get(key) or r1_row_map.get(key) or next(
            (rounds[r]['rows'] for r in hit_set if rounds[r].get('rows')), [{}]
        )
        if isinstance(base, list):
            base = next(
                (ro for ro in base
                 if (ro.get('campaign'), ro.get('ad_group'), ro.get('targeting')) == key),
                {}
            )
        row = dict(base)
        row['label']          = (r1_row_map.get(key) or base).get('label', base.get('label', ''))
        row['adj_pct']        = best_adj_str
        row['action']         = best_action
        row['reason']         = _hit_desc(hit_rounds_str)
        row['hit_rounds']     = hit_rounds_str
        row['hit_rounds_adj'] = hit_rounds_adj
        consensus_rows.append(row)

    # ── 提价 consensus ──────────────────────────────────────────────────────────
    _UP_LABEL = '✅ 低ACoS出单'
    vote_up: dict[tuple, dict] = {}        # key → {rname: (adj_dec, adj_raw)}
    r3_up_row_map: dict[tuple, dict] = {}  # metrics 来源（取 R3）
    r1_up_row_map: dict[tuple, dict] = {}  # label/action 来源（取 R1）

    for rname in ('R6', 'R5', 'R4', 'R3', 'R2', 'R1'):
        for row in rounds[rname].get('rows', []):
            if row.get('label') != _UP_LABEL:
                continue
            if (row.get('orders') or 0) < up_orders_threshold:
                continue
            adj_raw = str(row.get('adj_pct') or '').strip()
            adj_dec = _parse_adj_pct(adj_raw)
            if adj_dec is None:
                continue   # +0% / 数据缺失 → 跳过（不计票）
            key = (row.get('campaign'), row.get('ad_group'), row.get('targeting'))
            if key not in vote_up:
                vote_up[key] = {}
            vote_up[key][rname] = (adj_dec, adj_raw)
            if rname == 'R3':
                r3_up_row_map[key] = row
            if rname == 'R1':
                r1_up_row_map[key] = row

    consensus_up_rows: list[dict] = []
    for key, hit_map in vote_up.items():
        hit_set = set(hit_map.keys())
        # 门槛：≥3/6 且 R3 必须命中
        if len(hit_set) < 3 or 'R3' not in hit_set:
            continue
        # 唱票：票数最多的值；平票取最小（最保守）
        _up_ctr = Counter(round(v[0] * 100) for v in hit_map.values())
        _up_max = max(_up_ctr.values())
        best_adj_int = min(
            (d for d, c in _up_ctr.items() if c == _up_max)
        )
        best_adj = best_adj_int / 100
        best_str = f'+{best_adj_int}%' if best_adj > 0 else '0%'
        hit_rounds_str = ','.join(sorted(hit_map.keys()))
        hit_rounds_adj = ','.join(
            f'{r}:{hit_map[r][1]}' for r in sorted(hit_map.keys())
        )
        # metrics 取 R3，fallback R1
        base = r3_up_row_map.get(key) or r1_up_row_map.get(key) or {}
        row_out = dict(base)
        row_out['adj_pct']        = best_str
        row_out['action']         = '↗ 提价' if best_adj > 0 else '↗ 维持'
        row_out['reason']         = _hit_desc(hit_rounds_str)
        row_out['hit_rounds']     = hit_rounds_str
        row_out['hit_rounds_adj'] = hit_rounds_adj
        consensus_up_rows.append(row_out)

    r6 = rounds.get('R6', {})
    return {
        'R1': rounds.get('R1', {}),
        'R2': rounds.get('R2', {}),
        'R3': rounds.get('R3', {}),
        'R4': rounds.get('R4', {}),
        'R5': rounds.get('R5', {}),
        'R6': r6,
        'consensus':    {'rows': consensus_rows,    'count': len(consensus_rows)},
        'consensus_up': {'rows': consensus_up_rows, 'count': len(consensus_up_rows)},
        'product_target': product_target,
        'report_start':   r6.get('date_start', ''),
        'report_end':     r6.get('date_end',   ''),
    }
