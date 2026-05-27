# Upsert 内存优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `_upsert_lx` / `_upsert_df` 从"全表读 → concat → 全表写"改为"日期范围感知 upsert"，消除大内存峰值。

**Architecture:** 每次 upsert 前先 COUNT 新数据日期窗口内的旧行。无重叠（常规新增）直接 append INSERT；有重叠（重传旧周）只读重叠窗口、DELETE 旧行后 append 合并结果。

**Tech Stack:** Python 3.13, pandas, SQLAlchemy 2.x, PyMySQL, MySQL 8

---

## 文件变更范围

| 文件 | 操作 |
|------|------|
| `ads_funnel/api/db.py` | 修改 `_upsert_lx`（line 197-229）和 `_upsert_df`（line 491-522） |

调用方 `upsert_raw` / `upsert_tar_ap` 签名不变，无需修改。

---

## Task 1：记录改动前基准行数

**Files:**
- Read: `ads_funnel/api/db.py`

- [ ] **Step 1：查询现有各表行数，作为改动后的对比基准**

在项目根目录执行：

```powershell
cd "C:\Users\admin\Desktop\python\ads_dashboard\ads_funnel"
python -c "
from api.db import get_engine
from sqlalchemy import text
engine = get_engine()
for tbl in ['raw_camp_lx','raw_port_lx','raw_tar','raw_ap']:
    try:
        n = engine.connect().execute(text(f'SELECT COUNT(*) FROM \`{tbl}\`')).scalar()
        print(f'{tbl}: {n} rows')
    except:
        print(f'{tbl}: table not found')
"
```

记录输出（例如 `raw_tar: 58487 rows`），Task 3 验证时用到。

---

## Task 2：优化 `_upsert_lx`

**Files:**
- Modify: `ads_funnel/api/db.py:197-229`

- [ ] **Step 1：用以下代码替换 `_upsert_lx` 函数体**

将 `db.py` 中 `_upsert_lx` 整个函数替换为：

```python
def _upsert_lx(tbl: str, df_new: pd.DataFrame, keys: list):
    """通用 upsert（领星表）：按唯一键合并新旧数据，新行覆盖旧行。
    优化：优先走无重叠路径（直接 append），仅在日期窗口有重叠时才读取旧数据。
    """
    engine = get_engine()
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())

    if tbl not in tables:
        row_count = 0
    else:
        with engine.connect() as conn:
            row_count = conn.execute(text(f'SELECT COUNT(*) FROM `{tbl}`')).scalar()

    if row_count == 0:
        df_new.to_sql(tbl, engine, if_exists='replace', index=False)
    else:
        date_col = '日期'
        new_min = str(df_new[date_col].min())
        new_max = str(df_new[date_col].max())

        with engine.connect() as conn:
            overlap = conn.execute(
                text(f'SELECT COUNT(*) FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                {'a': new_min, 'b': new_max}
            ).scalar()

        if overlap == 0:
            # 无重叠：直接追加，零历史数据读取
            df_new.to_sql(tbl, engine, if_exists='append', index=False)
        else:
            # 有重叠：只读重叠窗口，新数据覆盖旧数据
            df_old = pd.read_sql(
                f'SELECT * FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b',
                engine, params={'a': new_min, 'b': new_max}
            )
            df_old = df_old.drop(columns=['report_id'], errors='ignore')
            avail_keys = [k for k in keys if k in df_old.columns and k in df_new.columns]
            if avail_keys:
                sep = '\x00'
                old_comp = df_old[avail_keys].astype(str).agg(sep.join, axis=1)
                new_comp = df_new[avail_keys].astype(str).agg(sep.join, axis=1)
                df_old = df_old[~old_comp.isin(set(new_comp))]
            with engine.begin() as conn:
                conn.execute(
                    text(f'DELETE FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                    {'a': new_min, 'b': new_max}
                )
            pd.concat([df_old, df_new], ignore_index=True).to_sql(
                tbl, engine, if_exists='append', index=False
            )

    try:
        with engine.begin() as conn:
            conn.execute(text(
                f'CREATE INDEX idx_{tbl}_date ON `{tbl}`(`日期`)'
            ))
    except Exception:
        pass  # 已存在则忽略
```

---

## Task 3：优化 `_upsert_df`

**Files:**
- Modify: `ads_funnel/api/db.py:491-522`

- [ ] **Step 1：用以下代码替换 `_upsert_df` 函数体**

