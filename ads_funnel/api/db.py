"""
db.py — MySQL 数据库操作层
连接配置：ads_funnel/db_config.json
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime
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
    """日期列统一转为 YYYY-MM-DD 字符串，非数值占位符替换为 None，去掉旧 report_id 列"""
    d = df.copy()
    if '日期' in d.columns:
        try:
            d['日期'] = pd.to_datetime(d['日期']).dt.strftime('%Y-%m-%d')
        except Exception:
            d['日期'] = d['日期'].astype(str)
    d = d.drop(columns=['report_id'], errors='ignore')
    # 将非数值占位符替换为 None，避免写入 double/bigint 列时报截断错误
    # 领星导出常见占位符：'--', '有花费无点击', '有花费无销售额', '有花费无订单' 等
    d = d.replace({'--': None, '有花费无点击': None, '有花费无销售额': None,
                   '有花费无订单': None, '有花费无数据': None}, regex=False)
    return d


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
                text(f'SELECT * FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
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
        import traceback
        traceback.print_exc()
        return {}


def get_campaign_stats_by_date_range(
    country: str,
    date_from: str,
    date_to: str,
) -> dict[str, dict]:
    """
    查询 raw_camp_lx（领星广告活动每日明细），按广告活动聚合指定日期范围内的指标。
    country: 国家代码，如 'UK'（与 raw_camp_lx.国家 字段一致）。
    返回 {campaign_name: {sp, sl, cl, im, or_, ac, ro, cp, ct, cv}}
    DB 异常时返回 {}，不中断调用方。
    """
    try:
        with get_engine().connect() as conn:
            result = conn.execute(
                text(
                    'SELECT `广告活动`,'
                    '  SUM(`花费`) AS sp,'
                    '  SUM(`广告销售额`) AS sl,'
                    '  SUM(`点击`) AS cl,'
                    '  SUM(`曝光量`) AS im,'
                    '  SUM(`广告订单`) AS or_,'
                    '  SUM(`花费`) / NULLIF(SUM(`广告销售额`), 0) * 100 AS ac,'
                    '  SUM(`广告销售额`) / NULLIF(SUM(`花费`), 0) AS ro,'
                    '  SUM(`花费`) / NULLIF(SUM(`点击`), 0) AS cp,'
                    '  SUM(`点击`) / NULLIF(SUM(`曝光量`), 0) * 100 AS ct,'
                    '  SUM(`广告订单`) / NULLIF(SUM(`点击`), 0) * 100 AS cv'
                    ' FROM `raw_camp_lx`'
                    ' WHERE `国家` = :country'
                    '   AND `日期` >= :date_from'
                    '   AND `日期` <= :date_to'
                    ' GROUP BY `广告活动`'
                ),
                {'country': country, 'date_from': date_from, 'date_to': date_to},
            )
            out: dict = {}
            for row in result.mappings():
                name = str(row['广告活动'])
                out[name] = {
                    'sp':  float(row['sp']  or 0),
                    'sl':  float(row['sl']  or 0),
                    'cl':  int(row['cl']    or 0),
                    'im':  int(row['im']    or 0),
                    'or_': int(row['or_']   or 0),
                    'ac':  float(row['ac'])  if row['ac']  is not None else None,
                    'ro':  float(row['ro'])  if row['ro']  is not None else None,
                    'cp':  float(row['cp'])  if row['cp']  is not None else None,
                    'ct':  float(row['ct'])  if row['ct']  is not None else None,
                    'cv':  float(row['cv'])  if row['cv']  is not None else None,
                }
            return out
    except Exception:
        import traceback
        traceback.print_exc()
        return {}


def get_campaign_trend(
    campaign: str,
    country: str,
    date_from: str,
    date_to: str,
) -> dict:
    """
    返回指定广告活动在日期范围内的周趋势、日趋势和调价事件。
    查询 raw_camp_lx（领星广告活动每日明细）获取指标数据。
    country: 国家代码如 'UK'，与 raw_camp_lx.国家 和 bid_update_log.country 一致。
    weekly: 在 Python 端按 ISO week 聚合日数据。
    查询失败返回 {'weekly': [], 'daily': [], 'bid_events': []}。
    """
    empty = {'weekly': [], 'daily': [], 'bid_events': []}
    try:
        with get_engine().connect() as conn:
            # 1. 日趋势（来自 raw_camp_lx）
            daily_rows = conn.execute(
                text(
                    'SELECT `日期` AS date,'
                    '  SUM(`花费`) AS sp,'
                    '  SUM(`广告销售额`) AS sl,'
                    '  SUM(`点击`) AS cl,'
                    '  SUM(`曝光量`) AS im,'
                    '  SUM(`广告订单`) AS or_'
                    ' FROM `raw_camp_lx`'
                    ' WHERE `广告活动` = :campaign'
                    '   AND `国家` = :country'
                    '   AND `日期` BETWEEN :date_from AND :date_to'
                    ' GROUP BY `日期`'
                    ' ORDER BY `日期`'
                ),
                {'campaign': campaign, 'country': country,
                 'date_from': date_from, 'date_to': date_to},
            ).mappings().all()

            # 2. 调价事件
            bid_rows = conn.execute(
                text(
                    'SELECT DATE(l.confirmed_at) AS event_date,'
                    '  COUNT(DISTINCT d.targeting) AS tgt_count,'
                    '  SUM(CASE WHEN d.new_bid > d.old_bid THEN 1 ELSE 0 END) AS up_count,'
                    '  SUM(CASE WHEN d.new_bid < d.old_bid THEN 1 ELSE 0 END) AS dn_count,'
                    '  ROUND(AVG(CASE WHEN d.new_bid > d.old_bid THEN d.adj_pct END), 1) AS avg_up_pct,'
                    '  ROUND(AVG(CASE WHEN d.new_bid < d.old_bid THEN d.adj_pct END), 1) AS avg_dn_pct'
                    ' FROM bid_update_detail d'
                    ' JOIN bid_update_log l ON d.log_id = l.id'
                    ' WHERE d.campaign = :campaign'
                    '   AND l.country  = :country'
                    ' GROUP BY DATE(l.confirmed_at)'
                    ' ORDER BY event_date'
                ),
                {'campaign': campaign, 'country': country},
            ).mappings().all()

        # 3. 构建 daily 列表
        daily = []
        for r in daily_rows:
            sp  = float(r['sp']  or 0)
            sl  = float(r['sl']  or 0)
            cl  = int(r['cl']    or 0)
            im  = int(r['im']    or 0)
            or_ = int(r['or_']   or 0)
            daily.append({
                'date': str(r['date']),
                'sp': sp, 'sl': sl, 'cl': cl, 'im': im, 'or_': or_,
                'ac': round(sp / sl * 100, 2) if sl > 0 else None,
                'ro': round(sl / sp, 3)        if sp > 0 else None,
                'cp': round(sp / cl, 3)        if cl > 0 else None,
                'ct': round(cl / im * 100, 4)  if im > 0 else None,
                'cv': round(or_ / cl * 100, 4) if cl > 0 else None,
            })

        # 4. Python 端按 ISO week 聚合为周数据
        from datetime import datetime as _dt
        wk_buckets: dict = {}
        for d in daily:
            date_obj = _dt.strptime(d['date'], '%Y-%m-%d').date()
            wk = f'W{date_obj.isocalendar().week}'
            if wk not in wk_buckets:
                wk_buckets[wk] = {'sp': 0, 'sl': 0, 'cl': 0, 'im': 0, 'or_': 0}
            b = wk_buckets[wk]
            b['sp']  += d['sp'];  b['sl']  += d['sl']
            b['cl']  += d['cl'];  b['im']  += d['im']
            b['or_'] += d['or_']

        weekly = []
        for wk, b in sorted(wk_buckets.items(), key=lambda x: int(x[0][1:])):
            sp, sl, cl, im, or_ = b['sp'], b['sl'], b['cl'], b['im'], b['or_']
            weekly.append({
                'wk': wk, 'sp': sp, 'sl': sl, 'cl': cl, 'im': im, 'or_': or_,
                'ac': round(sp / sl * 100, 2) if sl > 0 else None,
                'ro': round(sl / sp, 3)        if sp > 0 else None,
                'cp': round(sp / cl, 3)        if cl > 0 else None,
                'ct': round(cl / im * 100, 4)  if im > 0 else None,
                'cv': round(or_ / cl * 100, 4) if cl > 0 else None,
            })

        # 5. 调价事件列表
        bid_events = []
        for r in bid_rows:
            ev_date = r['event_date']
            bid_events.append({
                'date':        str(ev_date) if ev_date else '',
                'tgt_count':   int(r['tgt_count'] or 0),
                'up_count':    int(r['up_count']  or 0),
                'dn_count':    int(r['dn_count']  or 0),
                'avg_up_pct':  float(r['avg_up_pct']) if r['avg_up_pct'] is not None else None,
                'avg_dn_pct':  float(r['avg_dn_pct']) if r['avg_dn_pct'] is not None else None,
            })

        return {'weekly': weekly, 'daily': daily, 'bid_events': bid_events}

    except Exception:
        import traceback
        traceback.print_exc()
        return empty


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
                text(f'SELECT * FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
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
    优化：国家和日期过滤下推到 SQL，Python 只接收实际需要的行。
    """
    engine = get_engine()
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    if 'raw_tar' not in tables or 'raw_ap' not in tables:
        return None, None, []

    # 构建国家过滤片段（两表均用 Country 列）
    country_clause_max = ''   # MAX 查询用 WHERE
    country_clause_tar = ''   # read_sql 用 AND（已有 WHERE date >= :cutoff）
    country_clause_ap  = ''
    country_params: dict = {}
    if country_values is not None:
        ph = ', '.join([f':c{i}' for i in range(len(country_values))])
        country_params     = {f'c{i}': v for i, v in enumerate(country_values)}
        country_clause_max = f' WHERE `Country` IN ({ph})'
        country_clause_tar = f' AND `Country` IN ({ph})'
        country_clause_ap  = f' AND `Country` IN ({ph})'

    # 1. 取 raw_tar 最新日期（带国家过滤，确保 cutoff 与数据范围一致）
    with engine.connect() as conn:
        max_date_val = conn.execute(
            text(f'SELECT MAX(`Date`) FROM `raw_tar`{country_clause_max}'),
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
    query_params = {**country_params, 'cutoff': cutoff_str}

    tar_df = pd.read_sql(
        text(f'SELECT * FROM `raw_tar` WHERE `Date` >= :cutoff{country_clause_tar}'),
        engine, params=query_params
    )
    ap_df = pd.read_sql(
        text(f'SELECT * FROM `raw_ap` WHERE `日期` >= :cutoff{country_clause_ap}'),
        engine, params=query_params
    )

    if tar_df.empty or ap_df.empty:
        return None, None, []

    tar_df['Date'] = pd.to_datetime(tar_df['Date'])
    ap_df['日期']  = pd.to_datetime(ap_df['日期'])

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
