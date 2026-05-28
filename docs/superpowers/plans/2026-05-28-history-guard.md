# History Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 export-bulk 时检查每条 targeting（三元组）的历史调价记录，21天内已调价的行跳过，并在 targeting CSV 新增 `history` 列。

**Architecture:** db.py 新增 `get_targeting_history()` 查最近确认时间（三元组为 key），main.py 在 export-bulk 调用前查好 history_map 传入，bulk_update.py 新增 `_fmt_history()` 辅助函数 + 两个参数 + A/B/C 三步保护逻辑 + CSV 新列。

**Tech Stack:** Python 3.10+, SQLAlchemy, pandas, FastAPI, openpyxl

---

## 文件改动一览

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `ads_funnel/api/db.py` | 新增函数 | `get_targeting_history()` |
| `ads_funnel/api/bulk_update.py` | 新增函数 + 修改函数 | `_fmt_history()` + `apply_mode1_to_bulk()` + `save_label_updated()` |
| `ads_funnel/api/main.py` | 修改函数 | `api_mode1_export_bulk()` |
| `ads_funnel/api/_test_history_guard.py` | 新建测试 | 纯逻辑测试（无需 DB） |

---

### Task 1：db.py — `get_targeting_history()`

**Files:**
- Modify: `ads_funnel/api/db.py`（在 `list_bid_update_logs()` 结束后、`# ── 分析原始数据` 注释之前插入）
- Create: `ads_funnel/api/_test_history_guard.py`

- [ ] **Step 1：在 `ads_funnel/api/db.py` 中，`list_bid_update_logs()` 结束后（约第 487 行），插入以下函数**

```python
def get_targeting_history(product_target: str, country: str) -> dict:
    """
    返回每个 (campaign, ad_group, targeting) 三元组最近一次已确认调价的时间，
    按 product_target + country 范围查询。

    返回格式：
        {
            ('Camp1', 'AG1', 'kw_abc'): {
                'confirmed_at': '2026-05-10 14:23:00',
                'days_ago': 17.92,
            },
            ...
        }
    DB 异常时返回 {}，不中断调用方。
    """
    try:
        with get_engine().connect() as conn:
            result = conn.execute(
                text(
                    'SELECT d.campaign, d.ad_group, d.targeting, '
                    '       MAX(l.confirmed_at) AS last_confirmed_at '
                    'FROM   bid_update_detail d '
                    'JOIN   bid_update_log    l ON d.log_id = l.id '
                    'WHERE  l.product_target = :pt '
                    '  AND  l.country        = :co '
                    'GROUP  BY d.campaign, d.ad_group, d.targeting'
                ),
                {'pt': product_target, 'co': country},
            )
            now = datetime.now()
            out: dict = {}
            for row in result.mappings():
                conf_at = row['last_confirmed_at']   # MySQL 返回 datetime 对象
                if conf_at is None:
                    continue
                days_ago = (now - conf_at).total_seconds() / 86400
                key = (
                    str(row['campaign']).strip(),
                    str(row['ad_group']).strip(),
                    str(row['targeting']).strip(),
                )
                out[key] = {
                    'confirmed_at': conf_at.strftime('%Y-%m-%d %H:%M:%S'),
                    'days_ago':     days_ago,
                }
            return out
    except Exception:
        return {}
```

> **注意**：`db.py` 顶部已有 `from datetime import datetime`（通过现有函数确认），无需重复导入。

- [ ] **Step 2：创建测试文件 `ads_funnel/api/_test_history_guard.py`，内容如下（仅测试 bulk_update.py 的纯逻辑，不访问 DB）**

```python
"""
_test_history_guard.py
测试历史调价保护逻辑（history guard）。
运行命令（从 ads_dashboard/ 目录）：
    python ads_funnel/api/_test_history_guard.py
"""
import sys
import os
import io
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
import csv as _csv
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
# Bulk 中加第二条 targeting
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
    history_map=hmap_protect,   # 只保护 TGT，不保护 TGT2
    history_guard_days=21,
)
assert any('已更新' in l and TGT2 in l for l in log_lines3), \
    f'Test 6 FAIL: kw_other 应被更新，实际日志: {log_lines3}'
assert not any('已更新' in l and TGT in l and TGT2 not in l for l in log_lines3), \
    f'Test 6 FAIL: kw_test 不应被更新，实际日志: {log_lines3}'
print('✅ Test 6: 同活动不同 targeting 独立判断')


print('\n🎉 所有测试通过！')
```

- [ ] **Step 3：运行测试，确认失败（此时 `_fmt_history` 尚未存在）**

```bash
# 从 ads_dashboard/ 目录运行
python ads_funnel/api/_test_history_guard.py
```

期望输出：`ImportError: cannot import name '_fmt_history' from 'bulk_update'`

---

### Task 2：bulk_update.py — `_fmt_history` + `apply_mode1_to_bulk` 改动

**Files:**
- Modify: `ads_funnel/api/bulk_update.py`

- [ ] **Step 1：在 `_pct_str_to_decimal()` 结束后（约第 319 行）、`apply_mode1_to_bulk()` 开始前，插入 `_fmt_history()` 辅助函数**

```python
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
    days   = h['days_ago']
    result = '不调价' if days <= guard_days else '调价'
    return f"{h['confirmed_at']} | {days:.2f}天前 | {result}"
```

- [ ] **Step 2：修改 `apply_mode1_to_bulk()` 函数签名，新增两个参数（在 `up_orders_threshold` 之后）**

将：
```python
def apply_mode1_to_bulk(
    bulk_bytes: bytes,
    rows: list[dict],
    product_target: str = '',
    report_start: str = '',
    report_end: str = '',
    orders_threshold: int = 10,
    up_orders_threshold: int = 2,
) -> tuple[bytes, bytes, list[str]]:
```

