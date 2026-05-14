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


def rebuild_from_raw(title: str = '') -> int:
    """
    从 raw_camp / raw_port 重新计算完整 JSON，更新（或新建）主报告。
    - 每次导入后调用
    - 服务器重启时调用
    返回 report_id；raw 表为空时返回 None。
    """
    from . import gen_data  # 避免模块顶层循环引用

    df_c, df_p = get_raw_dfs()
    if df_c is None:
        return None

    data      = gen_data.process(df_c, df_p)
    weeks     = data['wk']
    wk_dates  = data.get('wk_dates', {})
    wk_range  = f'{weeks[0]}-{weeks[-1]}' if weeks else ''
    new_title = title.strip() or (f'HOWGO-US {wk_range}' if wk_range else 'HOWGO-US')

    data_json    = json.dumps(data,     ensure_ascii=False, separators=(',', ':'))
    weeks_json   = json.dumps(weeks,    ensure_ascii=False)
    wkdates_json = json.dumps(wk_dates, ensure_ascii=False)

    with get_conn() as conn:
        # 取最新一条报告作为主报告（id 最大）
        existing = conn.execute(
            'SELECT id FROM reports ORDER BY id DESC LIMIT 1'
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
                '''INSERT INTO reports (title, weeks, wk_dates, data, camp_file, port_file)
                   VALUES (?,?,?,?,'','')''',
                (new_title, weeks_json, wkdates_json, data_json)
            )
            report_id = cur.lastrowid

    # 清空并重建细粒度关系表
    with get_conn() as conn:
        for tbl in ('camps', 'ports', 'weekly', 'daily'):
            conn.execute(f'DELETE FROM {tbl} WHERE report_id=?', (report_id,))
    _normalize(report_id, weeks, data)

    print(f'[rebuild] report_id={report_id}  weeks={wk_range}  '
          f'raw_camp={len(df_c)}行  raw_port={len(df_p)}行')
    return report_id


def delete_report(report_id: int):
    """
    删除报告及其派生数据。
    raw_camp / raw_port 是原始数据源，不随报告删除。
    """
    with get_conn() as conn:
        conn.execute('PRAGMA foreign_keys = ON')
        for tbl in ('camps', 'ports', 'weekly', 'daily'):
            try:
                conn.execute(f'DELETE FROM {tbl} WHERE report_id=?', (report_id,))
            except Exception:
                pass
        conn.execute('DELETE FROM reports WHERE id=?', (report_id,))


# ── 数据规范化（打散 JSON → 关系表）────────────────────────────────────────────

def _m(obj, key):
    """安全取指标值"""
    return obj.get(key) if obj else None


