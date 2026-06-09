"""
bulk_update.py — Mode 1 竞价更新

直接复制 amazon_toolkit/tools/ad_bulk_update.py 的核心函数（load_label_df、
build_bulk_index、process_updates、save_bulk_updated、save_label_updated），
仅做最小 I/O 适配：文件路径 → 内存 bytes/DataFrame。

唯一必要的衔接处理：
  run_analysis() 返回的 rows 中 adj_pct 为 "-5%" 字符串，
  而 process_updates 使用 pd.to_numeric 解析，需在调用前转为小数（-0.05）。

返回：(bulk_out_bytes, label_csv_bytes, log_lines, details)
"""

from __future__ import annotations

import gc
import io
import re
import warnings
from datetime import datetime

import pandas as pd

warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')
from openpyxl import load_workbook
from openpyxl.styles import PatternFill

# process_updates / build_bulk_index 实际用到的 Bulk 列（约 10 列）
# 过滤掉其余 20+ 列，大幅减少 pandas DataFrame 内存占用
_BULK_NEEDED_COLS: frozenset[str] = frozenset({
    'Entity',
    'Campaign Name (Informational only)',
    'Ad Group Name (Informational only)',
    'Product Targeting Expression',
    'Keyword Text',
    'Bidding Strategy',
    'Placement',
    'Percentage',
    'Bid',
    'Operation',
})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 以下函数直接复制自 amazon_toolkit/tools/ad_bulk_update.py，未做任何修改
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_label_df(df: pd.DataFrame, orders_threshold: int = 10):
    """
    原版：读取竞价更新指导文件，筛选需处理的行。
    适配：接收已读入的 DataFrame，而非文件路径；orders 阈值可配置（原版硬编码 < 10）。
    筛选条件：O列label含"高ACoS出单"或"高点击不出单"，且G列orders < orders_threshold
    返回 (筛选后df, 完整df)
    """
    df = df.copy()
    df.columns = df.columns.str.strip()

    # G列 orders 转数值
    df['orders'] = pd.to_numeric(df['orders'], errors='coerce').fillna(0)

    # O列 label 筛选（含emoji前缀，用关键词匹配）
    label_col = df['label'].fillna('')
    mask_label = label_col.str.contains('高ACoS出单|高点击不出单', na=False)

    # G列 orders < orders_threshold
    mask_order = df['orders'] < orders_threshold

    filtered = df[mask_label & mask_order].copy()
    return filtered, df


def build_campaign_meta_index(df_bulk: pd.DataFrame) -> dict:
    """
    从 Bulk 表建立广告活动元数据索引，供 process_updates 填充明细字段。
    key   = Campaign Name (Informational only)
    value = {
        'strategy':     出价策略（如 'Dynamic bids - down only'）,
        'top':          Placement Top 调整百分比（float 或 None）,
        'rest':         Placement Rest Of Search 调整百分比（float 或 None）,
        'product_page': Placement Product Page 调整百分比（float 或 None）,
    }
    """
    meta: dict = {}

    for _, row in df_bulk.iterrows():
        entity = str(row.get('Entity', '')).strip()
        camp   = str(row.get('Campaign Name (Informational only)', '')).strip()
        if not camp:
            continue

        if camp not in meta:
            meta[camp] = {'strategy': '', 'top': None, 'rest': None, 'product_page': None}

        if entity == 'Campaign':
            strategy = str(row.get('Bidding Strategy', '')).strip()
            if strategy:
                meta[camp]['strategy'] = strategy

        elif entity == 'Bidding Adjustment':
            placement = str(row.get('Placement', '')).strip()
            pct = pd.to_numeric(row.get('Percentage'), errors='coerce')
            val = float(pct) if not pd.isna(pct) else None
            if placement == 'Placement Top':
                meta[camp]['top'] = val
            elif placement == 'Placement Rest Of Search':
                meta[camp]['rest'] = val
            elif placement == 'Placement Product Page':
                meta[camp]['product_page'] = val

    return meta


