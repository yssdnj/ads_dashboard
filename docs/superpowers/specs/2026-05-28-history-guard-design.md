# 历史调价保护（History Guard）设计文档

## 目标

在"生成更新文件"时，检查每条 targeting 的历史调价记录。若某条 targeting（Campaign + Ad Group + Targeting 三元组）距上次确认调价 ≤ 21 天，则跳过该条 targeting（降价和提价均不执行）。同时在 targeting_labels CSV 中新增 `history` 列，记录最近一次调价信息。

**保护粒度**：targeting 级别（不是 campaign 级别）。同一广告活动中不同 targeting 独立判断。

---

## 背景

### 现有流程（export-bulk）

```
用户上传 Bulk 文件 + rows_json
  → apply_mode1_to_bulk()
      → load_label_df()           降价筛选
      → 提价筛选
      → process_updates()         写回 Bulk Bid / Operation
      → save_label_updated()      输出 targeting_labels CSV
      → save_bulk_updated()       输出 updated Bulk xlsx
```

### 历史数据来源

```
bid_update_log         （一次确认操作 = 一条记录）
  id, confirmed_at, product_target, country, ...

bid_update_detail      （一次操作中每条 targeting = 一条记录）
  log_id, campaign, ad_group, targeting, old_bid, new_bid, ...
```

`confirmed_at`：用户点击"确认已上传到亚马逊"时写入，是广告后台实际变价的最近代理时间。

**匹配键**：`(campaign, ad_group, targeting)` 三元组，对应 `bid_update_detail` 的三个字段。

---

## 设计

### 涉及文件

| 文件 | 改动 |
|------|------|
| `ads_funnel/api/db.py` | 新增 `get_targeting_history(product_target, country)` |
| `ads_funnel/api/main.py` | `api_mode1_export_bulk()` 调用前查历史，传参 |
| `ads_funnel/api/bulk_update.py` | `apply_mode1_to_bulk()` 新增参数 + 3 处逻辑 |

---

### 1. db.py — `get_targeting_history()`

**函数签名**

```python
def get_targeting_history(product_target: str, country: str) -> dict[tuple, dict]:
```

**SQL**

```sql
SELECT   d.campaign,
         d.ad_group,
         d.targeting,
         MAX(l.confirmed_at) AS last_confirmed_at
FROM     bid_update_detail d
JOIN     bid_update_log    l ON d.log_id = l.id
WHERE    l.product_target = :pt
  AND    l.country        = :co
GROUP BY d.campaign, d.ad_group, d.targeting
```

**返回值**

```python
{
    ("SP_KW_UK_SL", "Ad Group 1", "keyword abc"): {
        "confirmed_at": "2026-05-10 14:23:00",   # str, YYYY-MM-DD HH:MM:SS
        "days_ago":     17.92                      # float
    },
    ...
}
```

key 为 Python tuple `(campaign, ad_group, targeting)`，与 `bulk_update.py` 中三元组匹配方式一致。

`days_ago = (now - confirmed_at).total_seconds() / 86400`

**异常处理**：DB 查询失败时返回空 dict `{}`（降级为无保护，不中断文件生成）。

---

### 2. main.py — `api_mode1_export_bulk()` 调用点

在调用 `apply_mode1_to_bulk()` 之前：

```python
history_map = db.get_targeting_history(product_target, report_country)
```

传入 `apply_mode1_to_bulk(..., history_map=history_map)`。

---

### 3. bulk_update.py — `apply_mode1_to_bulk()` 新增逻辑

#### 新增参数

```python
def apply_mode1_to_bulk(
    ...
    history_map: dict | None = None,   # {(campaign, ad_group, targeting): {confirmed_at, days_ago}}
    history_guard_days: int = 21,
) -> tuple[bytes, bytes, list[str], list]:
```

`history_map=None` 时跳过所有历史保护逻辑（行为与现在完全一致）。

