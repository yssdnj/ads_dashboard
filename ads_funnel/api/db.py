"""
db.py — MySQL 数据库操作层
连接配置：ads_funnel/db_config.json
"""

import json
import re
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
            up_orders_threshold  INT          DEFAULT 2,
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
        """
        CREATE TABLE IF NOT EXISTS campaign_placement_pct (
            campaign_name     VARCHAR(300) NOT NULL,
            campaign_id       VARCHAR(50)  DEFAULT '',
            portfolio_id      VARCHAR(50)  DEFAULT '',
            bidding_strategy  VARCHAR(20)  DEFAULT '',
            top_pct           INT          DEFAULT 0,
            rest_pct          INT          DEFAULT 0,
            pp_pct            INT          DEFAULT 0,
            country           VARCHAR(10)  NOT NULL,
            updated_at        DATETIME     DEFAULT NOW(),
            PRIMARY KEY (campaign_name, country)
        ) CHARACTER SET utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS raw_placement (
            date           DATE         NOT NULL,
            campaign_name  VARCHAR(300) NOT NULL,
            portfolio_name VARCHAR(300) DEFAULT '',
            placement      VARCHAR(100) NOT NULL,
            impressions    INT          DEFAULT 0,
            clicks         INT          DEFAULT 0,
            spend          DECIMAL(12,4) DEFAULT 0,
            sales          DECIMAL(12,4) DEFAULT 0,
            orders         INT          DEFAULT 0,
            units          INT          DEFAULT 0,
            country        VARCHAR(10)  NOT NULL,
            PRIMARY KEY (date, campaign_name, placement, country)
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
        if 'up_orders_threshold' not in log_cols:
            conn.execute(text(
                'ALTER TABLE bid_update_log ADD COLUMN up_orders_threshold INT DEFAULT 2'
            ))
        ensure_report_week_cols(conn)
        ensure_raw_metadata_cols(conn)


# ── Reports ───────────────────────────────────────────────────────────────────

def ensure_report_week_cols(conn=None):
    """Ensure reports can store one country/week snapshot per row."""
    engine = get_engine()

    def _do(c):
        cols = {row[0] for row in c.execute(text('SHOW COLUMNS FROM reports')).fetchall()}
        if 'week_key' not in cols:
            c.execute(text("ALTER TABLE reports ADD COLUMN week_key VARCHAR(10) NOT NULL DEFAULT ''"))
        if 'week_start' not in cols:
            c.execute(text('ALTER TABLE reports ADD COLUMN week_start DATE NULL'))
        if 'week_end' not in cols:
            c.execute(text('ALTER TABLE reports ADD COLUMN week_end DATE NULL'))
        try:
            c.execute(text(
                'CREATE UNIQUE INDEX uq_reports_country_week '
                'ON reports(country, week_key)'
            ))
        except Exception:
            pass
        try:
            c.execute(text(
                'CREATE INDEX idx_reports_country_week_start '
                'ON reports(country, week_start)'
            ))
        except Exception:
            pass

    if conn is not None:
        _do(conn)
    else:
        with engine.begin() as c:
            _do(c)


def _report_week_cols_exist() -> bool:
    with get_engine().connect() as conn:
        cols = {row[0] for row in conn.execute(text('SHOW COLUMNS FROM reports')).fetchall()}
    return {'week_key', 'week_start', 'week_end'}.issubset(cols)


def _week_sort_key(w):
    year, week = str(w).split('W', 1)
    return int(year), int(week)


def _format_country_title(country, weeks):
    wk_range = f'{weeks[0]}-{weeks[-1]}' if weeks else ''
    return f'{country} {wk_range}' if wk_range else str(country)


def list_reports():
    if not _report_week_cols_exist():
        with get_engine().connect() as conn:
            result = conn.execute(text(
                'SELECT id, title, country, weeks, wk_dates, camp_file, port_file, created_at '
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

    with get_engine().connect() as conn:
        result = conn.execute(text(
            """
            SELECT country, MAX(id) AS id, MAX(created_at) AS created_at
            FROM reports
            WHERE week_key <> ''
            GROUP BY country
            ORDER BY MAX(created_at) DESC
            """
        ))
        rows = result.mappings().all()

    out = []
    for r in rows:
        summary = _get_country_report_summary(r['country'])
        if summary:
            out.append(summary)
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
    if d.get('week_key'):
        return get_country_report(d.get('country', ''))
    d['weeks']    = json.loads(d['weeks'])
    d['wk_dates'] = json.loads(d['wk_dates'])
    return d


def _get_country_week_rows(country: str):
    with get_engine().connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT *
                FROM reports
                WHERE country=:country AND week_key <> ''
                ORDER BY week_start ASC, week_key ASC
                """
            ),
            {'country': str(country)}
        )
        return [dict(r) for r in result.mappings().all()]


def _get_country_report_summary(country: str):
    rows = _get_country_week_rows(country)
    if not rows:
        return None
    weeks = []
    wk_dates = {}
    for row in rows:
        row_weeks = json.loads(row['weeks'])
        row_wk_dates = json.loads(row['wk_dates'])
        for week in row_weeks:
            if week not in weeks:
                weeks.append(week)
        wk_dates.update(row_wk_dates)
    weeks = sorted(weeks, key=_week_sort_key)
    latest = max(rows, key=lambda r: r['id'])
    return {
        'id': latest['id'],
        'title': _format_country_title(country, weeks),
        'country': country,
        'weeks': weeks,
        'wk_dates': wk_dates,
        'camp_file': latest.get('camp_file') or '',
        'port_file': latest.get('port_file') or '',
        'created_at': latest.get('created_at'),
    }


