# get_tar_ap_for_analysis SQL 下推优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `get_tar_ap_for_analysis` 从"全表读 → Python 过滤"改为"SQL 带条件读"，消除每次分析触发的大内存峰值。

**Architecture:** 先用 `SELECT MAX(Date)` 取最新日期（带国家过滤），Python 计算 cutoff（周逻辑不变），再用 `WHERE Date >= cutoff [AND Country IN (...)]` 精准读取两张表。函数签名和返回值完全不变，调用方 `main.py` 零修改。

**Tech Stack:** Python 3.13, pandas, SQLAlchemy 2.x, PyMySQL, MySQL 8

---

## 文件变更范围

| 文件 | 操作 |
|------|------|
| `ads_funnel/api/db.py` | 替换 `get_tar_ap_for_analysis` 函数体（line 607–650） |

---

## Task 1：记录改动前基准数据

**Files:**
- Run: Python script against running server

- [ ] **Step 1：在项目目录执行基准脚本，记录输出**

```powershell
cd "C:\Users\admin\Desktop\python\ads_dashboard\ads_funnel"
python -c "
import pandas as pd
from api.db import get_tar_ap_for_analysis

# 有国家过滤（常规场景）
tar, ap, weeks = get_tar_ap_for_analysis(n_weeks=6, country_values={'United States', 'US'})
if tar is not None:
    print('=== country=US ===')
    print('tar shape:', tar.shape)
    print('ap  shape:', ap.shape)
    print('week_sundays:', [str(w.date()) for w in weeks])
    print('tar Date min:', str(tar['Date'].min().date()))
    print('tar Date max:', str(tar['Date'].max().date()))
else:
    print('country=US: no data')

# 无国家过滤
tar2, ap2, weeks2 = get_tar_ap_for_analysis(n_weeks=6, country_values=None)
if tar2 is not None:
    print('=== country=None ===')
    print('tar shape:', tar2.shape)
    print('ap  shape:', ap2.shape)
    print('week_sundays:', [str(w.date()) for w in weeks2])
"
```

记录全部输出。Task 3 验证时与优化后对比。

---

## Task 2：替换 `get_tar_ap_for_analysis` 函数体

**Files:**
- Modify: `ads_funnel/api/db.py:607-650`

- [ ] **Step 1：将 `get_tar_ap_for_analysis` 整个函数替换为以下代码**

定位当前函数（line 607 开始，`def get_tar_ap_for_analysis`），用下方代码完整替换：

```python
def get_tar_ap_for_analysis(n_weeks: int = 6, country_values: set | None = None) -> tuple:
    """
    从 raw_tar / raw_ap 读取最近 n_weeks 个完整日历周（周日开始）的数据。
    country_values 不为 None 时按 Country / 国家/地区 列过滤。
    返回 (tar_df, ap_df, week_sundays) 或 (None, None, []) 若数据不足。
    优化：国家和日期过滤下推到 SQL，Python 只接收实际需要的行。
    """
    engine = get_engine()
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    if 'raw_tar' not in tables or 'raw_ap' not in tables:
        return None, None, []

    # 构建国家过滤片段（raw_tar 用 Country，raw_ap 用 国家/地区）
    country_clause_tar = ''
    country_clause_ap  = ''
    country_params: dict = {}
    if country_values:
        ph = ', '.join([f':c{i}' for i in range(len(country_values))])
        country_params = {f'c{i}': v for i, v in enumerate(country_values)}
        country_clause_tar = f' AND `Country` IN ({ph})'
        country_clause_ap  = f' AND `国家/地区` IN ({ph})'

    # 1. 取 raw_tar 最新日期（带国家过滤，确保 cutoff 与数据范围一致）
    with engine.connect() as conn:
        max_date_val = conn.execute(
            text(f'SELECT MAX(`Date`) FROM `raw_tar`{country_clause_tar}'),
            country_params
        ).scalar()

    if max_date_val is None:
        return None, None, []

    # 2. 计算 cutoff（周计算逻辑与原代码完全相同）
    max_date     = pd.Timestamp(max_date_val)
    base_sunday  = _week_start(max_date)
    week_sundays = sorted([
        base_sunday - pd.Timedelta(weeks=i)
        for i in range(n_weeks - 1, -1, -1)
    ])
    cutoff_str = week_sundays[0].strftime('%Y-%m-%d')

    # 3. 精准读取：只取 cutoff 之后 + 当前国家的行
    tar_params = {**country_params, 'cutoff': cutoff_str}
    ap_params  = {**country_params, 'cutoff': cutoff_str}

    tar_df = pd.read_sql(
        f'SELECT * FROM `raw_tar` WHERE `Date` >= :cutoff{country_clause_tar}',
        engine, params=tar_params
    )
    ap_df = pd.read_sql(
        f'SELECT * FROM `raw_ap` WHERE `日期` >= :cutoff{country_clause_ap}',
        engine, params=ap_params
    )

    if tar_df.empty or ap_df.empty:
        return None, None, []

    tar_df['Date'] = pd.to_datetime(tar_df['Date'])
    ap_df['日期']  = pd.to_datetime(ap_df['日期'])

    return tar_df, ap_df, week_sundays
```

---

## Task 3：验证结果与基准一致

**Files:**
- Run: Python script + API call

- [ ] **Step 1：重新运行基准脚本，对比输出**

```powershell
cd "C:\Users\admin\Desktop\python\ads_dashboard\ads_funnel"
python -c "
import pandas as pd
from api.db import get_tar_ap_for_analysis

tar, ap, weeks = get_tar_ap_for_analysis(n_weeks=6, country_values={'United States', 'US'})
if tar is not None:
    print('=== country=US ===')
    print('tar shape:', tar.shape)
    print('ap  shape:', ap.shape)
    print('week_sundays:', [str(w.date()) for w in weeks])
    print('tar Date min:', str(tar['Date'].min().date()))
    print('tar Date max:', str(tar['Date'].max().date()))
else:
    print('country=US: no data')

tar2, ap2, weeks2 = get_tar_ap_for_analysis(n_weeks=6, country_values=None)
if tar2 is not None:
    print('=== country=None ===')
    print('tar shape:', tar2.shape)
    print('ap  shape:', ap2.shape)
    print('week_sundays:', [str(w.date()) for w in weeks2])
"
```

预期：`tar shape`、`ap shape`、`week_sundays`、`tar Date min/max` 与 Task 1 记录完全一致。

- [ ] **Step 2：调用 data-stats API 确认服务器正常**

```powershell
Invoke-RestMethod "http://127.0.0.1:5001/api/analysis/mode1/data-stats" | ConvertTo-Json
```

预期：返回 `raw_tar` / `raw_ap` 的 count、min_date、max_date，数值与之前相同。

---

## Task 4：提交

**Files:**
- Commit: `ads_funnel/api/db.py`

- [ ] **Step 1：提交**

```powershell
cd "C:\Users\admin\Desktop\python\ads_dashboard"
git add ads_funnel/api/db.py
git commit -m "perf: get_tar_ap_for_analysis 过滤下推到 SQL，消除全表读取"
git push origin HEAD
```