改为：
```python
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
```

- [ ] **Step 3：在 `apply_mode1_to_bulk()` 内，`df_label['操作日期'] = ''` 这行之后（约第 354 行），插入 Step A（填充 history 列）**

在：
```python
    df_label['操作日期'] = ''
```

之后插入：
```python
    # ── Step A: 填充 history 列 ────────────────────────────────────────────
    if history_map:
        df_label['history'] = df_label.apply(
            lambda row: _fmt_history(
                row['Campaign Name'],
                row['Ad Group Name'],
                row['Targeting'],
                history_map,
                history_guard_days,
            ),
            axis=1,
        )
    else:
        df_label['history'] = ''
```

- [ ] **Step 4：在提价筛选日志 `log.append(f'提价筛选: ...')` 之后（约第 372 行），插入 Step B（构建受保护集合）和 Step C（过滤）**

在：
```python
    log.append(
        f'提价筛选: {len(df_up_filtered)}/{len(df_full)} 行符合条件'
        f'（低ACoS出单 且 orders≥{up_orders_threshold} 且 adj_pct>0）'
    )
```

之后插入：
```python
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
        def _row_key(row) -> tuple:
            return (
                str(row['Campaign Name']).strip(),
                str(row['Ad Group Name']).strip(),
                str(row['Targeting']).strip(),
            )
        df_filtered    = df_filtered[
            ~df_filtered.apply(_row_key, axis=1).isin(protected_keys)
        ]
        df_up_filtered = df_up_filtered[
            ~df_up_filtered.apply(_row_key, axis=1).isin(protected_keys)
        ]
```

- [ ] **Step 5：修改 `save_label_updated()` 中的 `CSV_COLS`，末尾追加 `'history'`**

将：
```python
    CSV_COLS = [
        'Campaign Name', 'Ad Group Name', 'Targeting', 'Match Type',
        'impressions', 'clicks', 'orders', 'spend', 'sales',
        'ACoS(%)', 'CVR(%)', 'CPC($)', '销售占比(%)', '花费占比(%)',
        'label', 'action', 'adj_pct', 'adj_dollar', 'reason',
        '原竞价', '新竞价', '操作日期',
    ]
```

改为：
```python
    CSV_COLS = [
        'Campaign Name', 'Ad Group Name', 'Targeting', 'Match Type',
        'impressions', 'clicks', 'orders', 'spend', 'sales',
        'ACoS(%)', 'CVR(%)', 'CPC($)', '销售占比(%)', '花费占比(%)',
        'label', 'action', 'adj_pct', 'adj_dollar', 'reason',
        '原竞价', '新竞价', '操作日期', 'history',
    ]
```

- [ ] **Step 6：运行测试，确认全部通过**

```bash
python ads_funnel/api/_test_history_guard.py
```

期望输出：
```
✅ Test 1: 无历史记录 → 空字符串
✅ Test 2: 10 天前调价 → 不调价
✅ Test 3: 30 天前调价 → 调价
✅ Test 4: history_map=None → 正常调价，CSV 含 history 列
✅ Test 5: 受保护 targeting 跳过，history 列显示"不调价"
✅ Test 6: 同活动不同 targeting 独立判断

🎉 所有测试通过！
```

- [ ] **Step 7：提交**

```bash
git add ads_funnel/api/bulk_update.py ads_funnel/api/_test_history_guard.py
git commit -m "feat: history guard — _fmt_history + apply_mode1_to_bulk 保护逻辑 + CSV history 列"
```

---

### Task 3：main.py — 接入 `get_targeting_history()`

**Files:**
- Modify: `ads_funnel/api/main.py`（`api_mode1_export_bulk` 函数，约第 428—445 行）

- [ ] **Step 1：在 `bulk_bytes = await bulk_file.read()` 之后、`try:` 块之前，插入历史查询调用**

将：
```python
    bulk_bytes = await bulk_file.read()

    try:
        bulk_out_bytes, label_csv_bytes, log, details = bulk_update.apply_mode1_to_bulk(
            bulk_bytes, rows,
            product_target      = product_target,
            report_start        = report_start,
            report_end          = report_end,
            orders_threshold    = orders_threshold,
            up_orders_threshold = up_orders_threshold,
        )
    except Exception as e:
        raise HTTPException(500, f'Bulk 更新失败: {e}')
```

改为：
```python
    bulk_bytes = await bulk_file.read()

    history_map = db.get_targeting_history(product_target, report_country)

    try:
        bulk_out_bytes, label_csv_bytes, log, details = bulk_update.apply_mode1_to_bulk(
            bulk_bytes, rows,
            product_target      = product_target,
            report_start        = report_start,
            report_end          = report_end,
            orders_threshold    = orders_threshold,
            up_orders_threshold = up_orders_threshold,
            history_map         = history_map,
        )
    except Exception as e:
        raise HTTPException(500, f'Bulk 更新失败: {e}')
```

- [ ] **Step 2：提交**

```bash
git add ads_funnel/api/db.py ads_funnel/api/main.py
git commit -m "feat: history guard — get_targeting_history() + export-bulk 接入"
```

---

## 自检清单（实施者完成后确认）

- [ ] 6 个测试全部 ✅
- [ ] `history_map=None` 时行为与改动前完全一致（向后兼容）
- [ ] DB 查询失败时返回 `{}`，不影响文件生成
- [ ] targeting CSV 末尾有 `history` 列
- [ ] 受保护行不出现在 Bulk xlsx 的 `Operation=Update` 行中