def get_country_report(country: str):
    rows = _get_country_week_rows(country)
    if not rows:
        return None
    weekly_data = [json.loads(r['data']) for r in rows]
    data = _merge_ads_compact(weekly_data)
    latest = max(rows, key=lambda r: r['id'])
    weeks = data.get('wk', [])
    return {
        'id': latest['id'],
        'title': _format_country_title(country, weeks),
        'country': country,
        'weeks': weeks,
        'wk_dates': data.get('wk_dates', {}),
        'data': json.dumps(data, ensure_ascii=False, separators=(',', ':')),
        'camp_file': latest.get('camp_file') or '',
        'port_file': latest.get('port_file') or '',
        'created_at': latest.get('created_at'),
    }


def _sum_metrics(items):
    totals = {'sp': 0.0, 'sl': 0.0, 'cl': 0, 'im': 0, 'or_': 0}
    for item in items:
        if not item:
            continue
        totals['sp'] += float(item.get('sp') or 0)
        totals['sl'] += float(item.get('sl') or 0)
        totals['cl'] += int(item.get('cl') or 0)
        totals['im'] += int(item.get('im') or 0)
        totals['or_'] += int(item.get('or_') or 0)
    sp, sl, cl, im, or_ = totals['sp'], totals['sl'], totals['cl'], totals['im'], totals['or_']
    return {
        'sp': round(sp, 4),
        'sl': round(sl, 4),
        'cl': cl,
        'im': im,
        'or_': or_,
        'ac': round(sp / sl * 100, 4) if sl > 0 else None,
        'ro': round(sl / sp, 4) if sp > 0 else None,
        'ct': round(cl / im * 100, 4) if im > 0 else None,
        'cv': round(or_ / cl * 100, 4) if cl > 0 else None,
        'cp': round(sp / cl, 4) if cl > 0 else None,
    }


def _camp_status(metric):
    if metric['or_'] > 0:
        return 'converting'
    if metric['cl'] > 0:
        return 'click_no_order'
    if metric['im'] > 0:
        return 'imp_only'
    return 'no_imp'


def _merge_daily_maps(target, source):
    for key, value in (source or {}).items():
        if isinstance(value, dict) and all(k in value for k in ('sp', 'sl', 'cl', 'im', 'or_')):
            target[key] = value
        elif isinstance(value, dict):
            child = target.setdefault(key, {})
            _merge_daily_maps(child, value)
        else:
            target[key] = value


def _merge_section_map(weekly_data, section, weeks):
    week_index = {week: idx for idx, week in enumerate(weeks)}
    merged = {}
    for data in weekly_data:
        week = data.get('wk', [''])[0] if data.get('wk') else ''
        idx = week_index.get(week)
        if idx is None:
            continue
        for name, item in (data.get(section) or {}).items():
            bucket = merged.setdefault(name, {'w': [None] * len(weeks)})
            bucket['w'][idx] = (item.get('w') or [item.get('t') or None])[0]
    for bucket in merged.values():
        bucket['t'] = _sum_metrics(bucket['w'])
    return merged


def _merge_prod_cats(weekly_data, weeks):
    week_index = {week: idx for idx, week in enumerate(weeks)}
    merged = {}
    for data in weekly_data:
        week = data.get('wk', [''])[0] if data.get('wk') else ''
        idx = week_index.get(week)
        if idx is None:
            continue
        for prod, cats in (data.get('prod_cats') or {}).items():
            prod_bucket = merged.setdefault(prod, {})
            for cat, item in (cats or {}).items():
                bucket = prod_bucket.setdefault(cat, {'w': [None] * len(weeks)})
                bucket['w'][idx] = (item.get('w') or [item.get('t') or None])[0]
    for cats in merged.values():
        for bucket in cats.values():
            bucket['t'] = _sum_metrics(bucket['w'])
    return merged


def _merge_list_section(weekly_data, section, key_field, weeks):
    week_index = {week: idx for idx, week in enumerate(weeks)}
    merged = {}
    for data in weekly_data:
        week = data.get('wk', [''])[0] if data.get('wk') else ''
        idx = week_index.get(week)
        if idx is None:
            continue
        for item in data.get(section) or []:
            key = item.get(key_field)
            if key is None:
                continue
            bucket = merged.setdefault(key, {'base': {}, 'metrics': [None] * len(weeks)})
            bucket['base'].update({k: v for k, v in item.items() if k not in ('t', 'wa')})
            metric = item.get('t') or {k: item.get(k) for k in ('sp', 'sl', 'cl', 'im', 'or_')}
            bucket['metrics'][idx] = metric

    out = []
    for bucket in merged.values():
        total = _sum_metrics(bucket['metrics'])
        row = dict(bucket['base'])
        row['t'] = total
        row['wa'] = [m.get('ac') if m else None for m in bucket['metrics']]
        if section == 'camps':
            row.update(total)
            row['st'] = _camp_status(total)
        out.append(row)
    out.sort(key=lambda x: (x.get('t') or x).get('sp', 0) if isinstance(x.get('t'), dict) else x.get('sp', 0), reverse=True)
    return out


