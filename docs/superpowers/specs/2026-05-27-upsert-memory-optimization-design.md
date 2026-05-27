# Upsert 内存优化设计文档

**日期：** 2026-05-27  
**涉及文件：** `ads_funnel/api/db.py`  
**问题：** `_upsert_lx` / `_upsert_df` 每次导入数据时全表读入内存，随历史数据积累内存峰值持续增长

---

## 背景

当前 upsert 逻辑（`_upsert_lx` 用于 `raw_camp_lx`/`raw_port_lx`，`_upsert_df` 用于 `raw_tar`/`raw_ap`）：

```
1. SELECT * FROM table  →  整张表读进 Python DataFrame
2. pandas concat 新旧数据
3. to_sql(..., if_exists='replace')  →  整张表覆盖写回
```

问题根源：无论新数据多少，都强制全表 IO。随着多国家 × 多周历史数据积累，内存峰值持续增长。

**使用规律：**
- 绝大多数上传是新增数据（新的一批周）
- 极少数情况会重传已有周数据（纠错）

---

## 优化方案：日期范围感知 upsert

利用"上传通常是新增"这一规律，在每次 upsert 前先查询日期是否重叠，走不同路径：

### 无重叠路径（绝大多数情况）

```
1. COUNT：表内是否有 date BETWEEN new_min AND new_max 的行？
2. 结果为 0 → 直接 df_new.to_sql(if_exists='append')
```

内存占用：仅新数据本身，无任何历史数据读取。

### 有重叠路径（极少数情况，重传旧周）

```
1. COUNT 结果 > 0
2. SELECT * WHERE date BETWEEN new_min AND new_max  →  只读重叠窗口
3. 按唯一键过滤：删除旧数据中与新数据冲突的行
4. DELETE FROM table WHERE date BETWEEN new_min AND new_max（清空重叠窗口）
5. pd.concat(保留的旧行, 新数据).to_sql(if_exists='append')
```

内存占用：新数据 + 重叠日期窗口（通常 1-4 周），远小于全表。

### 内存对比

| 场景 | 当前 | 优化后 |
|------|------|--------|
| 新增上传（常规） | O(全部历史) | O(新数据) |
| 重传旧周（偶发） | O(全部历史) | O(新数据 + 重叠窗口) |
| 写入方式 | 全表覆盖 | append INSERT |

---

## 实现细节

### 涉及函数

| 函数 | 表 | 日期列 |
|------|----|--------|
| `_upsert_lx` | `raw_camp_lx`, `raw_port_lx` | `日期` |
| `_upsert_df` | `raw_tar`, `raw_ap` | `Date` / `日期` |

### 改动逻辑（两个函数统一改为以下结构）

```python
def _upsert_xxx(engine, tbl, df_new, keys, date_col):
    # 1. 表为空 → 直接写入（现有逻辑不变）
    if row_count == 0:
        df_new.to_sql(tbl, engine, if_exists='replace', index=False)
        return

    # 2. 查询新数据的日期范围
    new_min = df_new[date_col].min()
    new_max = df_new[date_col].max()

    # 3. 检查是否有重叠
    with engine.connect() as conn:
        overlap = conn.execute(
            text(f'SELECT COUNT(*) FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
            {'a': str(new_min), 'b': str(new_max)}
        ).scalar()

    if overlap == 0:
        # 4a. 无重叠 → 直接 append，零历史数据读取
        df_new.to_sql(tbl, engine, if_exists='append', index=False)
    else:
        # 4b. 有重叠 → 只读重叠窗口
        df_old = pd.read_sql(
            f'SELECT * FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b',
            engine, params={'a': str(new_min), 'b': str(new_max)}
        )
        # 按唯一键过滤：新数据覆盖旧数据
        avail_keys = [k for k in keys if k in df_old.columns and k in df_new.columns]
        if avail_keys:
            sep = '\x00'
            old_comp = df_old[avail_keys].astype(str).agg(sep.join, axis=1)
            new_comp = df_new[avail_keys].astype(str).agg(sep.join, axis=1)
            df_old = df_old[~old_comp.isin(set(new_comp))]
        # 删除重叠窗口旧行，写入合并结果
        with engine.begin() as conn:
            conn.execute(
                text(f'DELETE FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                {'a': str(new_min), 'b': str(new_max)}
            )
        pd.concat([df_old, df_new], ignore_index=True).to_sql(
            tbl, engine, if_exists='append', index=False
        )
```

### 边界情况处理

| 场景 | 处理方式 |
|------|----------|
| 表不存在 | `if_exists='replace'` 自动建表（现有逻辑不变） |
| 表为空 | `if_exists='replace'` 直接写入（现有逻辑不变） |
| 新数据跨越多个已有周 | DELETE + INSERT 覆盖整个 new_min~new_max 窗口 |
| 日期列为 datetime 类型 | str() 转换后传给 SQL 参数（与现有 `_prep_*` 函数保持一致） |

---

## 不改动的部分

- 函数签名不变，调用方 `upsert_raw` / `upsert_tar_ap` 无需修改
- 索引创建逻辑不变
- `_prep_tar` / `_prep_ap_daily` / `_prep_raw` 预处理逻辑不变
- `get_tar_ap_for_analysis` 全表读问题留作后续单独优化

---

## 风险评估

| 风险 | 等级 | 说明 |
|------|------|------|
| 新增数据写错表 | 低 | `if_exists='append'` 与现有字段完全兼容 |
| DELETE 误删数据 | 低 | DELETE 范围与新数据日期完全一致，且在事务内执行 |
| 日期类型不兼容 | 低 | `_prep_*` 已将日期统一为 YYYY-MM-DD 字符串 |
| 并发写入冲突 | 无 | 当前为单用户单进程，无并发写入 |
