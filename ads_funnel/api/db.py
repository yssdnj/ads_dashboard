"""
db.py — MySQL 数据库操作层
连接配置：ads_funnel/db_config.json
"""

import json
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text, inspect as sa_inspect

_CONFIG_PATH = Path(__file__).parent.parent / 'db_config.json'
_engine = None


def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)


def get_engine():
    global _engine
    if _engine is None:
        cfg = _load_config()
        url = (
            f"mysql+pymysql://{cfg['user']}:{cfg['password']}"
            f"@{cfg['host']}:{cfg['port']}/{cfg['database']}?charset=utf8mb4"
        )
        _engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    return _engine


def init_db():
    """初始化数据库表（幂等）"""
    stmts = [
        """
        CREATE TABLE IF NOT EXISTS reports (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            title       TEXT         NOT NULL,
            country     VARCHAR(50)  NOT NULL DEFAULT '',
            weeks       TEXT         NOT NULL,
            wk_dates    TEXT         NOT NULL,
            data        LONGTEXT     NOT NULL,
            camp_file   VARCHAR(500) DEFAULT '',
            port_file   VARCHAR(500) DEFAULT '',
            created_at  DATETIME     DEFAULT NOW()
        ) CHARACTER SET utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS config (
            `key`      VARCHAR(100) PRIMARY KEY,
            value      LONGTEXT     NOT NULL,
            updated_at DATETIME     DEFAULT NOW()
        ) CHARACTER SET utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS bid_update_log (
            id                   INT AUTO_INCREMENT PRIMARY KEY,
            confirmed_at         DATETIME     NOT NULL DEFAULT NOW(),
            product_target       VARCHAR(200) DEFAULT '',
            bulk_filename        VARCHAR(500) DEFAULT '',
            label_filename       VARCHAR(500) DEFAULT '',
            report_start         VARCHAR(20)  DEFAULT '',
            report_end           VARCHAR(20)  DEFAULT '',
            orders_threshold     INT          DEFAULT 10,
            updated_count        INT          DEFAULT 0,
            log_lines            LONGTEXT,
            target_acos          DOUBLE,
            avg_clicks_per_order DOUBLE,
            country              VARCHAR(50)  DEFAULT ''
        ) CHARACTER SET utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS bid_update_detail (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            log_id       INT          NOT NULL,
            campaign     VARCHAR(500) DEFAULT '',
            ad_group     VARCHAR(500) DEFAULT '',
            strategy     VARCHAR(50)  DEFAULT '',
            top          DOUBLE,
            rest         DOUBLE,
            product_page DOUBLE,
            label        VARCHAR(500) DEFAULT '',
            targeting    VARCHAR(500) DEFAULT '',
            old_bid      DOUBLE,
            new_bid      DOUBLE,
            adj_pct      DOUBLE,
            datetime     VARCHAR(30)  DEFAULT ''
        ) CHARACTER SET utf8mb4
        """,
    ]
    with get_engine().begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))
        # 索引单独创建（MySQL 不支持 CREATE INDEX IF NOT EXISTS）
        try:
            conn.execute(text(
                'CREATE INDEX idx_bid_detail_log_id ON bid_update_detail(log_id)'
            ))
        except Exception:
            pass  # 已存在则忽略
        # 迁移：旧表若缺少 country 列则补加
        result = conn.execute(text('SHOW COLUMNS FROM reports'))
        cols = {row[0] for row in result}
        if 'country' not in cols:
            conn.execute(text(
                "ALTER TABLE reports ADD COLUMN country VARCHAR(50) NOT NULL DEFAULT ''"
            ))
        # 迁移：bid_update_log 补加 target_acos / avg_clicks_per_order 列
        result = conn.execute(text('SHOW COLUMNS FROM bid_update_log'))
        log_cols = {row[0] for row in result}
        if 'target_acos' not in log_cols:
            conn.execute(text(
                'ALTER TABLE bid_update_log ADD COLUMN target_acos DOUBLE'
            ))
        if 'avg_clicks_per_order' not in log_cols:
            conn.execute(text(
                'ALTER TABLE bid_update_log ADD COLUMN avg_clicks_per_order DOUBLE'
            ))
        if 'country' not in log_cols:
            conn.execute(text(
                "ALTER TABLE bid_update_log ADD COLUMN country VARCHAR(50) DEFAULT ''"
            ))


# ── Reports ───────────────────────────────────────────────────────────────────