def _merge_ads_compact(weekly_data):
    weeks = []
    wk_dates = {}
    wk_iso_dates = {}
    for data in weekly_data:
        for week in data.get('wk', []):
            if week not in weeks:
                weeks.append(week)
        wk_dates.update(data.get('wk_dates') or {})
        wk_iso_dates.update(data.get('wk_iso_dates') or {})
    weeks = sorted(weeks, key=_week_sort_key)
    week_index = {week: idx for idx, week in enumerate(weeks)}

    ov_w = [None] * len(weeks)
    for data in weekly_data:
        week = data.get('wk', [''])[0] if data.get('wk') else ''
        idx = week_index.get(week)
        if idx is not None:
            ov_w[idx] = (data.get('ov') or {}).get('w', [(data.get('ov') or {}).get('t')])[0]

    merged_daily = {}
    for data in weekly_data:
        _merge_daily_maps(merged_daily, data.get('daily_ov') or {})

    daily_prod, daily_cat, daily_prod_cat, daily_port = {}, {}, {}, {}
    for data in weekly_data:
        _merge_daily_maps(daily_prod, data.get('daily_prod') or {})
        _merge_daily_maps(daily_cat, data.get('daily_cat') or {})
        _merge_daily_maps(daily_prod_cat, data.get('daily_prod_cat') or {})
        _merge_daily_maps(daily_port, data.get('daily_port') or {})

    return {
        'wk': weeks,
        'wk_dates': {w: wk_dates[w] for w in weeks if w in wk_dates},
        'wk_iso_dates': {w: wk_iso_dates[w] for w in weeks if w in wk_iso_dates},
        'ov': {'t': _sum_metrics(ov_w), 'w': ov_w},
        'cats': _merge_section_map(weekly_data, 'cats', weeks),
        'prods': _merge_section_map(weekly_data, 'prods', weeks),
        'ports': _merge_list_section(weekly_data, 'ports', 'n', weeks),
        'camps': _merge_list_section(weekly_data, 'camps', 'n', weeks),
        'prod_cats': _merge_prod_cats(weekly_data, weeks),
        'daily_ov': dict(sorted(merged_daily.items())),
        'daily_prod': daily_prod,
        'daily_cat': daily_cat,
        'daily_prod_cat': daily_prod_cat,
        'daily_port': daily_port,
    }


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

# raw_camp_lx 唯一主键列：国家 + 广告活动（归一化名称）+ 日期 + 类型
# 去掉广告组合：活动可能在组合间移动，名称是更稳定的标识
_KEYS_C = ['国家', '广告活动', '日期', '类型']
# raw_port_lx 唯一主键列：国家 + 广告组合 + 日期
_KEYS_P = ['国家', '广告组合', '日期']

# ASIN 格式：B0 开头共 10 个字符（B0 + 8位字母数字）
_ASIN_RE = re.compile(r'^B0[A-Z0-9]{8}$')


def ensure_raw_metadata_cols(conn=None):
    """Ensure Lingxing raw tables contain Amazon Ads metadata columns.

    The raw Lingxing tables are created dynamically from uploaded Excel files,
    so this migration is intentionally idempotent and skips tables that do not
    exist yet.
    """
    _tables = {
        'raw_camp_lx': ['campaign_id', 'portfolio_id'],
        'raw_port_lx': ['portfolio_id'],
    }
    engine = get_engine()

    def _do(c):
        existing_tables = {r[0] for r in c.execute(text('SHOW TABLES')).fetchall()}
        for tbl, need_cols in _tables.items():
            if tbl not in existing_tables:
                continue
            have = {r[0] for r in c.execute(text(f'SHOW COLUMNS FROM `{tbl}`')).fetchall()}
            for col in need_cols:
                if col not in have:
                    c.execute(text(
                        f"ALTER TABLE `{tbl}` ADD COLUMN `{col}` VARCHAR(50) DEFAULT ''"
                    ))
                try:
                    c.execute(text(
                        f'CREATE INDEX idx_{tbl}_{col} ON `{tbl}`(`{col}`)'
                    ))
                except Exception:
                    pass

    if conn is not None:
        _do(conn)
    else:
        with engine.begin() as c:
            _do(c)


def _build_campaign_metadata_lookup(campaigns: list[dict]) -> dict[str, dict]:
    if not campaigns:
        return {}
    names = pd.Series([c.get('campaign_name', '') for c in campaigns], dtype=str)
    norms = _normalize_camp_name(names)
    lookup = {}
    for norm, row in zip(norms, campaigns):
        norm_s = str(norm or '').strip()
        if norm_s:
            lookup[norm_s] = row
    return lookup