将 `db.py` 中 `_upsert_df` 整个函数替换为：

```python
def _upsert_df(engine, tbl: str, df_new: pd.DataFrame, keys: list, date_col: str):
    """通用 upsert：按唯一键合并新旧数据，新行覆盖旧行。
    优化：优先走无重叠路径（直接 append），仅在日期窗口有重叠时才读取旧数据。
    """
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())

    if tbl not in tables:
        row_count = 0
    else:
        with engine.connect() as conn:
            row_count = conn.execute(text(f'SELECT COUNT(*) FROM `{tbl}`')).scalar()

    if row_count == 0:
        df_new.to_sql(tbl, engine, if_exists='replace', index=False)
    else:
        new_min = str(df_new[date_col].min())
        new_max = str(df_new[date_col].max())

        with engine.connect() as conn:
            overlap = conn.execute(
                text(f'SELECT COUNT(*) FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                {'a': new_min, 'b': new_max}
            ).scalar()

        if overlap == 0:
            # 无重叠：直接追加，零历史数据读取
            df_new.to_sql(tbl, engine, if_exists='append', index=False)
        else:
            # 有重叠：只读重叠窗口，新数据覆盖旧数据
            df_old = pd.read_sql(
                f'SELECT * FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b',
                engine, params={'a': new_min, 'b': new_max}
            )
            avail_keys = [k for k in keys if k in df_old.columns and k in df_new.columns]
            if avail_keys:
                sep = '\x00'
                old_comp = df_old[avail_keys].astype(str).agg(sep.join, axis=1)
                new_comp = df_new[avail_keys].astype(str).agg(sep.join, axis=1)
                df_old = df_old[~old_comp.isin(set(new_comp))]
            with engine.begin() as conn:
                conn.execute(
                    text(f'DELETE FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                    {'a': new_min, 'b': new_max}
                )
            pd.concat([df_old, df_new], ignore_index=True).to_sql(
                tbl, engine, if_exists='append', index=False
            )

    try:
        safe_col = date_col.replace('日期', 'rq')
        with engine.begin() as conn:
            conn.execute(text(
                f'CREATE INDEX idx_{tbl}_{safe_col} ON `{tbl}`(`{date_col}`)'
            ))
    except Exception:
        pass  # 已存在则忽略
```

---

## Task 4：验证数据完整性

**Files:**
- Run: server on port 5001

- [ ] **Step 1：重启服务器，确认启动无报错**

```powershell
# 停止当前进程
$proc = (Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue).OwningProcess
Stop-Process -Id $proc -Force

# 启动新进程
Start-Process -FilePath "python" -ArgumentList "-m uvicorn api.main:app --host 0.0.0.0 --port 5001 --reload" -WorkingDirectory "C:\Users\admin\Desktop\python\ads_dashboard\ads_funnel" -WindowStyle Hidden

Start-Sleep -Seconds 3
Get-NetTCPConnection -LocalPort 5001 -State Listen | Select-Object OwningProcess
```

预期：输出一个 PID，说明服务器正常启动。

- [ ] **Step 2：验证各表行数与 Task 1 基准一致**

```powershell
cd "C:\Users\admin\Desktop\python\ads_dashboard\ads_funnel"
python -c "
from api.db import get_engine
from sqlalchemy import text
engine = get_engine()
for tbl in ['raw_camp_lx','raw_port_lx','raw_tar','raw_ap']:
    try:
        n = engine.connect().execute(text(f'SELECT COUNT(*) FROM \`{tbl}\`')).scalar()
        print(f'{tbl}: {n} rows')
    except:
        print(f'{tbl}: table not found')
"
```

预期：行数与 Task 1 记录的基准完全一致（代码改动不触及数据，仅改写入路径）。

- [ ] **Step 3：调用 data-stats API 确认数据范围正常**

```powershell
Invoke-RestMethod "http://127.0.0.1:5001/api/analysis/mode1/data-stats" | ConvertTo-Json
```

预期：返回 `raw_tar` / `raw_ap` 的 `min_date`、`max_date`、`count`，数值与改动前相同。

---

## Task 5：提交

**Files:**
- Commit: `ads_funnel/api/db.py`

- [ ] **Step 1：提交**

```bash
cd "C:\Users\admin\Desktop\python\ads_dashboard"
git add ads_funnel/api/db.py
git commit -m "perf: 优化 upsert 内存占用，新增走直接 append 路径，重叠时只读窗口数据"
git push origin HEAD
```