#### Step A：填充 `history` 列（在 df_label 初始化之后，筛选之前）

```python
def _fmt_history(campaign: str, ad_group: str, targeting: str,
                 history_map: dict, guard_days: int) -> str:
    key = (campaign, ad_group, targeting)
    h = history_map.get(key)
    if not h:
        return ''
    days   = h['days_ago']
    result = '不调价' if days <= guard_days else '调价'
    return f"{h['confirmed_at']} | {days:.2f}天前 | {result}"

if history_map:
    df_label['history'] = df_label.apply(
        lambda row: _fmt_history(
            str(row['Campaign Name']).strip(),
            str(row['Ad Group Name']).strip(),
            str(row['Targeting']).strip(),
            history_map, history_guard_days
        ),
        axis=1
    )
else:
    df_label['history'] = ''
```

#### Step B：构建受保护 targeting 集合

```python
protected_keys: set[tuple] = set()
if history_map:
    for (camp, adgrp, tgt), h in history_map.items():
        if h['days_ago'] <= history_guard_days:
            protected_keys.add((camp, adgrp, tgt))

if protected_keys:
    log.append(f'[历史保护] 共 {len(protected_keys)} 条 targeting 在 {history_guard_days} 天内已调价，跳过')
```

#### Step C：过滤降价 / 提价筛选结果

在 `load_label_df()` 返回 `df_filtered` 之后，以及 `df_up_filtered` 构建之后：

```python
def _make_key(row) -> tuple:
    return (
        str(row['Campaign Name']).strip(),
        str(row['Ad Group Name']).strip(),
        str(row['Targeting']).strip(),
    )

if protected_keys:
    mask_down = df_filtered.apply(_make_key, axis=1).isin(protected_keys)
    df_filtered = df_filtered[~mask_down]

    mask_up = df_up_filtered.apply(_make_key, axis=1).isin(protected_keys)
    df_up_filtered = df_up_filtered[~mask_up]
```

受保护的行保留在 `df_full`（仍出现在 CSV 中，history 列显示"不调价"），但不进入 `process_updates()`（Bulk 文件中不修改 Bid）。

#### Step D：`history` 列加入 CSV 输出

`save_label_updated()` 的 `CSV_COLS` 末尾追加 `'history'`：

```python
CSV_COLS = [
    'Campaign Name', 'Ad Group Name', 'Targeting', 'Match Type',
    'impressions', 'clicks', 'orders', 'spend', 'sales',
    'ACoS(%)', 'CVR(%)', 'CPC($)', '销售占比(%)', '花费占比(%)',
    'label', 'action', 'adj_pct', 'adj_dollar', 'reason',
    '原竞价', '新竞价', '操作日期', 'history',   # ← 新增
]
```

---

## history 列格式示例

| 场景 | history 列内容 |
|------|--------------|
| 该 targeting 从未调价 | `""` （空） |
| 最近调价 10 天前 | `"2026-05-18 09:30:00 \| 10.25天前 \| 不调价"` |
| 最近调价 25 天前 | `"2026-05-03 14:15:00 \| 24.87天前 \| 调价"` |

---

## 边界条件

| 情况 | 处理 |
|------|------|
| DB 查询失败 | 返回空 dict，history 列全空，无保护（不阻断文件生成） |
| history_map=None（未传入）| history 列全空，无保护 |
| product_target 或 country 为空字符串 | 仍正常查询（WHERE 条件匹配空字符串） |
| targeting 含特殊字符 | SQL 参数化查询，无影响 |
| 同一三元组多次历史记录 | MAX(confirmed_at) 取最近一次 |
| 同活动下其他 targeting 无历史 | 仅对有记录的三元组保护，其余正常调价 |

---

## 不改动的内容

- `bid_update_log` 和 `bid_update_detail` 表结构不变
- `confirm-update` 写库逻辑不变
- 前端（template.html）不改动
- `history_guard_days=21` 硬编码在后端，不作为 API 参数暴露