def build_bulk_index(df_bulk: pd.DataFrame, label_type: str) -> dict:
    """
    从 Bulk SP Campaigns 表建立三元组查找索引。
    key = (Campaign Name, Ad Group Name, Targeting文本)
    value = df_bulk 的行索引（0-based）

    ASIN → Entity='Product Targeting'，Targeting列='Product Targeting Expression'
    KW   → Entity='Keyword'，          Targeting列='Keyword Text'
    BOTH → 同时索引以上两种实体
    """
    if label_type == 'BOTH':
        type_pairs = [
            ('Product Targeting', 'Product Targeting Expression'),
            ('Keyword',           'Keyword Text'),
        ]
    elif label_type == 'ASIN':
        type_pairs = [('Product Targeting', 'Product Targeting Expression')]
    else:
        type_pairs = [('Keyword', 'Keyword Text')]

    index_map = {}
    for entity_filter, targeting_col in type_pairs:
        for i, row in df_bulk.iterrows():
            if str(row.get('Entity', '')).strip() != entity_filter:
                continue
            key = (
                str(row.get('Campaign Name (Informational only)', '')).strip(),
                str(row.get('Ad Group Name (Informational only)', '')).strip(),
                str(row.get(targeting_col, '')).strip(),
            )
            if key not in index_map:
                index_map[key] = i
    return index_map


def process_updates(df_label_filtered, df_label_full, df_bulk, index_map, label_type, log,
                    camp_meta: dict = None):
    """
    遍历筛选出的竞价指导行，在 Bulk 中找匹配并更新竞价。
    - 更新 df_bulk 的 Bid 列
    - 回写 df_label_full 的 原竞价/新竞价/操作日期 列
    camp_meta: build_campaign_meta_index() 返回的索引，用于填充 strategy/placement 明细
    返回 (df_label_full, df_bulk, 被更新的bulk行索引列表, 结构化明细列表)
    """
    if camp_meta is None:
        camp_meta = {}
    updated_bulk_rows = []
    details = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for label_idx, label_row in df_label_filtered.iterrows():
        camp  = str(label_row.get('Campaign Name', '')).strip()
        adgrp = str(label_row.get('Ad Group Name', '')).strip()
        tgt   = str(label_row.get('Targeting', '')).strip()
        key   = (camp, adgrp, tgt)

        # ── 找匹配行 ──
        if key not in index_map:
            log.append(f'[未匹配] {label_type} | {camp} | {adgrp} | {tgt}')
            continue

        bulk_idx = index_map[key]

        # ── 读原竞价 ──
        old_bid = pd.to_numeric(df_bulk.at[bulk_idx, 'Bid'], errors='coerce')
        if pd.isna(old_bid):
            log.append(f'[跳过-Bid为空] {label_type} | {camp} | {adgrp} | {tgt}')
            continue

        # ── 读 adj_pct ──
        adj_pct = pd.to_numeric(label_row.get('adj_pct'), errors='coerce')
        if pd.isna(adj_pct):
            log.append(f'[跳过-adj_pct为空] {label_type} | {camp} | {adgrp} | {tgt}')
            continue

        new_bid = round(float(old_bid) * (1 + float(adj_pct)), 4)

        # ── 更新 Bulk（df 以 dtype=str 读入，写回须转 str）──
        df_bulk.at[bulk_idx, 'Bid'] = str(new_bid)
        # 修改了 Bid 的行，C列 Operation 同步标记为 Update
        df_bulk.at[bulk_idx, 'Operation'] = 'Update'
        updated_bulk_rows.append(bulk_idx)

        # ── 回写竞价指导（同样 dtype=str，用 str 写入；openpyxl 写文件时再用 float）──
        df_label_full.at[label_idx, '原竞价']  = str(float(old_bid))
        df_label_full.at[label_idx, '新竞价']  = str(new_bid)
        df_label_full.at[label_idx, '操作日期'] = now_str

        log.append(
            f'[已更新] {label_type} | {camp} | {adgrp} | {tgt} '
            f'| {old_bid} → {new_bid} (adj={adj_pct:+.0%})'
        )

        # ── 收集结构化明细（每条投放一行，含活动配置快照）──
        cm = camp_meta.get(camp, {})
        details.append({
            'campaign':     camp,
            'ad_group':     adgrp,
            'strategy':     cm.get('strategy', ''),
            'top':          cm.get('top'),
            'rest':         cm.get('rest'),
            'product_page': cm.get('product_page'),
            'label':        str(label_row.get('label', '')).strip(),
            'targeting':    tgt,
            'old_bid':      float(old_bid),
            'new_bid':      new_bid,
            'adj_pct':      float(adj_pct),
            'datetime':     now_str,
        })

    return df_label_full, df_bulk, updated_bulk_rows, details


