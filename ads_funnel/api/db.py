"""
db.py — SQLite 数据库操作层
数据库文件: ads_funnel/ads_funnel.db
"""

import sqlite3, json
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'ads_funnel.db'


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表（幂等）"""
    with get_conn() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                country     TEXT    NOT NULL DEFAULT '',
                weeks       TEXT    NOT NULL,
                wk_dates    TEXT    NOT NULL,
                data        TEXT    NOT NULL,
                camp_file   TEXT    DEFAULT '',
                port_file   TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS config (
                key         TEXT PRIMARY KEY,
                value       TEXT    NOT NULL,
                updated_at  TEXT    DEFAULT (datetime('now','localtime'))
            );
        ''')
        # 迁移：旧表若缺少 country 列则补加
        cols = {r[1] for r in conn.execute('PRAGMA table_info(reports)').fetchall()}
        if 'country' not in cols:
            conn.execute("ALTER TABLE reports ADD COLUMN country TEXT NOT NULL DEFAULT ''")


# ── Reports ───────────────────────────────────────────────────────────────────

def list_reports():
    with get_conn() as conn:
        rows = conn.execute(
            '''SELECT id, title, weeks, wk_dates, camp_file, port_file, created_at
               FROM reports ORDER BY created_at DESC'''
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['weeks']    = json.loads(d['weeks'])
        d['wk_dates'] = json.loads(d['wk_dates'])
        result.append(d)
    return result


def get_report(report_id: int):
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d['weeks']    = json.loads(d['weeks'])
    d['wk_dates'] = json.loads(d['wk_dates'])
    return d


def save_report(title: str, weeks: list, wk_dates: dict, data: dict,
                camp_file='', port_file='') -> int:
    with get_conn() as conn:
        cur = conn.execute(
            '''INSERT INTO reports (title, weeks, wk_dates, data, camp_file, port_file)
               VALUES (?,?,?,?,?,?)''',
            (
                title,
                json.dumps(weeks, ensure_ascii=False),
                json.dumps(wk_dates, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False, separators=(',', ':')),
                camp_file,
                port_file,
            )
        )
        report_id = cur.lastrowid

    # 同步写入细粒度关系表
    _normalize(report_id, weeks, data)
    return report_id


# ── 增量原始数据 ──────────────────────────────────────────────────────────────

# raw_camp 唯一主键列：国家 + 广告组合 + 广告活动 + 日期 + 类型
_KEYS_C = ['国家', '广告组合', '广告活动', '日期', '类型']
# raw_port 唯一主键列：国家 + 广告组合 + 日期
_KEYS_P = ['国家', '广告组合', '日期']


def _prep_raw(df: pd.DataFrame) -> pd.DataFrame:
    """日期列统一转为 YYYY-MM-DD 字符串，去掉旧 report_id 列"""
    d = df.copy()
    if '日期' in d.columns:
        try:
            d['日期'] = pd.to_datetime(d['日期']).dt.strftime('%Y-%m-%d')
        except Exception:
            d['日期'] = d['日期'].astype(str)
    d = d.drop(columns=['report_id'], errors='ignore')
    return d


def upsert_raw(df_c: pd.DataFrame, df_p: pd.DataFrame):
    """
    增量写入 raw_camp / raw_port：
    新数据与库内数据按唯一主键合并，新行覆盖旧行，不同键的旧行保留。
    """
    dc = _prep_raw(df_c)
    dp = _prep_raw(df_p)

    with sqlite3.connect(DB_PATH) as conn:
        for tbl, df_new, keys in [
            ('raw_camp', dc, _KEYS_C),
            ('raw_port', dp, _KEYS_P),
        ]:
            tbl_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            row_count = conn.execute(
                f'SELECT COUNT(*) FROM "{tbl}"'
            ).fetchone()[0] if tbl_exists else 0

            if not tbl_exists or row_count == 0:
                # 首次导入：直接建表写入
                df_new.to_sql(tbl, conn, if_exists='replace', index=False)
            else:
                # 读取库内已有数据
                df_old = pd.read_sql(f'SELECT * FROM "{tbl}"', conn)
                df_old = df_old.drop(columns=['report_id'], errors='ignore')

                # 找出新旧数据共有的主键列
                avail_keys = [k for k in keys
                              if k in df_old.columns and k in df_new.columns]

                if avail_keys:
                    # 用分隔符拼接复合主键，找出旧表中需要被覆盖的行
                    sep = '\x00'
                    old_comp = df_old[avail_keys].astype(str).agg(sep.join, axis=1)
                    new_comp = df_new[avail_keys].astype(str).agg(sep.join, axis=1)
                    # 保留旧表中 key 不在新数据里的行
                    df_old = df_old[~old_comp.isin(set(new_comp))]

                df_merged = pd.concat([df_old, df_new], ignore_index=True)
                df_merged.to_sql(tbl, conn, if_exists='replace', index=False)

            # 日期索引
            try:
                conn.execute(
                    f'CREATE INDEX IF NOT EXISTS idx_{tbl}_date ON "{tbl}"("日期")'
                )
            except Exception:
                pass


def get_raw_dfs():
    """
    从 raw_camp / raw_port 读回完整 DataFrame，供 rebuild_from_raw 使用。
    表不存在或为空时返回 (None, None)。
    """
    with sqlite3.connect(DB_PATH) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'raw_camp' not in tables or 'raw_port' not in tables:
            return None, None
        df_c = pd.read_sql('SELECT * FROM raw_camp', conn)
        df_p = pd.read_sql('SELECT * FROM raw_port', conn)

    if df_c.empty or df_p.empty:
        return None, None

    # 日期还原为 datetime（gen_data.process 内部会再次转换，提前转也无妨）
    for df in (df_c, df_p):
        if '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])

    return df_c, df_p


def rebuild_from_raw() -> list:
    """
    按国家分组，从 raw_camp / raw_port 重建每个国家的报告 JSON。
    - 每次导入后调用
    - 服务器重启时调用
    返回本次更新的 report_id 列表；raw 表为空时返回 []。
    """
    from . import gen_data

    df_c, df_p = get_raw_dfs()
    if df_c is None:
        return []

    col_c = '国家' if '国家' in df_c.columns else None
    col_p = '国家' if '国家' in df_p.columns else None

    # 获取所有国家（以 raw_camp 为准）
    countries = df_c[col_c].unique().tolist() if col_c else ['']

    report_ids = []
    for country in countries:
        # 按国家过滤
        dc = df_c[df_c[col_c] == country].copy() if col_c else df_c
        dp = df_p[df_p[col_p] == country].copy() if col_p else df_p

        if dc.empty or dp.empty:
            continue

        try:
            data = gen_data.process(dc, dp)
        except Exception as e:
            print(f'[rebuild] {country} 处理失败: {e}')
            continue

        weeks     = data['wk']
        wk_dates  = data.get('wk_dates', {})
        wk_range  = f'{weeks[0]}-{weeks[-1]}' if weeks else ''
        new_title = f'{country} {wk_range}' if wk_range else str(country)

        data_json    = json.dumps(data,     ensure_ascii=False, separators=(',', ':'))
        weeks_json   = json.dumps(weeks,    ensure_ascii=False)
        wkdates_json = json.dumps(wk_dates, ensure_ascii=False)

        with get_conn() as conn:
            existing = conn.execute(
                'SELECT id FROM reports WHERE country=? ORDER BY id DESC LIMIT 1',
                (str(country),)
            ).fetchone()

            if existing:
                report_id = existing[0]
                conn.execute(
                    '''UPDATE reports
                       SET title=?, weeks=?, wk_dates=?, data=?,
                           created_at=datetime('now','localtime')
                       WHERE id=?''',
                    (new_title, weeks_json, wkdates_json, data_json, report_id)
                )
            else:
                cur = conn.execute(
                    '''INSERT INTO reports (title, country, weeks, wk_dates, data, camp_file, port_file)
                       VALUES (?,?,?,?,?,'','')''',
                    (new_title, str(country), weeks_json, wkdates_json, data_json)
                )
                report_id = cur.lastrowid

        report_ids.append(report_id)
        print(f'[rebuild] {country}  report_id={report_id}  {wk_range}  '
              f'camp={len(dc)}行  port={len(dp)}行')

    return report_ids


def delete_report(report_id: int):
    """删除报告。raw_camp / raw_port 是原始数据源，不随报告删除。"""
    with get_conn() as conn:
        conn.execute('DELETE FROM reports WHERE id=?', (report_id,))


# ── Config ────────────────────────────────────────────────────────────────────

def get_config(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def set_config(key: str, value):
    with get_conn() as conn:
        conn.execute(
            '''INSERT OR REPLACE INTO config (key, value, updated_at)
               VALUES (?, ?, datetime('now','localtime'))''',
            (key, json.dumps(value, ensure_ascii=False))
        )