def _build_portfolio_metadata_lookup(portfolios: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    by_name = {}
    by_id = {}
    for p in portfolios:
        pid = str(p.get('portfolio_id') or '').strip()
        name = str(p.get('portfolio_name') or '').strip()
        if pid and name:
            by_name[name] = pid
            by_id[pid] = name
    return by_name, by_id


def sync_amazon_metadata(country: str, campaigns: list[dict], portfolios: list[dict]) -> dict:
    """Sync Amazon Ads campaign/portfolio metadata into raw tables and placement config."""
    ensure_raw_metadata_cols()
    camp_lookup = _build_campaign_metadata_lookup(campaigns)
    camp_by_id = {
        str(c.get('campaign_id') or '').strip(): c
        for c in campaigns if str(c.get('campaign_id') or '').strip()
    }
    port_by_name, port_by_id = _build_portfolio_metadata_lookup(portfolios)

    stats = {
        'campaigns_fetched': len(campaigns),
        'portfolios_fetched': len(portfolios),
        'raw_camp_ids_updated': 0,
        'raw_camp_names_updated': 0,
        'raw_port_ids_updated': 0,
        'raw_port_names_updated': 0,
        'placement_pct_updated': 0,
    }

    engine = get_engine()
    with engine.begin() as conn:
        tables = {r[0] for r in conn.execute(text('SHOW TABLES')).fetchall()}

        if 'raw_camp_lx' in tables:
            rows = conn.execute(text(
                'SELECT DISTINCT `广告活动`, campaign_id FROM raw_camp_lx WHERE `国家`=:country'
            ), {'country': country}).fetchall()
            for name, existing_cid in rows:
                name_s = str(name or '')
                cid_s = str(existing_cid or '').strip()
                if cid_s and cid_s in camp_by_id:
                    latest = str(camp_by_id[cid_s].get('campaign_name') or '').strip()
                    if latest and latest != name_s:
                        result = conn.execute(text(
                            'UPDATE raw_camp_lx SET `广告活动`=:new_name '
                            'WHERE `国家`=:country AND campaign_id=:cid'
                        ), {'new_name': latest, 'country': country, 'cid': cid_s})
                        stats['raw_camp_names_updated'] += int(result.rowcount or 0)
                    continue

                norm = _normalize_camp_name(pd.Series([name_s], dtype=str)).iloc[0]
                match = camp_lookup.get(str(norm))
                if match:
                    result = conn.execute(text(
                        'UPDATE raw_camp_lx SET campaign_id=:cid, portfolio_id=:pid '
                        'WHERE `国家`=:country AND `广告活动`=:name '
                        "AND (campaign_id IS NULL OR campaign_id='')"
                    ), {
                        'cid': match.get('campaign_id', ''),
                        'pid': match.get('portfolio_id', ''),
                        'country': country,
                        'name': name_s,
                    })
                    stats['raw_camp_ids_updated'] += int(result.rowcount or 0)

        if 'raw_port_lx' in tables:
            rows = conn.execute(text(
                'SELECT DISTINCT `广告组合`, portfolio_id FROM raw_port_lx WHERE `国家`=:country'
            ), {'country': country}).fetchall()
            for name, existing_pid in rows:
                name_s = str(name or '')
                pid_s = str(existing_pid or '').strip()
                if pid_s and pid_s in port_by_id:
                    latest = port_by_id[pid_s]
                    if latest and latest != name_s:
                        result = conn.execute(text(
                            'UPDATE raw_port_lx SET `广告组合`=:new_name '
                            'WHERE `国家`=:country AND portfolio_id=:pid'
                        ), {'new_name': latest, 'country': country, 'pid': pid_s})
                        stats['raw_port_names_updated'] += int(result.rowcount or 0)
                    continue

                pid = port_by_name.get(name_s)
                if pid:
                    result = conn.execute(text(
                        'UPDATE raw_port_lx SET portfolio_id=:pid '
                        'WHERE `国家`=:country AND `广告组合`=:name '
                        "AND (portfolio_id IS NULL OR portfolio_id='')"
                    ), {'pid': pid, 'country': country, 'name': name_s})
                    stats['raw_port_ids_updated'] += int(result.rowcount or 0)

    placement_rows = [
        {
            'campaign_name': c.get('campaign_name', ''),
            'campaign_id': c.get('campaign_id', ''),
            'portfolio_id': c.get('portfolio_id', ''),
            'bidding_strategy': c.get('bidding_strategy', ''),
            'top_pct': c.get('top_pct', 0),
            'rest_pct': c.get('rest_pct', 0),
            'pp_pct': c.get('pp_pct', 0),
        }
        for c in campaigns
    ]
    upsert_placement_pct(placement_rows, country)
    stats['placement_pct_updated'] = len(placement_rows)
    return stats


# ── Placement PCT ─────────────────────────────────────────────────────────────

def upsert_placement_pct(rows: list, country: str):
    """将 Bulk 提取的 placement 配置写入 campaign_placement_pct（upsert）。
    rows: list of dict，keys: campaign_name, campaign_id, portfolio_id,
          bidding_strategy, top_pct, rest_pct, pp_pct
    """
    if not rows:
        return
    with get_engine().begin() as conn:
        for r in rows:
            conn.execute(text("""
                INSERT INTO campaign_placement_pct
                    (campaign_name, campaign_id, portfolio_id, bidding_strategy,
                     top_pct, rest_pct, pp_pct, country, updated_at)
                VALUES
                    (:campaign_name, :campaign_id, :portfolio_id, :bidding_strategy,
                     :top_pct, :rest_pct, :pp_pct, :country, NOW())
                ON DUPLICATE KEY UPDATE
                    campaign_id      = VALUES(campaign_id),
                    portfolio_id     = VALUES(portfolio_id),
                    bidding_strategy = VALUES(bidding_strategy),
                    top_pct          = VALUES(top_pct),
                    rest_pct         = VALUES(rest_pct),
                    pp_pct           = VALUES(pp_pct),
                    updated_at       = NOW()
            """), {**r, 'country': country})


def get_placement_pct(country: str) -> list:
    """返回指定国家的 campaign_placement_pct 列表。"""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT campaign_name, campaign_id, portfolio_id,
                   bidding_strategy, top_pct, rest_pct, pp_pct, updated_at
            FROM campaign_placement_pct WHERE country = :country
        """), {'country': country}).mappings().all()
    return [dict(r) for r in rows]


# ── Raw Placement ─────────────────────────────────────────────────────────────

def upsert_raw_placement(df: 'pd.DataFrame', country: str):
    """增量写入 Placement 天维度报告（按 date+campaign_name+placement+country 去重）。"""
    if df is None or df.empty:
        return 0

    col_map = {
        'Date': 'date',
        'Campaign Name': 'campaign_name',
        'Portfolio name': 'portfolio_name',
        'Placement': 'placement',
        'Impressions': 'impressions',
        'Clicks': 'clicks',
        'Spend': 'spend',
        '7 Day Total Sales ': 'sales',
        '7 Day Total Orders (#)': 'orders',
        '7 Day Total Units (#)': 'units',
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    needed = ['date', 'campaign_name', 'placement', 'impressions', 'clicks',
              'spend', 'sales', 'orders', 'units']
    for c in needed:
        if c not in df.columns:
            df[c] = 0
    if 'portfolio_name' not in df.columns:
        df['portfolio_name'] = ''

    df['country'] = country
    df['date'] = pd.to_datetime(df['date']).dt.date
    for c in ['impressions', 'clicks', 'orders', 'units']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)
    for c in ['spend', 'sales']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).round(4)
    df['portfolio_name'] = df['portfolio_name'].fillna('').astype(str)

    with get_engine().begin() as conn:
        for _, row in df.iterrows():
            conn.execute(text("""
                INSERT INTO raw_placement
                    (date, campaign_name, portfolio_name, placement,
                     impressions, clicks, spend, sales, orders, units, country)
                VALUES
                    (:date, :campaign_name, :portfolio_name, :placement,
                     :impressions, :clicks, :spend, :sales, :orders, :units, :country)
                ON DUPLICATE KEY UPDATE
                    portfolio_name = VALUES(portfolio_name),
                    impressions    = VALUES(impressions),
                    clicks         = VALUES(clicks),
                    spend          = VALUES(spend),
                    sales          = VALUES(sales),
                    orders         = VALUES(orders),
                    units          = VALUES(units)
            """), row.to_dict())
    return len(df)


def _normalize_camp_name(names: pd.Series) -> pd.Series:
    """
    按活动名称结构归一化，忽略末尾版本后缀，用于 upsert 去重。

    归一化规则（取前 N 段）：
      SL_1/2/3/4_XXX          → 前 3 段   SL_1_dog leash
      SL_0_XXX（非合集）       → 前 3 段   SL_0_slip leash
      SL_0_合集_XXX            → 完整名称（不归一化）
      SL_B0XXXXXXXXXX_XXX     → 前 2 段   SL_B0DF6M88FM
      其他                    → 完整名称
    """
    parts   = names.str.split('_')
    part1   = parts.apply(lambda p: p[1] if isinstance(p, list) and len(p) > 1 else '')
    part2   = parts.apply(lambda p: p[2] if isinstance(p, list) and len(p) > 2 else '')
    norm    = names.copy()

    # ASIN 格式：SL_B0XXXXXXXXXX_...  → 前 2 段
    mask_asin = part1.str.match(_ASIN_RE, na=False)
    if mask_asin.any():
        norm[mask_asin] = parts[mask_asin].apply(
            lambda p: '_'.join(p[:2]) if len(p) >= 2 else '_'.join(p)
        )

    # 数字编号：SL_0/1/2/3/4_...  → 前 3 段（SL_0_合集 排除）
    mask_num   = part1.isin({'0', '1', '2', '3', '4'})
    mask_heji  = (part1 == '0') & part2.str.startswith('合集', na=False)
    mask_norm3 = mask_num & ~mask_heji & ~mask_asin
    if mask_norm3.any():
        norm[mask_norm3] = parts[mask_norm3].apply(
            lambda p: '_'.join(p[:3]) if len(p) >= 3 else '_'.join(p)
        )

    return norm


def _camp_comp_key(df: pd.DataFrame, sep: str) -> pd.Series:
    """raw_camp_lx 专用复合键：国家 + 归一化活动名 + 日期 + 类型"""
    norm = _normalize_camp_name(df['广告活动'].astype(str))
    return (
        df['国家'].astype(str) + sep +
        norm + sep +
        df['日期'].astype(str) + sep +
        df['类型'].astype(str)
    )


def _tar_comp_key(df: pd.DataFrame, sep: str) -> pd.Series:
    """raw_tar 专用复合键：Date + 归一化活动名 + Ad Group Name + Targeting + Match Type"""
    norm = _normalize_camp_name(df['Campaign Name'].astype(str))
    return (
        df['Date'].astype(str) + sep +
        norm + sep +
        df['Ad Group Name'].astype(str) + sep +
        df['Targeting'].astype(str) + sep +
        df['Match Type'].astype(str)
    )


def _ap_comp_key(df: pd.DataFrame, sep: str) -> pd.Series:
    """raw_ap 专用复合键：日期 + 归一化活动名 + Ad Group Name + Advertised ASIN
    列名兼容：Date 已由 _prep_ap_daily 重命名为 日期；ASIN 列中英文兼容。
    """
    camp_col = 'Campaign Name' if 'Campaign Name' in df.columns else '广告活动名称'
    asin_col = 'Advertised ASIN' if 'Advertised ASIN' in df.columns else '广告ASIN'
    norm = _normalize_camp_name(df[camp_col].astype(str))
    return (
        df['日期'].astype(str) + sep +
        norm + sep +
        df['Ad Group Name'].astype(str) + sep +
        df[asin_col].astype(str)
    )


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


def _upsert_lx(tbl: str, df_new: pd.DataFrame, keys: list, comp_key_fn=None):
    """通用 upsert（领星表）：按唯一键合并新旧数据，新行覆盖旧行。
    优化：优先走无重叠路径（直接 append），仅在日期窗口有重叠时才读取旧数据。
    comp_key_fn: 可选，签名 (df, sep) -> Series，用于自定义复合键（如归一化活动名）。
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
        with engine.begin() as conn:
            df_new.to_sql(tbl, conn, if_exists='replace', index=False)
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
            with engine.begin() as conn:
                df_new.to_sql(tbl, conn, if_exists='append', index=False)
        else:
            # 有重叠：只读重叠窗口，新数据覆盖旧数据
            with engine.connect() as conn:
                df_old = pd.read_sql(
                    text(f'SELECT * FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                    conn, params={'a': new_min, 'b': new_max}
                )
            df_old = df_old.drop(columns=['report_id'], errors='ignore')
            sep = '\x00'
            if comp_key_fn is not None:
                old_comp = comp_key_fn(df_old, sep)
                new_comp = comp_key_fn(df_new, sep)
                df_old = df_old[~old_comp.isin(set(new_comp))]
            else:
                avail_keys = [k for k in keys if k in df_old.columns and k in df_new.columns]
                if avail_keys:
                    old_comp = df_old[avail_keys].astype(str).agg(sep.join, axis=1)
                    new_comp = df_new[avail_keys].astype(str).agg(sep.join, axis=1)
                    df_old = df_old[~old_comp.isin(set(new_comp))]
            with engine.begin() as conn:
                conn.execute(
                    text(f'DELETE FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                    {'a': new_min, 'b': new_max}
                )
            with engine.begin() as conn:
                pd.concat([df_old, df_new], ignore_index=True).to_sql(
                    tbl, conn, if_exists='append', index=False
                )

    try:
        with engine.begin() as conn:
            conn.execute(text(
                f'CREATE INDEX idx_{tbl}_date ON `{tbl}`(`日期`)'
            ))
    except Exception:
        pass  # 已存在则忽略


def _sync_portfolio_from_import(df_new: pd.DataFrame):
    """导入后同步广告组合：若某活动换了组合，把历史行也更新为最新组合。
    逻辑：
    1. 对新数据按（国家 + 归一化活动名）分组，取最新日期对应的广告组合
    2. 收集该组内所有原始活动名（同一归一化名可能有细微变体）
    3. UPDATE raw_camp_lx 里组合不一致的历史行
    """
    if '广告活动' not in df_new.columns or '广告组合' not in df_new.columns:
        return

    norm = _normalize_camp_name(df_new['广告活动'].astype(str))
    df_new = df_new.copy()
    df_new['_norm'] = norm
    df_new['日期'] = df_new['日期'].astype(str)

    engine = get_engine()
    with engine.begin() as conn:
        for (country, norm_name), grp in df_new.groupby(['国家', '_norm']):
            # 最新日期对应的广告组合
            latest_port = grp.sort_values('日期').iloc[-1]['广告组合']
            if pd.isna(latest_port) or str(latest_port).strip() == '':
                continue
            latest_port = str(latest_port).strip()

            # 该归一化名下所有原始活动名
            raw_names = list(grp['广告活动'].dropna().unique())
            if not raw_names:
                continue

            placeholders = ','.join([f':n{i}' for i in range(len(raw_names))])
            params = {'country': str(country), 'port': latest_port}
            params.update({f'n{i}': str(n) for i, n in enumerate(raw_names)})

            conn.execute(text(
                f'UPDATE raw_camp_lx SET `广告组合` = :port '
                f'WHERE `国家` = :country '
                f'AND `广告活动` IN ({placeholders}) '
                f'AND `广告组合` != :port'
            ), params)


def _week_key_for_date(dt) -> str:
    iso = pd.to_datetime(dt).isocalendar()
    return f'{iso.year % 100:02d}W{iso.week}'


def _affected_country_weeks(dc: pd.DataFrame, dp: pd.DataFrame) -> dict:
    frames = [df for df in (dc, dp) if df is not None and not df.empty]
    countries = set()
    week_keys = set()
    for df in frames:
        if '国家' in df.columns:
            countries.update(str(v) for v in df['国家'].dropna().unique())
        if '日期' in df.columns:
            week_keys.update(_week_key_for_date(v) for v in df['日期'].dropna().unique())
    return {
        'countries': sorted(countries),
        'week_keys': sorted(week_keys, key=_week_sort_key),
    }


def upsert_raw(df_c: pd.DataFrame, df_p: pd.DataFrame):
    """增量写入 raw_camp_lx / raw_port_lx"""
    dc = _prep_raw(df_c)
    dp = _prep_raw(df_p)
    affected = _affected_country_weeks(dc, dp)
    _upsert_lx('raw_camp_lx', dc, _KEYS_C, comp_key_fn=_camp_comp_key)
    _upsert_lx('raw_port_lx', dp, _KEYS_P)
    _sync_portfolio_from_import(dc)
    return affected


def _week_date_range(week_keys):
    if not week_keys:
        return None, None
    starts = []
    ends = []
    for week_key in week_keys:
        year, week = str(week_key).split('W', 1)
        iso_year = 2000 + int(year)
        start = datetime.fromisocalendar(iso_year, int(week), 1).date()
        starts.append(start)
        ends.append(datetime.fromisocalendar(iso_year, int(week), 7).date())
    return min(starts), max(ends)


def _raw_filter_sql(countries=None, week_keys=None):
    clauses = []
    params = {}
    if countries:
        country_params = []
        for idx, country in enumerate(countries):
            key = f'country_{idx}'
            country_params.append(f':{key}')
            params[key] = str(country)
        clauses.append(f"`国家` IN ({', '.join(country_params)})")
    date_from, date_to = _week_date_range(week_keys)
    if date_from and date_to:
        clauses.append('`日期` BETWEEN :date_from AND :date_to')
        params['date_from'] = date_from
        params['date_to'] = date_to
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    return where, params


def get_raw_dfs(countries=None, week_keys=None):
    """
    从 raw_camp_lx / raw_port_lx 读回完整 DataFrame，供 rebuild_from_raw 使用。
    表不存在或为空时返回 (None, None)。
    """
    engine = get_engine()
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    if 'raw_camp_lx' not in tables or 'raw_port_lx' not in tables:
        return None, None
    where_sql, params = _raw_filter_sql(countries, week_keys)
    with engine.connect() as _c:
        df_c = pd.read_sql(text(f'SELECT * FROM `raw_camp_lx`{where_sql}'), _c, params=params)
        df_p = pd.read_sql(text(f'SELECT * FROM `raw_port_lx`{where_sql}'), _c, params=params)
    if df_c.empty:
        return None, None
    for df in (df_c, df_p):
        if '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])
    return df_c, df_p


def rebuild_from_raw(countries=None, week_keys=None, clear_reports: bool = False) -> list:
    """
    从 raw_camp_lx / raw_port_lx 重建报告 JSON。
    物理存储粒度为 country + week_key；读取时再按 country 合并给前端。
    返回本次更新的 report_id 列表；raw 表为空时返回 []。
    """
    from . import gen_data

    df_c, df_p = get_raw_dfs(countries=countries, week_keys=week_keys)
    if df_c is None:
        return []
    ensure_report_week_cols()

    col_c = '国家' if '国家' in df_c.columns else None
    col_p = '国家' if '国家' in df_p.columns else None

    if countries is None:
        countries = df_c[col_c].unique().tolist() if col_c else ['']
    countries = [str(c) for c in countries]
    if not countries:
        countries = ['']

    df_c['_report_week'] = df_c['日期'].apply(_week_key_for_date)
    df_p['_report_week'] = df_p['日期'].apply(_week_key_for_date)
    if week_keys is None:
        week_keys = sorted(df_c['_report_week'].dropna().unique().tolist(), key=_week_sort_key)
    else:
        week_keys = sorted({str(w) for w in week_keys}, key=_week_sort_key)

    if clear_reports:
        with get_engine().begin() as conn:
            conn.execute(text('DELETE FROM reports'))

    report_ids = []
    for country in countries:
        dc = df_c[df_c[col_c] == country].copy() if col_c else df_c
        dp = df_p[df_p[col_p] == country].copy() if col_p else df_p
        if dc.empty:
            continue

        for week_key in week_keys:
            dcw = dc[dc['_report_week'] == week_key].drop(columns=['_report_week']).copy()
            dpw = dp[dp['_report_week'] == week_key].drop(columns=['_report_week']).copy()
            if dcw.empty:
                continue

            try:
                data = gen_data.process(dcw, dpw)
            except Exception as e:
                print(f'[rebuild] {country} {week_key} 处理失败: {e}')
                continue

            weeks = data['wk']
            wk_dates = data.get('wk_dates', {})
            wk_iso_dates = data.get('wk_iso_dates', {})
            iso_range = wk_iso_dates.get(week_key) or []
            week_start = iso_range[0] if len(iso_range) >= 1 else None
            week_end = iso_range[1] if len(iso_range) >= 2 else None
            new_title = _format_country_title(country, weeks)

            data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            weeks_json = json.dumps(weeks, ensure_ascii=False)
            wkdates_json = json.dumps(wk_dates, ensure_ascii=False)

            with get_engine().begin() as conn:
                result = conn.execute(
                    text(
                        """
                        INSERT INTO reports
                            (title, country, week_key, week_start, week_end,
                             weeks, wk_dates, data, camp_file, port_file, created_at)
                        VALUES
                            (:title, :country, :week_key, :week_start, :week_end,
                             :weeks, :wk_dates, :data, '', '', NOW())
                        ON DUPLICATE KEY UPDATE
                            title=VALUES(title),
                            week_start=VALUES(week_start),
                            week_end=VALUES(week_end),
                            weeks=VALUES(weeks),
                            wk_dates=VALUES(wk_dates),
                            data=VALUES(data),
                            created_at=NOW()
                        """
                    ),
                    {
                        'title': new_title,
                        'country': str(country),
                        'week_key': week_key,
                        'week_start': week_start,
                        'week_end': week_end,
                        'weeks': weeks_json,
                        'wk_dates': wkdates_json,
                        'data': data_json,
                    }
                )
                existing = conn.execute(
                    text('SELECT id FROM reports WHERE country=:country AND week_key=:week_key'),
                    {'country': str(country), 'week_key': week_key}
                ).first()
                report_id = existing[0] if existing else result.lastrowid

            report_ids.append(report_id)
            print(f'[rebuild] {country} {week_key} report_id={report_id} '
                  f'camp={len(dcw)}行 port={len(dpw)}行')

    return report_ids


def delete_report(report_id: int):
    """删除报告。raw_camp_lx / raw_port_lx 是原始数据源，不随报告删除。"""
    with get_engine().begin() as conn:
        row = conn.execute(text('SELECT country, week_key FROM reports WHERE id=:id'), {'id': report_id}).first()
        if row and row[1]:
            conn.execute(text('DELETE FROM reports WHERE country=:country'), {'country': row[0]})
        else:
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
    up_orders_threshold:  int = 2,
) -> int:
    """记录一次已确认上传到亚马逊的竞价更新操作，返回新记录 id。"""
    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                'INSERT INTO bid_update_log '
                '(product_target, bulk_filename, label_filename, '
                ' report_start, report_end, orders_threshold, up_orders_threshold, updated_count, log_lines, '
                ' target_acos, avg_clicks_per_order, country) '
                'VALUES (:pt, :bf, :lf, :rs, :re, :ot, :uot, :uc, :ll, :ta, :ac, :co)'
            ),
            {
                'pt': product_target,  'bf': bulk_filename,
                'lf': label_filename,  'rs': report_start,
                're': report_end,      'ot': orders_threshold,
                'uot': up_orders_threshold,
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
                    '       report_start, report_end, orders_threshold, up_orders_threshold, updated_count, '
                    '       target_acos, avg_clicks_per_order, country '
                    'FROM bid_update_log WHERE country = :co ORDER BY id DESC LIMIT :lim'
                ),
                {'lim': limit, 'co': country}
            )
        else:
            result = conn.execute(
                text(
                    'SELECT id, confirmed_at, product_target, bulk_filename, '
                    '       report_start, report_end, orders_threshold, up_orders_threshold, updated_count, '
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
            weekly_result = conn.execute(
                text(
                    'SELECT `广告活动`, `日期`,'
                    '  SUM(`花费`) AS sp,'
                    '  SUM(`广告销售额`) AS sl'
                    ' FROM `raw_camp_lx`'
                    ' WHERE `国家` = :country'
                    '   AND `日期` >= :date_from'
                    '   AND `日期` <= :date_to'
                    ' GROUP BY `广告活动`, `日期`'
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
            week_keys = []
            start_dt = pd.to_datetime(date_from).date()
            end_dt = pd.to_datetime(date_to).date()
            cur = start_dt
            while cur <= end_dt:
                wk = _week_key_for_date(cur)
                if wk not in week_keys:
                    week_keys.append(wk)
                cur = cur + pd.Timedelta(days=1)
            weekly_totals: dict = {}
            for row in weekly_result.mappings():
                name = str(row['广告活动'])
                wk = _week_key_for_date(row['日期'])
                bucket = weekly_totals.setdefault(name, {}).setdefault(wk, {'sp': 0.0, 'sl': 0.0})
                bucket['sp'] += float(row['sp'] or 0)
                bucket['sl'] += float(row['sl'] or 0)
            for name, item in out.items():
                item['wa'] = [
                    round(v['sp'] / v['sl'] * 100, 2) if (v := weekly_totals.get(name, {}).get(wk)) and v['sl'] > 0 else None
                    for wk in week_keys
                ]
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

            # 2. 调价明细（逐条，含原价/新价/幅度）
            bid_rows = conn.execute(
                text(
                    'SELECT DATE(l.confirmed_at) AS event_date,'
                    '  d.targeting, d.old_bid, d.new_bid, d.adj_pct'
                    ' FROM bid_update_detail d'
                    ' JOIN bid_update_log l ON d.log_id = l.id'
                    ' WHERE d.campaign = :campaign'
                    '   AND l.country  = :country'
                    ' ORDER BY event_date, d.targeting'
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
            iso = date_obj.isocalendar()
            wk = f'{iso.year % 100:02d}W{iso.week}'
            if wk not in wk_buckets:
                wk_buckets[wk] = {'sp': 0, 'sl': 0, 'cl': 0, 'im': 0, 'or_': 0}
            b = wk_buckets[wk]
            b['sp']  += d['sp'];  b['sl']  += d['sl']
            b['cl']  += d['cl'];  b['im']  += d['im']
            b['or_'] += d['or_']

        def _week_sort_key(wk: str) -> tuple[int, int]:
            try:
                year_part, week_part = str(wk).split('W', 1)
                return int(year_part), int(week_part)
            except Exception:
                return (0, 0)

        weekly = []
        for wk, b in sorted(wk_buckets.items(), key=lambda item: _week_sort_key(item[0])):
            sp, sl, cl, im, or_ = b['sp'], b['sl'], b['cl'], b['im'], b['or_']
            weekly.append({
                'wk': wk, 'sp': sp, 'sl': sl, 'cl': cl, 'im': im, 'or_': or_,
                'ac': round(sp / sl * 100, 2) if sl > 0 else None,
                'ro': round(sl / sp, 3)        if sp > 0 else None,
                'cp': round(sp / cl, 3)        if cl > 0 else None,
                'ct': round(cl / im * 100, 4)  if im > 0 else None,
                'cv': round(or_ / cl * 100, 4) if cl > 0 else None,
            })

        # 5. 调价事件列表（按日期分组，每组含逐条明细）
        bid_by_date: dict = {}
        for r in bid_rows:
            dt = str(r['event_date']) if r['event_date'] else ''
            if not dt:
                continue
            if dt not in bid_by_date:
                bid_by_date[dt] = []
            bid_by_date[dt].append({
                'targeting': str(r['targeting'] or ''),
                'old_bid':   round(float(r['old_bid'] or 0), 4),
                'new_bid':   round(float(r['new_bid'] or 0), 4),
                'adj_pct':   round(float(r['adj_pct']), 4) if r['adj_pct'] is not None else None,
            })

        bid_events = []
        for dt, records in sorted(bid_by_date.items()):
            up = sum(1 for x in records if x['new_bid'] > x['old_bid'])
            dn = sum(1 for x in records if x['new_bid'] < x['old_bid'])
            bid_events.append({
                'date':      dt,
                'tgt_count': len(records),
                'up_count':  up,
                'dn_count':  dn,
                'records':   records,
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


def _upsert_df(engine, tbl: str, df_new: pd.DataFrame, keys: list, date_col: str,
               comp_key_fn=None):
    """通用 upsert：按唯一键合并新旧数据，新行覆盖旧行。
    优化：优先走无重叠路径（直接 append），仅在日期窗口有重叠时才读取旧数据。
    comp_key_fn: 可选，签名 (df, sep) -> Series，用于自定义复合键（如归一化活动名）。
    """
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())

    if tbl not in tables:
        row_count = 0
    else:
        with engine.connect() as conn:
            row_count = conn.execute(text(f'SELECT COUNT(*) FROM `{tbl}`')).scalar()

    if row_count == 0:
        with engine.begin() as conn:
            df_new.to_sql(tbl, conn, if_exists='replace', index=False)
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
            with engine.begin() as conn:
                df_new.to_sql(tbl, conn, if_exists='append', index=False)
        else:
            # 有重叠：只读重叠窗口，新数据覆盖旧数据
            with engine.connect() as conn:
                df_old = pd.read_sql(
                    text(f'SELECT * FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                    conn, params={'a': new_min, 'b': new_max}
                )
            sep = '\x00'
            if comp_key_fn is not None:
                old_comp = comp_key_fn(df_old, sep)
                new_comp = comp_key_fn(df_new, sep)
                df_old = df_old[~old_comp.isin(set(new_comp))]
            else:
                avail_keys = [k for k in keys if k in df_old.columns and k in df_new.columns]
                if avail_keys:
                    old_comp = df_old[avail_keys].astype(str).agg(sep.join, axis=1)
                    new_comp = df_new[avail_keys].astype(str).agg(sep.join, axis=1)
                    df_old = df_old[~old_comp.isin(set(new_comp))]
            with engine.begin() as conn:
                conn.execute(
                    text(f'DELETE FROM `{tbl}` WHERE `{date_col}` BETWEEN :a AND :b'),
                    {'a': new_min, 'b': new_max}
                )
            with engine.begin() as conn:
                pd.concat([df_old, df_new], ignore_index=True).to_sql(
                    tbl, conn, if_exists='append', index=False
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
    _upsert_df(engine, 'raw_tar', tar, _KEYS_TAR, 'Date', comp_key_fn=_tar_comp_key)
    _upsert_df(engine, 'raw_ap',  ap,  ap_keys,   '日期', comp_key_fn=_ap_comp_key)

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

    with engine.connect() as conn:
        tar_df = pd.read_sql(
            text(f'SELECT * FROM `raw_tar` WHERE `Date` >= :cutoff{country_clause_tar}'),
            conn, params=query_params
        )
        ap_df = pd.read_sql(
            text(f'SELECT * FROM `raw_ap` WHERE `日期` >= :cutoff{country_clause_ap}'),
            conn, params=query_params
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