def save_bulk_updated(bulk_bytes_or_wb, df_bulk: pd.DataFrame, updated_rows: list) -> bytes:
    """
    原版：复制原文件，用 openpyxl 只写改动行的 Bid/Operation，黄色高亮，删除 RAS 表。
    适配：bulk_bytes_or_wb 可以是 bytes（向后兼容）或已加载的 Workbook（节省内存）。
    """
    if isinstance(bulk_bytes_or_wb, bytes):
        wb = load_workbook(io.BytesIO(bulk_bytes_or_wb))
    else:
        wb = bulk_bytes_or_wb  # 直接使用已加载的 Workbook，无需重复读取
    ws = wb['Sponsored Products Campaigns']

    # 找 Bid 和 Operation 列（1-based）
    header_row = [cell.value for cell in ws[1]]
    try:
        bid_col_excel = header_row.index('Bid') + 1
    except ValueError:
        bid_col_excel = 28  # 默认 AB 列
    try:
        op_col_excel = header_row.index('Operation') + 1
    except ValueError:
        op_col_excel = 3   # 默认 C 列

    yellow_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')

    # 只写被修改行的 Bid 和 Operation 单元格，其余行保持原文件不动
    for df_idx in updated_rows:
        excel_row = df_idx + 2  # +1 表头行, +1 转 1-based
        # Bid
        bid_val = df_bulk.at[df_idx, 'Bid']
        cell = ws.cell(row=excel_row, column=bid_col_excel)
        try:
            cell.value = float(bid_val)
        except (ValueError, TypeError):
            cell.value = bid_val
        cell.fill = yellow_fill
        # Operation = Update
        ws.cell(row=excel_row, column=op_col_excel).value = 'Update'

    # 删除 RAS Search Term Report
    if 'RAS Search Term Report' in wb.sheetnames:
        del wb['RAS Search Term Report']

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out.read()


def save_label_updated(df_label_full: pd.DataFrame) -> bytes:
    """
    原版：将 原竞价/新竞价/操作日期 写入竞价指导文件副本（xlsx）。
    适配：输出为 CSV bytes（原版写 xlsx 文件，我们的输入来自内存 DataFrame）。
    列顺序与 v2.py out_cols 完全一致，末尾追加三列。
    """
    CSV_COLS = [
        'Campaign Name', 'Ad Group Name', 'Targeting', 'Match Type',
        'impressions', 'clicks', 'orders', 'spend', 'sales',
        'ACoS(%)', 'CVR(%)', 'CPC($)', '销售占比(%)', '花费占比(%)',
        'label', 'action', 'adj_pct', 'adj_dollar', 'reason',
        'hit_rounds', 'hit_rounds_adj',
        '原竞价', '新竞价', '操作日期', 'history',
    ]
    out_cols = [c for c in CSV_COLS if c in df_label_full.columns]
    df_out = df_label_full[out_cols]

    buf = io.StringIO()
    df_out.to_csv(buf, index=False)
    return ('﻿' + buf.getvalue()).encode('utf-8')  # BOM for Excel 中文兼容


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 适配层：接收 rows(list[dict]) + bulk_bytes，返回两个文件的 bytes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# rows dict key → v2.py CSV 列名（与 load_label_df 期望的列名一致）
_ROW_COL_RENAME = {
    'campaign':    'Campaign Name',
    'ad_group':    'Ad Group Name',
    'targeting':   'Targeting',
    'match_type':  'Match Type',
    'impressions': 'impressions',
    'clicks':      'clicks',
    'orders':      'orders',
    'spend':       'spend',
    'sales':       'sales',
    'acos':        'ACoS(%)',
    'cvr':         'CVR(%)',
    'cpc':         'CPC($)',
    'sales_share': '销售占比(%)',
    'spend_share': '花费占比(%)',
    'label':       'label',
    'action':      'action',
    'adj_pct':     'adj_pct',
    'adj_dollar':  'adj_dollar',
    'reason':      'reason',
}


