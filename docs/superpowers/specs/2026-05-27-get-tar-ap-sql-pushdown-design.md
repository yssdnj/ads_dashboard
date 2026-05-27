# get_tar_ap_for_analysis SQL 下推优化设计文档

**日期：** 2026-05-27  
**涉及文件：** `ads_funnel/api/db.py`  
**问题：** `get_tar_ap_for_analysis` 每次分析时全表读取 `raw_tar`（~10万行）和 `raw_ap`（~32万行），在 Python 内存里再按国家和日期过滤，实际用到的只是最近 6 周 × 当前国家的子集

---

## 背景

当前流程：

```
1. SELECT * FROM raw_tar          →  ~103,674 行全部读入 Python
2. SELECT * FROM raw_ap           →  ~315,809 行全部读入 Python
3. Python: 按 country_values 过滤
4. Python: max_date = tar_df['Date'].max()
5. Python: 计算 cutoff（最近 6 周起始周日）
6. Python: 按 date >= cutoff 过滤
7. 实际用到 ~1–2 万行，约 95% 的数据被丢弃
```

每次跑 3R 或 6R 分析都触发此路径，随历史数据积累内存峰值持续增长。

---

## 优化方案：过滤条件下推到 SQL

将国家过滤和日期过滤全部在 SQL 层完成，Python 只接收实际需要的行。

### 新执行流程

```
1. SELECT MAX(Date) FROM raw_tar [WHERE country IN (...)]
   → 返回 1 个值：max_date

2. Python: 计算 cutoff（逻辑与现在完全相同，_week_start 不变）

3. SELECT * FROM raw_tar
   WHERE Date >= :cutoff [AND country IN (...)]
   → 只返回 6 周 × 当前国家的行（约 8,000 行）

4. SELECT * FROM raw_ap
   WHERE 日期 >= :cutoff [AND 国家/地区 IN (...)]
   → 只返回 6 周 × 当前国家的行（约 15,000 行）
```

### 内存对比

| 场景 | 当前 | 优化后 |
|------|------|--------|
| 有国家过滤（常规） | O(全部历史 × 全部国家) | O(6周 × 1国家) |
| 无国家过滤 | O(全部历史 × 全部国家) | O(6周 × 全部国家) |

---

## 实现细节

### 函数签名与返回值

**不变**。`get_tar_ap_for_analysis(n_weeks, country_values)` 签名、返回的 `(tar_df, ap_df, week_sundays)` 结构均与现在完全一致，调用方 `main.py` 无需修改。

### 周计算逻辑

**不变**。`_week_start`、`week_sundays` 推导代码一行不动，只是执行时机从"全表读入后"提前到"MAX 查询后"：

```python
# 1. 取 max_date
max_date = <SQL MAX 查询结果>

# 2. 完全相同的周计算逻辑
base_sunday = _week_start(max_date)
week_sundays = sorted([base_sunday - pd.Timedelta(weeks=i) for i in range(n_weeks-1, -1, -1)])
cutoff = week_sundays[0]

# 3. 按 cutoff + country 读数据
tar_df = pd.read_sql('SELECT * FROM raw_tar WHERE Date >= :cutoff ...', ...)
```

### 国家列名处理

两张表的列名不同，需分别处理：

| 表 | 日期列 | 国家列 |
|----|--------|--------|
| `raw_tar` | `Date` | `Country` |
| `raw_ap` | `日期` | `国家/地区` |

country filter 为 None 时，SQL 不加 WHERE 国家条件（与现在逻辑一致）。

### MAX 查询带国家过滤

`max_date` 必须在同一国家过滤下取，否则不同国家的数据截止日期不同会导致 `cutoff` 偏差：

```sql
-- 有国家过滤
SELECT MAX(Date) FROM raw_tar WHERE Country IN (:c0, :c1, ...)

-- 无国家过滤
SELECT MAX(Date) FROM raw_tar
```

### 空表 / 无数据处理

- `max_date` 为 NULL（表为空）→ 返回 `(None, None, [])`，与现在行为一致
- 过滤后 `tar_df` 或 `ap_df` 为空 → 返回 `(None, None, [])`，与现在行为一致

---

## 不改动的部分

- `_week_start` 函数
- `get_tar_ap_for_analysis` 函数签名和返回值
- `main.py` 中的两处调用
- `upsert_tar_ap`、`_upsert_df` 等其他函数
- `get_tar_ap_stats`（已经是 SQL 聚合，不全表读）

---

## 风险评估

| 风险 | 等级 | 说明 |
|------|------|------|
| 周计算结果变化 | 无 | `_week_start` 逻辑完全不变 |
| country=None 时行为变化 | 无 | 无 country 条件时 SQL 不加 WHERE，读取全部国家的 6 周数据 |
| MAX 查询与全量读取的 max_date 不一致 | 无 | MAX 查询与之前 `tar_df['Date'].max()` 语义完全等价 |
| 返回数据与现在不同 | 无 | WHERE cutoff 与现在 Python 过滤条件完全等价 |
