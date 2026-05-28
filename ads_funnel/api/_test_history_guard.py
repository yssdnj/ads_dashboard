"""
_test_history_guard.py
测试历史调价保护逻辑（history guard）。
运行命令（从 ads_dashboard/ 目录）：
    python ads_funnel/api/_test_history_guard.py
"""
import sys
import os
import io
import csv as _csv
from datetime import datetime, timedelta

# Python 会把脚本所在目录加入 sys.path，因此可直接导入同目录模块
from bulk_update import _fmt_history, apply_mode1_to_bulk
from openpyxl import Workbook


# ────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────────────────────────────────

def _make_bulk_bytes(camp: str, adgrp: str, tgt: str, bid: str = '0.50') -> bytes:
    """生成最小可用的 Bulk xlsx bytes，只含一条 Keyword 行。"""
    wb = Workbook()
    ws = wb.active
    ws.title = 'Sponsored Products Campaigns'
    ws.append([
        'Entity', 'Operation',
        'Campaign Name (Informational only)',
        'Ad Group Name (Informational only)',
        'Keyword Text', 'Product Targeting Expression', 'Bid',
    ])
    ws.append([
        'Keyword', 'Create', camp, adgrp, tgt, '', bid,
    ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _make_row(camp: str, adgrp: str, tgt: str,
              label: str, adj_pct: str, orders: int = 3) -> dict:
    """生成最小 consensus row dict（与 targeting_analysis.py 输出格式一致）。"""
    return {
        'campaign': camp, 'ad_group': adgrp, 'targeting': tgt,
        'match_type': 'exact', 'impressions': 1000, 'clicks': 50,
        'orders': orders, 'spend': 100.0, 'sales': 200.0,
        'acos': 0.5, 'cvr': 0.1, 'cpc': 2.0,
        'sales_share': 0.1, 'spend_share': 0.1,
        'label': label, 'action': '', 'adj_pct': adj_pct,
        'adj_dollar': 0, 'reason': '',
    }


# ────────────────────────────────────────────────────────────────────────────
# Test 1: _fmt_history — 无历史记录 → 返回空字符串
# ────────────────────────────────────────────────────────────────────────────
result = _fmt_history('C1', 'AG1', 'kw1', {}, 21)
assert result == '', f'Test 1 FAIL: 无历史应返回空字符串，实际 {result!r}'
print('✅ Test 1: 无历史记录 → 空字符串')


# ────────────────────────────────────────────────────────────────────────────
# Test 2: _fmt_history — 10 天前调价 → 不调价
# ────────────────────────────────────────────────────────────────────────────
recent_at = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
hmap_recent = {('C1', 'AG1', 'kw1'): {'confirmed_at': recent_at, 'days_ago': 10.0}}
result = _fmt_history('C1', 'AG1', 'kw1', hmap_recent, 21)
assert '不调价' in result, f'Test 2 FAIL: 应含"不调价"，实际 {result!r}'
assert '10.00天前' in result, f'Test 2 FAIL: 应含"10.00天前"，实际 {result!r}'
assert recent_at in result, f'Test 2 FAIL: 应含 confirmed_at，实际 {result!r}'
print('✅ Test 2: 10 天前调价 → 不调价')


# ────────────────────────────────────────────────────────────────────────────
# Test 3: _fmt_history — 30 天前调价 → 调价（>21 天保护期外）
# ────────────────────────────────────────────────────────────────────────────
old_at = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
hmap_old = {('C1', 'AG1', 'kw1'): {'confirmed_at': old_at, 'days_ago': 30.0}}
result = _fmt_history('C1', 'AG1', 'kw1', hmap_old, 21)
assert result.endswith('| 调价'), f'Test 3 FAIL: 应以"| 调价"结尾，实际 {result!r}'
assert '不调价' not in result, f'Test 3 FAIL: 不应含"不调价"，实际 {result!r}'
print('✅ Test 3: 30 天前调价 → 调价')


# ────────────────────────────────────────────────────────────────────────────
# Test 4: apply_mode1_to_bulk — history_map=None → 正常调价，CSV 含 history 列
# ────────────────────────────────────────────────────────────────────────────
CAMP, ADGRP, TGT = 'Camp1', 'AG1', 'kw_test'
rows_down = [_make_row(CAMP, ADGRP, TGT, '⚠️ 高ACoS出单', '-10%', orders=3)]
bulk_bytes = _make_bulk_bytes(CAMP, ADGRP, TGT, bid='0.50')

_, csv_bytes, log_lines, _ = apply_mode1_to_bulk(
    bulk_bytes, rows_down, history_map=None,
)
csv_text = csv_bytes.decode('utf-8-sig')
header_cols = csv_text.split('\n')[0].strip().split(',')
assert 'history' in header_cols, \
    f'Test 4 FAIL: CSV 首行应含 history 列，实际首行: {header_cols}'
assert any('已更新' in l for l in log_lines), \
    f'Test 4 FAIL: 应有 [已更新] 日志，实际: {log_lines}'
print('✅ Test 4: history_map=None → 正常调价，CSV 含 history 列')


# ────────────────────────────────────────────────────────────────────────────
# Test 5: apply_mode1_to_bulk — 受保护 targeting 被跳过，history 列显示"不调价"
# ────────────────────────────────────────────────────────────────────────────
recent_at2 = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
hmap_protect = {(CAMP, ADGRP, TGT): {'confirmed_at': recent_at2, 'days_ago': 5.0}}

_, csv_bytes2, log_lines2, details2 = apply_mode1_to_bulk(
    bulk_bytes, rows_down,
    history_map=hmap_protect,
    history_guard_days=21,
)
csv_text2 = csv_bytes2.decode('utf-8-sig')

# 不应有 [已更新] 日志
assert not any('已更新' in l for l in log_lines2), \
    f'Test 5 FAIL: 受保护行不应被更新，实际日志: {log_lines2}'
# 应有 [历史保护] 日志
assert any('历史保护' in l for l in log_lines2), \
    f'Test 5 FAIL: 应有 [历史保护] 日志，实际: {log_lines2}'
# CSV 数据行 history 列应含"不调价"
reader = _csv.DictReader(io.StringIO(csv_text2.lstrip('﻿')))
rows_out = list(reader)
assert len(rows_out) == 1, f'Test 5 FAIL: CSV 应有 1 行数据，实际 {len(rows_out)} 行'
assert '不调价' in rows_out[0].get('history', ''), \
    f'Test 5 FAIL: history 列应含"不调价"，实际 {rows_out[0].get("history")!r}'
print('✅ Test 5: 受保护 targeting 跳过，history 列显示"不调价"')


# ────────────────────────────────────────────────────────────────────────────
# Test 6: 同活动另一 targeting 无保护，正常调价
# ────────────────────────────────────────────────────────────────────────────
TGT2 = 'kw_other'
rows_two = [
    _make_row(CAMP, ADGRP, TGT,  '⚠️ 高ACoS出单', '-10%', orders=3),   # 受保护
    _make_row(CAMP, ADGRP, TGT2, '⚠️ 高ACoS出单', '-10%', orders=3),   # 不受保护
]
wb2 = Workbook()
ws2 = wb2.active
ws2.title = 'Sponsored Products Campaigns'
ws2.append([
    'Entity', 'Operation',
    'Campaign Name (Informational only)',
    'Ad Group Name (Informational only)',
    'Keyword Text', 'Product Targeting Expression', 'Bid',
])
ws2.append(['Keyword', 'Create', CAMP, ADGRP, TGT,  '', '0.50'])
ws2.append(['Keyword', 'Create', CAMP, ADGRP, TGT2, '', '0.60'])
buf2 = io.BytesIO()
wb2.save(buf2)
buf2.seek(0)
bulk_bytes2 = buf2.read()

_, csv_bytes3, log_lines3, _ = apply_mode1_to_bulk(
    bulk_bytes2, rows_two,
    history_map=hmap_protect,
    history_guard_days=21,
)
assert any('已更新' in l and TGT2 in l for l in log_lines3), \
    f'Test 6 FAIL: kw_other 应被更新，实际日志: {log_lines3}'
assert not any('已更新' in l and TGT in l and TGT2 not in l for l in log_lines3), \
    f'Test 6 FAIL: kw_test 不应被更新，实际日志: {log_lines3}'
print('✅ Test 6: 同活动不同 targeting 独立判断')


print('\n🎉 所有测试通过！')