def _pct_str_to_decimal(val) -> object:
    """
    将 v2.py 输出的 adj_pct 字符串（如 "-10%", "+0~5%", "0%"）转为小数（-0.10, NaN, 0.0）。
    process_updates 使用 pd.to_numeric 解析 adj_pct，需要小数格式才能正常工作。
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return float('nan')
    s = str(val).strip()
    if '~' in s:          # "+0~5%" → 不确定，跳过
        return float('nan')
    s = s.replace('%', '').replace('+', '')
    try:
        return float(s) / 100
    except ValueError:
        return float('nan')


def _fmt_history(
    campaign:     str,
    ad_group:     str,
    targeting:    str,
    history_map:  dict,
    guard_days:   int,
) -> str:
    """
    为单条 targeting 行生成 history 列字符串。
    无记录 → ''；有记录 → '{confirmed_at} | {days:.2f}天前 | {调价/不调价}'
    """
    key = (
        str(campaign).strip(),
        str(ad_group).strip(),
        str(targeting).strip(),
    )
    h = history_map.get(key)
    if not h:
        return ''
    days = h.get('days_ago')
    if days is None:
        return ''
    result = '不调价' if days <= guard_days else '调价'
    return f"{h['confirmed_at']} | {days:.2f}天前 | {result}"


def apply_mode1_to_bulk(
    bulk_bytes: bytes,
    rows: list[dict],
    product_target: str = '',
    report_start: str = '',
    report_end: str = '',
    orders_threshold: int = 10,
    up_orders_threshold: int = 2,
    history_map: dict | None = None,
    history_guard_days: int = 21,
) -> tuple[bytes, bytes, list[str], list]:
    """
    将 Mode 1 分析结果写回 Amazon Bulk 文件，同时生成含 原竞价/新竞价/操作日期 的
    targeting_labels CSV。

    筛选条件：
      降价：label 含 "高ACoS出单" 或 "高点击不出单"，且 orders < orders_threshold
      提价：label 含 "低ACoS出单"，orders >= up_orders_threshold，adj_pct > 0

    Returns
    -------
    (bulk_out_bytes, label_csv_bytes, log_lines, details)
    """
    log: list[str] = []

    # ── 1. rows → DataFrame，列名对齐为 v2.py CSV 格式 ───────────────────────
    df_label = pd.DataFrame(rows).rename(columns=_ROW_COL_RENAME)

    # 衔接：将 adj_pct 从 "-10%"/"+5%" 字符串转为小数（process_updates 依赖此格式）
    df_label['adj_pct'] = df_label['adj_pct'].apply(_pct_str_to_decimal)

    # 预留三列（初始化为空字符串）
    df_label['原竞价']  = ''
    df_label['新竞价']  = ''
    df_label['操作日期'] = ''

    # ── 预计算三元组 key 列（供 Step A/C 共用）──────────────────────────────────
    df_label['_key'] = list(zip(
        df_label['Campaign Name'].str.strip(),
        df_label['Ad Group Name'].str.strip(),
        df_label['Targeting'].str.strip(),
    ))

    # ── Step A: 填充 history 列 ────────────────────────────────────────────
    if history_map:
        df_label['history'] = df_label['_key'].apply(
            lambda key: _fmt_history(*key, history_map, history_guard_days)
        )
    else:
        df_label['history'] = ''

    # ── 2. 降价筛选 ───────────────────────────────────────────────────────────
    df_filtered, df_full = load_label_df(df_label, orders_threshold=orders_threshold)
    log.append(
        f'降价筛选: {len(df_filtered)}/{len(df_full)} 行符合条件'
        f'（高ACoS出单/高点击不出单 且 orders<{orders_threshold}）'
    )

    # ── 2b. 提价筛选 ──────────────────────────────────────────────────────────
    _mask_up_label  = df_full['label'].fillna('').str.contains('低ACoS出单', na=False)
    _mask_up_orders = df_full['orders'] >= up_orders_threshold
    _adj_vals       = pd.to_numeric(df_full['adj_pct'], errors='coerce').fillna(0)
    _mask_up_adj    = _adj_vals > 0
    df_up_filtered  = df_full[_mask_up_label & _mask_up_orders & _mask_up_adj].copy()
    log.append(
        f'提价筛选: {len(df_up_filtered)}/{len(df_full)} 行符合条件'
        f'（低ACoS出单 且 orders≥{up_orders_threshold} 且 adj_pct>0）'
    )

    # ── Step B: 构建受保护 targeting 集合 ─────────────────────────────────
    protected_keys: set = set()
    if history_map:
        for (camp, adgrp, tgt), h in history_map.items():
            if h['days_ago'] <= history_guard_days:
                protected_keys.add((camp, adgrp, tgt))
        if protected_keys:
            log.append(
                f'[历史保护] {len(protected_keys)} 条 targeting 在 '
                f'{history_guard_days} 天内已调价，已跳过'
            )

    # ── Step C: 从降价/提价筛选结果中剔除受保护行 ────────────────────────
    if protected_keys:
        df_filtered    = df_filtered[
            ~df_filtered['_key'].isin(protected_keys)
        ]
        df_up_filtered = df_up_filtered[
            ~df_up_filtered['_key'].isin(protected_keys)
        ]

    if df_filtered.empty and df_up_filtered.empty:
        log.insert(0, '共更新 0 条 | 筛选结果为空，无需处理')
        empty_csv = save_label_updated(df_full)
        return bulk_bytes, empty_csv, log, []

    # ── 3. 读取 Bulk（单次加载：openpyxl → DataFrame，峰值约 2x 文件大小）────
    # ★★ 只调用一次 load_workbook，避免 pd.read_excel 内部再次加载同一文件
    _wb_preloaded = load_workbook(io.BytesIO(bulk_bytes))
    del bulk_bytes
    gc.collect()

    _ws = _wb_preloaded['Sponsored Products Campaigns']
    _rows = list(_ws.values)
    _headers = [str(h).strip() if h is not None else '' for h in _rows[0]]
    df_bulk = pd.DataFrame(_rows[1:], columns=_headers)
    del _rows
    # ★ 仅保留必要列（约 10 列 vs 原始 30+ 列），减少 pandas 内存占用约 60-70%
    _keep = [c for c in df_bulk.columns if c in _BULK_NEEDED_COLS]
    df_bulk = df_bulk[_keep].fillna('').astype(str)
    gc.collect()

    # ── 4. 建立索引（KW + ASIN）──────────────────────────────────────────────
    index_map = build_bulk_index(df_bulk, 'BOTH')

    # ── 4b. 建立活动元数据索引（出价策略 + 展示位置调整）────────────────────
    camp_meta = build_campaign_meta_index(df_bulk)

    # ── 5. 降价处理 ───────────────────────────────────────────────────────────
    all_updated_rows: list = []
    all_details:      list = []

    if not df_filtered.empty:
        df_full, df_bulk, updated_rows, details = process_updates(
            df_filtered, df_full, df_bulk, index_map, 'BOTH', log, camp_meta=camp_meta
        )
        all_updated_rows.extend(updated_rows)
        all_details.extend(details)

    # ── 5b. 提价处理 ──────────────────────────────────────────────────────────
    if not df_up_filtered.empty:
        df_full, df_bulk, up_updated_rows, up_details = process_updates(
            df_up_filtered, df_full, df_bulk, index_map, 'BOTH', log, camp_meta=camp_meta
        )
        all_updated_rows.extend(up_updated_rows)
        all_details.extend(up_details)

    # ── 6. 保存 Bulk（传入预加载的 Workbook，避免重复读取文件）────────────────
    bulk_out_bytes = save_bulk_updated(_wb_preloaded, df_bulk, all_updated_rows)
    del _wb_preloaded
    gc.collect()

    # ── 7. 保存 targeting_labels ──────────────────────────────────────────────
    label_csv_bytes = save_label_updated(df_full)

    # ── 8. 汇总日志 ────────────────────────────────────────────────────────────
    n_matched   = len(all_updated_rows)
    n_unmatched = sum(1 for l in log if '[未匹配]' in l)
    n_skip      = sum(1 for l in log if '[跳过' in l)
    log.insert(0,
        f'共更新 {n_matched} 条 | 未匹配 {n_unmatched} 条 | '
        f'跳过 {n_skip} 条（Bid为空/adj无效）'
    )

    return bulk_out_bytes, label_csv_bytes, log, all_details