def list_reports():
    with get_engine().connect() as conn:
        result = conn.execute(text(
            'SELECT id, title, weeks, wk_dates, camp_file, port_file, created_at '
            'FROM reports ORDER BY created_at DESC'
        ))
        rows = result.mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d['weeks']    = json.loads(d['weeks'])
        d['wk_dates'] = json.loads(d['wk_dates'])
        out.append(d)
    return out


def get_report(report_id: int):
    with get_engine().connect() as conn:
        result = conn.execute(
            text('SELECT * FROM reports WHERE id=:id'), {'id': report_id}
        )
        row = result.mappings().first()
    if not row:
        return None
    d = dict(row)
    d['weeks']    = json.loads(d['weeks'])
    d['wk_dates'] = json.loads(d['wk_dates'])
    return d


def save_report(title: str, weeks: list, wk_dates: dict, data: dict,
                camp_file='', port_file='') -> int:
    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                'INSERT INTO reports (title, weeks, wk_dates, data, camp_file, port_file) '
                'VALUES (:title, :weeks, :wk_dates, :data, :camp_file, :port_file)'
            ),
            {
                'title':     title,
                'weeks':     json.dumps(weeks,    ensure_ascii=False),
                'wk_dates':  json.dumps(wk_dates, ensure_ascii=False),
                'data':      json.dumps(data,     ensure_ascii=False, separators=(',', ':')),
                'camp_file': camp_file,
                'port_file': port_file,
            }
        )
        return result.lastrowid


# ── 增量原始数据（领星导出：raw_camp_lx / raw_port_lx）───────────────────────

# raw_camp_lx 唯一主键列：国家 + 广告组合 + 广告活动 + 日期 + 类型
_KEYS_C = ['国家', '广告组合', '广告活动', '日期', '类型']
# raw_port_lx 唯一主键列：国家 + 广告组合 + 日期
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


def _upsert_lx(tbl: str, df_new: pd.DataFrame, keys: list):
    """通用 upsert（领星表）：按唯一键合并新旧数据，新行覆盖旧行"""
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
        df_old = pd.read_sql(f'SELECT * FROM `{tbl}`', engine)
        df_old = df_old.drop(columns=['report_id'], errors='ignore')
        avail_keys = [k for k in keys if k in df_old.columns and k in df_new.columns]
        if avail_keys:
            sep = '\x00'
            old_comp = df_old[avail_keys].astype(str).agg(sep.join, axis=1)
            new_comp = df_new[avail_keys].astype(str).agg(sep.join, axis=1)
            df_old = df_old[~old_comp.isin(set(new_comp))]
        df_merged = pd.concat([df_old, df_new], ignore_index=True)
        df_merged.to_sql(tbl, engine, if_exists='replace', index=False)

    try:
        with engine.begin() as conn:
            conn.execute(text(
                f'CREATE INDEX idx_{tbl}_date ON `{tbl}`(`日期`)'
            ))
    except Exception:
        pass  # 已存在则忽略


def upsert_raw(df_c: pd.DataFrame, df_p: pd.DataFrame):
    """增量写入 raw_camp_lx / raw_port_lx"""
    dc = _prep_raw(df_c)
    dp = _prep_raw(df_p)
    _upsert_lx('raw_camp_lx', dc, _KEYS_C)
    _upsert_lx('raw_port_lx', dp, _KEYS_P)


def get_raw_dfs():
    """
    从 raw_camp_lx / raw_port_lx 读回完整 DataFrame，供 rebuild_from_raw 使用。
    表不存在或为空时返回 (None, None)。
    """
    engine = get_engine()
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    if 'raw_camp_lx' not in tables or 'raw_port_lx' not in tables:
        return None, None
    df_c = pd.read_sql('SELECT * FROM `raw_camp_lx`', engine)
    df_p = pd.read_sql('SELECT * FROM `raw_port_lx`', engine)
    if df_c.empty or df_p.empty:
        return None, None
    for df in (df_c, df_p):
        if '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])
    return df_c, df_p