def _normalize(report_id: int, weeks: list, data: dict):
    """将 ads_compact JSON 展开写入 camps / ports / weekly / daily"""
    conn = get_conn()
    conn.execute('PRAGMA foreign_keys = ON')

    def ins_weekly(week, scope, prod, cat, m):
        if not m:
            return
        conn.execute(
            '''INSERT INTO weekly(report_id,week,scope,prod,cat,
               sp,sl,cl,im,orders,ac,ro,ct,cv,cp) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (report_id, week, scope, prod, cat,
             _m(m,'sp'), _m(m,'sl'), _m(m,'cl'), _m(m,'im'),
             _m(m,'or') or _m(m,'or_'),
             _m(m,'ac'), _m(m,'ro'), _m(m,'ct'), _m(m,'cv'), _m(m,'cp'))
        )

    def ins_daily(date, scope, prod, cat, m):
        if not m:
            return
        conn.execute(
            '''INSERT INTO daily(report_id,date,scope,prod,cat,
               sp,sl,cl,im,orders) VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (report_id, date, scope, prod, cat,
             _m(m,'sp'), _m(m,'sl'), _m(m,'cl'), _m(m,'im'),
             _m(m,'or') or _m(m,'or_'))
        )

    with conn:
        # ── 广告活动 ──────────────────────────────────────────────────────────
        for c in data.get('camps', []):
            conn.execute(
                '''INSERT INTO camps(report_id,name,portfolio,prod,cat,type,status,
                   sp,sl,cl,im,orders,ac,ro,ct,cv,cp) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (report_id, c.get('n'), c.get('po'), c.get('p'), c.get('c'),
                 c.get('ty'), c.get('st'),
                 c.get('sp'), c.get('sl'), c.get('cl'), c.get('im'),
                 c.get('or') or c.get('or_'),
                 c.get('ac'), c.get('ro'), c.get('ct'), c.get('cv'), c.get('cp'))
            )

        # ── 广告组合 ──────────────────────────────────────────────────────────
        for p in data.get('ports', []):
            t = p.get('t') or {}
            conn.execute(
                '''INSERT INTO ports(report_id,name,prod,cat,
                   sp,sl,cl,im,orders,ac,ro,ct,cv,cp) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (report_id, p.get('n'), p.get('p'), p.get('c'),
                 _m(t,'sp'), _m(t,'sl'), _m(t,'cl'), _m(t,'im'),
                 _m(t,'or') or _m(t,'or_'),
                 _m(t,'ac'), _m(t,'ro'), _m(t,'ct'), _m(t,'cv'), _m(t,'cp'))
            )

        # ── 周汇总：总览 ──────────────────────────────────────────────────────
        ov = data.get('ov', {})
        ins_weekly('total', 'ov', None, None, ov.get('t'))
        for i, wk in enumerate(weeks):
            ins_weekly(wk, 'ov', None, None,
                       ov['w'][i] if ov.get('w') and i < len(ov['w']) else None)

        # ── 周汇总：按商品 ────────────────────────────────────────────────────
        for prod, pdata in data.get('prods', {}).items():
            ins_weekly('total', 'prod', prod, None, pdata.get('t'))
            for i, wk in enumerate(weeks):
                ins_weekly(wk, 'prod', prod, None,
                           pdata['w'][i] if pdata.get('w') and i < len(pdata['w']) else None)

        # ── 周汇总：按类别 ────────────────────────────────────────────────────
        for cat, cdata in data.get('cats', {}).items():
            ins_weekly('total', 'cat', None, cat, cdata.get('t'))
            for i, wk in enumerate(weeks):
                ins_weekly(wk, 'cat', None, cat,
                           cdata['w'][i] if cdata.get('w') and i < len(cdata['w']) else None)

        # ── 周汇总：商品×类别 ─────────────────────────────────────────────────
        for prod, cats in data.get('prod_cats', {}).items():
            for cat, pcdata in cats.items():
                ins_weekly('total', 'prod_cat', prod, cat, pcdata.get('t'))
                for i, wk in enumerate(weeks):
                    ins_weekly(wk, 'prod_cat', prod, cat,
                               pcdata['w'][i] if pcdata.get('w') and i < len(pcdata['w']) else None)

        # ── 日明细：总览 ──────────────────────────────────────────────────────
        for date, m in data.get('daily_ov', {}).items():
            ins_daily(date, 'ov', None, None, m)

        # ── 日明细：按商品 ────────────────────────────────────────────────────
        for prod, days in data.get('daily_prod', {}).items():
            for date, m in days.items():
                ins_daily(date, 'prod', prod, None, m)

        # ── 日明细：按类别 ────────────────────────────────────────────────────
        for cat, days in data.get('daily_cat', {}).items():
            for date, m in days.items():
                ins_daily(date, 'cat', None, cat, m)

        # ── 日明细：商品×类别 ─────────────────────────────────────────────────
        for prod, cats in data.get('daily_prod_cat', {}).items():
            for cat, days in cats.items():
                for date, m in days.items():
                    ins_daily(date, 'prod_cat', prod, cat, m)

        # ── 日明细：广告组合 ──────────────────────────────────────────────────
        for port, days in data.get('daily_port', {}).items():
            for date, m in days.items():
                ins_daily(date, 'port', port, None, m)

    conn.close()


def migrate_existing():
    """将已存在报告的 JSON 补写到细粒度表（首次升级用）"""
    conn = get_conn()
    existing_ids = {r[0] for r in conn.execute('SELECT DISTINCT report_id FROM camps').fetchall()}
    reports = conn.execute('SELECT id, weeks, data FROM reports').fetchall()
    conn.close()
    for row in reports:
        rid = row[0]
        if rid in existing_ids:
            continue
        weeks = json.loads(row[1])
        data  = json.loads(row[2])
        _normalize(rid, weeks, data)


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