def rebuild_from_raw() -> list:
    """
    按国家分组，从 raw_camp_lx / raw_port_lx 重建每个国家的报告 JSON。
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

    # 获取所有国家（以 raw_camp_lx 为准）
    countries = df_c[col_c].unique().tolist() if col_c else ['']

    report_ids = []
    for country in countries:
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

        with get_engine().begin() as conn:
            existing = conn.execute(
                text('SELECT id FROM reports WHERE country=:c ORDER BY id DESC LIMIT 1'),
                {'c': str(country)}
            ).first()

            if existing:
                report_id = existing[0]
                conn.execute(
                    text(
                        'UPDATE reports '
                        'SET title=:title, weeks=:weeks, wk_dates=:wk_dates, '
                        '    data=:data, created_at=NOW() '
                        'WHERE id=:id'
                    ),
                    {'title': new_title, 'weeks': weeks_json,
                     'wk_dates': wkdates_json, 'data': data_json, 'id': report_id}
                )
            else:
                result = conn.execute(
                    text(
                        "INSERT INTO reports "
                        "(title, country, weeks, wk_dates, data, camp_file, port_file) "
                        "VALUES (:title, :country, :weeks, :wk_dates, :data, '', '')"
                    ),
                    {'title': new_title, 'country': str(country),
                     'weeks': weeks_json, 'wk_dates': wkdates_json, 'data': data_json}
                )
                report_id = result.lastrowid

        report_ids.append(report_id)
        print(f'[rebuild] {country}  report_id={report_id}  {wk_range}  '
              f'camp={len(dc)}行  port={len(dp)}行')

    return report_ids


def delete_report(report_id: int):
    """删除报告。raw_camp_lx / raw_port_lx 是原始数据源，不随报告删除。"""
    with get_engine().begin() as conn:
        conn.execute(text('DELETE FROM reports WHERE id=:id'), {'id': report_id})


# ── Config ────────────────────────────────────────────────────────────────────

def get_config(key: str, default=None):
    with get_engine().connect() as conn:
        row = conn.execute(
            text('SELECT value FROM config WHERE `key`=:k'), {'k': key}
        ).first()
    return json.loads(row[0]) if row else default


def set_config(key: str, value):
    with get_engine().begin() as conn:
        conn.execute(
            text(
                'INSERT INTO config (`key`, value, updated_at) '
                'VALUES (:k, :v, NOW()) '
                'ON DUPLICATE KEY UPDATE value=:v, updated_at=NOW()'
            ),
            {'k': key, 'v': json.dumps(value, ensure_ascii=False)}
        )


# ── Bid Update Log ────────────────────────────────────────────────────────────

def save_bid_update_log(
    product_target:       str,
    bulk_filename:        str,
    label_filename:       str,
    report_start:         str,
    report_end:           str,
    orders_threshold:     int,
    updated_count:        int,
    log_lines:            list,
    target_acos:          float | None = None,
    avg_clicks_per_order: float | None = None,
    country:              str = '',
) -> int:
    """记录一次已确认上传到亚马逊的竞价更新操作，返回新记录 id。"""
    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                'INSERT INTO bid_update_log '
                '(product_target, bulk_filename, label_filename, '
                ' report_start, report_end, orders_threshold, updated_count, log_lines, '
                ' target_acos, avg_clicks_per_order, country) '
                'VALUES (:pt, :bf, :lf, :rs, :re, :ot, :uc, :ll, :ta, :ac, :co)'
            ),
            {
                'pt': product_target,  'bf': bulk_filename,
                'lf': label_filename,  'rs': report_start,
                're': report_end,      'ot': orders_threshold,
                'uc': updated_count,   'll': json.dumps(log_lines, ensure_ascii=False),
                'ta': target_acos,     'ac': avg_clicks_per_order,
                'co': country,
            }
        )
        return result.lastrowid


def save_bid_update_details(log_id: int, details: list):
    """批量写入竞价更新明细行。"""
    if not details:
        return
    rows = [
        {
            'log_id':      log_id,
            'campaign':    d.get('campaign',     ''),
            'ad_group':    d.get('ad_group',     ''),
            'strategy':    d.get('strategy',     ''),
            'top':         d.get('top'),
            'rest':        d.get('rest'),
            'product_page': d.get('product_page'),
            'label':       d.get('label',        ''),
            'targeting':   d.get('targeting',    ''),
            'old_bid':     d.get('old_bid'),
            'new_bid':     d.get('new_bid'),
            'adj_pct':     d.get('adj_pct'),
            'datetime':    d.get('datetime',     ''),
        }
        for d in details
    ]
    with get_engine().begin() as conn:
        conn.execute(
            text(
                'INSERT INTO bid_update_detail '
                '(log_id, campaign, ad_group, strategy, top, rest, product_page, '
                ' label, targeting, old_bid, new_bid, adj_pct, datetime) '
                'VALUES (:log_id, :campaign, :ad_group, :strategy, :top, :rest, :product_page, '
                '        :label, :targeting, :old_bid, :new_bid, :adj_pct, :datetime)'
            ),
            rows
        )


def list_bid_update_logs(limit: int = 100, country: str = '') -> list:
    """返回最近的竞价更新记录列表（不含 log_lines 详情）。country 非空时只返回该国家记录。"""
    with get_engine().connect() as conn:
        if country:
            result = conn.execute(
                text(
                    'SELECT id, confirmed_at, product_target, bulk_filename, '
                    '       report_start, report_end, orders_threshold, updated_count, '
                    '       target_acos, avg_clicks_per_order, country '
                    'FROM bid_update_log WHERE country = :co ORDER BY id DESC LIMIT :lim'
                ),
                {'lim': limit, 'co': country}
            )
        else:
            result = conn.execute(
                text(
                    'SELECT id, confirmed_at, product_target, bulk_filename, '
                    '       report_start, report_end, orders_threshold, updated_count, '
                    '       target_acos, avg_clicks_per_order, country '
                    'FROM bid_update_log ORDER BY id DESC LIMIT :lim'
                ),
                {'lim': limit}
            )
        return [dict(r) for r in result.mappings().all()]


# ── 分析原始数据（raw_tar / raw_ap）──────────────────────────────────────────

_KEYS_TAR = ['Date', 'Campaign Name', 'Ad Group Name', 'Targeting', 'Match Type']
_KEYS_AP  = ['日期', '广告活动名称', '广告组名称', '广告ASIN']


def _prep_tar(df: pd.DataFrame) -> pd.DataFrame:
    """标准化 tar DataFrame：列名 strip，Date 转 YYYY-MM-DD 字符串"""
    d = df.copy()
    d.columns = [c.strip() for c in d.columns]
    if 'Date' in d.columns:
        d['Date'] = pd.to_datetime(d['Date']).dt.strftime('%Y-%m-%d')
    return d


def _prep_ap_daily(df: pd.DataFrame) -> pd.DataFrame:
    """标准化 ap 每日 DataFrame：列名 strip，日期列转 YYYY-MM-DD 字符串"""
    d = df.copy()
    d.columns = [c.strip() for c in d.columns]
    if '日期' in d.columns:
        d['日期'] = pd.to_datetime(d['日期']).dt.strftime('%Y-%m-%d')
    elif 'Date' in d.columns:
        d = d.rename(columns={'Date': '日期'})
        d['日期'] = pd.to_datetime(d['日期']).dt.strftime('%Y-%m-%d')
    return d


def _upsert_df(engine, tbl: str, df_new: pd.DataFrame, keys: list, date_col: str):
    """通用 upsert：按唯一键合并新旧数据，新行覆盖旧行"""
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
        df_old = pd.read_sql(f'SELECT * FROM `{tbl}`', engine)
        avail_keys = [k for k in keys if k in df_old.columns and k in df_new.columns]
        if avail_keys:
            sep = '\x00'
            old_comp = df_old[avail_keys].astype(str).agg(sep.join, axis=1)
            new_comp = df_new[avail_keys].astype(str).agg(sep.join, axis=1)
            df_old = df_old[~old_comp.isin(set(new_comp))]
        df_merged = pd.concat([df_old, df_new], ignore_index=True)
        df_merged.to_sql(tbl, engine, if_exists='replace', index=False)

    try:
        safe_col = date_col.replace('日期', 'rq')
        with engine.begin() as conn:
            conn.execute(text(
                f'CREATE INDEX idx_{tbl}_{safe_col} ON `{tbl}`(`{date_col}`)'
            ))
    except Exception:
        pass  # 已存在则忽略


def upsert_tar_ap(tar_df: pd.DataFrame, ap_df: pd.DataFrame) -> dict:
    """
    增量写入 raw_tar（投放报告每日）/ raw_ap（推广商品报告每日）。
    返回 {'tar_rows', 'ap_rows', 'tar_min', 'tar_max', 'ap_min', 'ap_max'}
    """
    engine = get_engine()
    tar = _prep_tar(tar_df)
    ap  = _prep_ap_daily(ap_df)

    ap_keys = _KEYS_AP if '广告活动名称' in ap.columns else [
        '日期', 'Campaign Name', 'Ad Group Name', 'Advertised ASIN'
    ]
    _upsert_df(engine, 'raw_tar', tar, _KEYS_TAR, 'Date')
    _upsert_df(engine, 'raw_ap',  ap,  ap_keys,   '日期')

    return {
        'tar_rows': len(tar),
        'ap_rows':  len(ap),
        'tar_min':  tar['Date'].min() if 'Date' in tar.columns else None,
        'tar_max':  tar['Date'].max() if 'Date' in tar.columns else None,
        'ap_min':   ap['日期'].min()  if '日期' in ap.columns  else None,
        'ap_max':   ap['日期'].max()  if '日期' in ap.columns  else None,
    }


def _week_start(date: pd.Timestamp) -> pd.Timestamp:
    """返回该日期所在日历周（周日开始）的周日"""
    days_since_sunday = (date.dayofweek + 1) % 7
    return (date - pd.Timedelta(days=days_since_sunday)).normalize()


def get_tar_ap_for_analysis(n_weeks: int = 6, country_values: set | None = None) -> tuple:
    """
    从 raw_tar / raw_ap 读取最近 n_weeks 个完整日历周（周日开始）的数据。
    country_values 不为 None 时按 Country / 国家/地区 列过滤。
    返回 (tar_df, ap_df, week_sundays) 或 (None, None, []) 若数据不足。
    """
    engine = get_engine()
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    if 'raw_tar' not in tables or 'raw_ap' not in tables:
        return None, None, []

    tar_df = pd.read_sql('SELECT * FROM `raw_tar`', engine)
    ap_df  = pd.read_sql('SELECT * FROM `raw_ap`',  engine)
    if tar_df.empty or ap_df.empty:
        return None, None, []

    if country_values:
        for col in ('Country', '国家/地区'):
            if col in tar_df.columns:
                tar_df = tar_df[tar_df[col].isin(country_values)].copy()
                break
        for col in ('Country', '国家/地区'):
            if col in ap_df.columns:
                ap_df = ap_df[ap_df[col].isin(country_values)].copy()
                break
        if tar_df.empty or ap_df.empty:
            return None, None, []

    tar_df['Date'] = pd.to_datetime(tar_df['Date'])
    ap_df['日期']  = pd.to_datetime(ap_df['日期'])

    max_date    = tar_df['Date'].max()
    base_sunday = _week_start(max_date)
    week_sundays = sorted([
        base_sunday - pd.Timedelta(weeks=i)
        for i in range(n_weeks - 1, -1, -1)
    ])

    cutoff = week_sundays[0]
    tar_df = tar_df[tar_df['Date'] >= cutoff].copy()
    ap_df  = ap_df[ap_df['日期']   >= cutoff].copy()

    return tar_df, ap_df, week_sundays


def get_tar_ap_stats(country_values: set | None = None) -> dict:
    """返回 raw_tar / raw_ap 的数据覆盖范围，供前端展示。
    country_values: 若提供，则只统计 Country 列在该集合内的行。
    """
    engine = get_engine()
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    result = {}
    for tbl, dcol in [('raw_tar', 'Date'), ('raw_ap', '日期')]:
        if tbl not in tables:
            result[tbl] = {'count': 0, 'min_date': None, 'max_date': None}
            continue
        with engine.connect() as conn:
            if country_values:
                placeholders = ','.join([f':c{i}' for i in range(len(country_values))])
                params = {f'c{i}': v for i, v in enumerate(country_values)}
                sql = text(
                    f'SELECT COUNT(*), MIN(`{dcol}`), MAX(`{dcol}`) '
                    f'FROM `{tbl}` WHERE Country IN ({placeholders})'
                )
                row = conn.execute(sql, params).fetchone()
            else:
                row = conn.execute(text(
                    f'SELECT COUNT(*), MIN(`{dcol}`), MAX(`{dcol}`) FROM `{tbl}`'
                )).fetchone()
        result[tbl] = {'count': row[0], 'min_date': str(row[1]) if row[1] else None,
                       'max_date': str(row[2]) if row[2] else None}
    return result


def get_bid_update_details(log_id: int) -> list:
    """返回某次调价记录的所有明细行。"""
    with get_engine().connect() as conn:
        result = conn.execute(
            text(
                'SELECT campaign, ad_group, strategy, top, rest, product_page, '
                '       label, targeting, old_bid, new_bid, adj_pct, datetime '
                'FROM bid_update_detail WHERE log_id=:lid ORDER BY id'
            ),
            {'lid': log_id}
        )
        return [dict(r) for r in result.mappings().all()]
