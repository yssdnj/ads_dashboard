"""
main.py — FastAPI 后端
启动: uvicorn api.main:app --reload  （从 ads_funnel/ 目录执行）
"""

import asyncio, base64, gc, io, json, traceback
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db, export, gen_data, targeting_analysis, bulk_update


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield

app = FastAPI(title='广告漏斗分析 v2.0', version='2.0.0', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'], allow_methods=['*'], allow_headers=['*'],
)

FRONTEND = Path(__file__).parent.parent / 'frontend'
BULK_DIR = Path(__file__).parent.parent / 'data' / 'bulk'

# 国家别名映射：中文 → 所有等价写法（英文全称、短码、中文）
_COUNTRY_ALIASES: dict[str, set[str]] = {
    '美国':  {'United States', 'US', '美国'},
    '英国':  {'United Kingdom', 'UK', '英国'},
    '德国':  {'Germany', 'DE', '德国'},
    '法国':  {'France', 'FR', '法国'},
    '加拿大': {'Canada', 'CA', '加拿大'},
    '日本':  {'Japan', 'JP', '日本'},
    '意大利': {'Italy', 'IT', '意大利'},
    '西班牙': {'Spain', 'ES', '西班牙'},
}

# 反向查找：任意别名（DE / Germany / 德国）→ 该国家的完整别名集合
_COUNTRY_REVERSE: dict[str, set[str]] = {}
for _cn, _vals in _COUNTRY_ALIASES.items():
    for _v in _vals:
        _COUNTRY_REVERSE[_v] = _vals

def _country_code(country: str) -> str:
    """将任意国家表示转为两字母代码。收口：实际逻辑在 export.country_code()。"""
    return export.country_code(country)


def _resolve_country(country: str) -> set[str] | None:
    """将任意国家表示（中文/英文/ISO 码）转为完整别名集合，匹配失败返回 None。"""
    c = country.strip()
    if not c:
        return None
    return _COUNTRY_ALIASES.get(c) or _COUNTRY_REVERSE.get(c)


def _check_file_country(df: pd.DataFrame, report_country: str, label: str):
    """校验 DataFrame 的 Country / 国家/地区 列是否与 report_country 一致。"""
    col = next(
        (c for c in df.columns if c.strip() in ('Country', '国家/地区')),
        None,
    )
    if col is None:
        return
    aliases = _resolve_country(report_country)
    if not aliases:
        return
    file_countries = set(str(v) for v in df[col].dropna().unique())
    if not aliases.intersection(file_countries):
        raise HTTPException(
            400,
            f'{label} 国家不匹配：当前页面 {report_country}，'
            f'文件中包含：{", ".join(sorted(file_countries))}',
        )


    db.rebuild_from_raw()       # 从原始数据重建 JSON（若 raw 表有数据）
    print('Database ready (MySQL)')


# ── 静态文件 ──────────────────────────────────────────────────────────────────
# frontend/static/ 下的 CSS/JS
static_dir = FRONTEND / 'static'
static_dir.mkdir(exist_ok=True)
app.mount('/static', StaticFiles(directory=str(static_dir)), name='static')


# ── 页面路由 ──────────────────────────────────────────────────────────────────


@app.get('/', response_class=HTMLResponse)
def index_page(id: int = None):
    """主页：直接渲染 dashboard，支持 ?id=N 切换报告"""
    reports = db.list_reports()
    if not reports:
        return (FRONTEND / 'index.html').read_text(encoding='utf-8')

    # 确定要展示的报告
    if id:
        report_id = id
    else:
        # 优先按 default_country 配置选择，默认 US
        default_country = db.get_config('default_country', 'US')
        preferred = next((r for r in reports if r.get('country') == default_country), None)
        report_id = preferred['id'] if preferred else reports[0]['id']
    r = db.get_report(report_id)
    if not r:
        r = db.get_report(reports[0]['id'])
        report_id = reports[0]['id']

    acos_targets = db.get_config('acos_targets', {})
    data = json.loads(r['data'])
    html = export.to_html_webapp(data, r['title'], acos_targets, report_id=report_id)
    return HTMLResponse(html)


# ── API: 报告列表 ──────────────────────────────────────────────────────────────

@app.get('/api/reports')
def api_list_reports():
    return db.list_reports()


@app.get('/api/reports/{report_id}')
def api_get_report(report_id: int):
    r = db.get_report(report_id)
    if not r:
        raise HTTPException(404, '报告不存在')
    r['data'] = json.loads(r['data'])
    return r


@app.delete('/api/reports/{report_id}')
def api_delete_report(report_id: int):
    db.delete_report(report_id)
    return {'ok': True}


# ── API: 导入 ─────────────────────────────────────────────────────────────────

@app.post('/api/import')
async def api_import(
    camp_file: UploadFile = File(..., description='广告活动每日明细 Excel（领星"全部"导出）'),
    port_file: UploadFile = File(..., description='广告组合每日明细 Excel'),
    title: str = Form(''),
):
    """
    增量导入：
    1. 读取两份 Excel
    2. 按唯一主键 upsert 到 raw_camp / raw_port（新覆盖旧，旧数据保留）
    3. 从完整 raw 数据重新计算 JSON，更新主报告
    """
    camp_bytes = await camp_file.read()
    port_bytes = await port_file.read()

    # P0: 重型同步操作放入线程池，释放事件循环，避免 nginx 502
    # P1: 读完 DataFrame 后立即释放原始字节，降低内存峰值
    loop = asyncio.get_event_loop()

    def _do_import():
        nonlocal camp_bytes, port_bytes
        try:
            df_c = pd.read_excel(io.BytesIO(camp_bytes))
            df_p = pd.read_excel(io.BytesIO(port_bytes))
        except Exception as e:
            raise ValueError(f'Excel 读取失败: {e}')
        finally:
            del camp_bytes, port_bytes
            gc.collect()

        db.upsert_raw(df_c, df_p)
        del df_c, df_p
        gc.collect()
        return db.rebuild_from_raw()

    try:
        report_ids = await loop.run_in_executor(None, _do_import)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f'数据处理失败: {e}')

    if not report_ids:
        raise HTTPException(500, 'raw 数据为空，导入失败')

    reports = [db.get_report(rid) for rid in report_ids]
    return {
        'ids':     report_ids,
        'reports': [{'id': r['id'], 'title': r['title'], 'weeks': r['weeks']} for r in reports if r],
    }


# ── API: Bulk 文件管理 ────────────────────────────────────────────────────────

import re as _re

def _parse_bulk_end_date(filename: str) -> str | None:
    """从 bulk 文件名提取 end_date（格式 YYYYMMDD），无法解析返回 None。"""
    m = _re.search(r'\d{8}-(\d{8})', filename)
    return m.group(1) if m else None


def _safe_id(val) -> str:
    """将数值型 ID 安全转为字符串，NaN / 空 → ''"""
    if val is None:
        return ''
    try:
        if pd.isna(val):
            return ''
    except Exception:
        pass
    s = str(val).strip()
    if s in ('', 'nan', 'None'):
        return ''
    try:
        return str(int(float(s)))
    except ValueError:
        return s


@app.post('/api/bulk/upload')
async def api_bulk_upload(
    bulk_file: UploadFile = File(...),
    country_code: str = Form(...),
):
    """上传 bulk 文件：按 end_date 判断是否替换，并同步四张原始表的 ID 列。"""
    new_end = _parse_bulk_end_date(bulk_file.filename)
    if not new_end:
        raise HTTPException(400, f'文件名无法识别日期范围：{bulk_file.filename}')

    country_dir = BULK_DIR / country_code
    country_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(country_dir.glob('*.xls*'))
    existing_end = None
    if existing:
        existing_end = _parse_bulk_end_date(existing[0].name)

    if existing_end and new_end <= existing_end:
        return {
            'replaced': False,
            'message': f'当前已是最新（end_date={existing_end}），跳过替换',
            'end_date': existing_end,
            'filename': existing[0].name,
        }

    content = await bulk_file.read()

    for f in existing:
        f.unlink()
    new_path = country_dir / bulk_file.filename
    new_path.write_bytes(content)

    # 提取 campaign/portfolio 映射
    xls = pd.read_excel(io.BytesIO(content), sheet_name='Sponsored Products Campaigns')
    xls.columns = [c.strip() for c in xls.columns]

    camp_rows = xls[xls['Entity'] == 'Campaign'].copy()
    camp_rows = camp_rows[camp_rows['Campaign Name'].notna()]

    norm_names = db._normalize_camp_name(camp_rows['Campaign Name'].astype(str))
    camp_map: dict = {}
    port_col = 'Portfolio Name (Informational only)'
    for orig, norm, cid, pid in zip(
        camp_rows['Campaign Name'],
        norm_names,
        camp_rows.get('Campaign ID', pd.Series([''] * len(camp_rows), index=camp_rows.index)),
        camp_rows.get('Portfolio ID', pd.Series([''] * len(camp_rows), index=camp_rows.index)),
    ):
        camp_map[norm] = (_safe_id(cid), _safe_id(pid))

    port_map: dict = {}
    if port_col in camp_rows.columns and 'Portfolio ID' in camp_rows.columns:
        for pname, pid in zip(camp_rows[port_col], camp_rows['Portfolio ID']):
            pname_s = str(pname).strip() if pd.notna(pname) else ''
            if pname_s and pname_s not in ('nan', 'None'):
                port_map[pname_s] = _safe_id(pid)

    # 反向映射：ID → 规范名称（用于改名检测）
    camp_id_name_map: dict = {}
    for orig, cid_pid in zip(camp_rows['Campaign Name'], zip(
        camp_rows.get('Campaign ID', pd.Series([''] * len(camp_rows), index=camp_rows.index)),
        camp_rows.get('Portfolio ID', pd.Series([''] * len(camp_rows), index=camp_rows.index)),
    )):
        cid = _safe_id(cid_pid[0])
        if cid:
            camp_id_name_map[cid] = str(orig).strip()

    port_id_name_map: dict = {v: k for k, v in port_map.items() if v}

    stats = db.update_ids_from_bulk(camp_map, port_map, camp_id_name_map, port_id_name_map)
    db.rebuild_from_raw()

    # 提取 Placement PCT 数据写库
    _STRATEGY_MAP = {
        'Fixed bid': 'FX',
        'Fixed bids': 'FX',
        'Dynamic bids - down only': 'DW',
        'Dynamic bids - up and down': 'UD',
    }
    _PLACEMENT_MAP = {
        'Placement Top': 'top_pct',
        'Placement Rest Of Search': 'rest_pct',
        'Placement Product Page': 'pp_pct',
    }
    camp_strategy: dict = {}
    camp_name_col = 'Campaign Name (Informational only)'
    for _, row in xls[xls['Entity'] == 'Campaign'].iterrows():
        cid = _safe_id(row.get('Campaign ID', ''))
        cname = str(row.get('Campaign Name', '')).strip()
        strategy_raw = str(row.get('Bidding Strategy', '')).strip()
        strategy = _STRATEGY_MAP.get(strategy_raw, strategy_raw)
        if cid:
            camp_strategy[cid] = (cname, strategy)

    placement_rows_raw = xls[xls['Entity'] == 'Bidding Adjustment'].copy()
    pct_by_camp: dict = {}
    for _, row in placement_rows_raw.iterrows():
        cid = _safe_id(row.get('Campaign ID', ''))
        placement = str(row.get('Placement', '')).strip()
        pct_key = _PLACEMENT_MAP.get(placement)
        if not cid or not pct_key:
            continue
        if cid not in pct_by_camp:
            pct_by_camp[cid] = {'top_pct': 0, 'rest_pct': 0, 'pp_pct': 0}
        try:
            pct_by_camp[cid][pct_key] = int(float(row.get('Percentage', 0) or 0))
        except (ValueError, TypeError):
            pass

    placement_pct_rows = []
    for cid, pcts in pct_by_camp.items():
        cname, strategy = camp_strategy.get(cid, ('', ''))
        if not cname:
            continue
        pid = ''
        for norm_n, (cid2, pid2) in camp_map.items():
            if cid2 == cid:
                pid = pid2
                break
        placement_pct_rows.append({
            'campaign_name': cname,
            'campaign_id': cid,
            'portfolio_id': pid,
            'bidding_strategy': strategy,
            **pcts,
        })
    db.upsert_placement_pct(placement_pct_rows, country_code)

    return {
        'replaced': True,
        'filename': bulk_file.filename,
        'end_date': new_end,
        'stats': stats,
        'placement_pct_updated': len(placement_pct_rows),
    }


@app.get('/api/bulk/status')
def api_bulk_status():
    """返回各国家当前存储的 bulk 文件信息。"""
    if not BULK_DIR.exists():
        return []
    result = []
    for country_dir in sorted(BULK_DIR.iterdir()):
        if not country_dir.is_dir():
            continue
        files = sorted(country_dir.glob('*.xls*'))
        if not files:
            continue
        f = files[0]
        end_date = _parse_bulk_end_date(f.name) or ''
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        result.append({
            'country_code': country_dir.name,
            'filename': f.name,
            'end_date': end_date,
            'uploaded_at': mtime,
        })
    return result


@app.post('/api/placement/upload')
async def api_placement_upload(
    placement_file: UploadFile = File(...),
    country_code: str = Form(...),
):
    """上传 Placement 天维度报告，增量写入 raw_placement。"""
    content = await placement_file.read()
    try:
        df = pd.read_excel(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(400, f'文件读取失败：{e}')
    count = db.upsert_raw_placement(df, country_code)
    return {'inserted': count, 'filename': placement_file.filename}


@app.get('/api/analysis/placement-pct')
def api_placement_pct(country: str):
    """返回指定国家的广告活动 Placement 配置（竞价策略 + TOS/ROS/PP%）。"""
    rows = db.get_placement_pct(country)
    return rows


@app.post('/api/bulk/reprocess-placement')
def api_bulk_reprocess_placement(country_code: str = Form(...)):
    """从已存储的 bulk 文件重新提取 placement pct，不替换文件。"""
    country_dir = BULK_DIR / country_code
    files = sorted(country_dir.glob('*.xls*')) if country_dir.exists() else []
    if not files:
        raise HTTPException(404, f'未找到 {country_code} 的 bulk 文件')

    xls = pd.read_excel(files[0], sheet_name='Sponsored Products Campaigns')
    xls.columns = [c.strip() for c in xls.columns]

    _STRATEGY_MAP = {
        'Fixed bid': 'FX', 'Fixed bids': 'FX',
        'Dynamic bids - down only': 'DW',
        'Dynamic bids - up and down': 'UD',
    }
    _PLACEMENT_MAP = {
        'Placement Top': 'top_pct',
        'Placement Rest Of Search': 'rest_pct',
        'Placement Product Page': 'pp_pct',
    }

    # cid → (cname, strategy, pid)
    camp_info: dict = {}
    for _, row in xls[xls['Entity'] == 'Campaign'].iterrows():
        cid = _safe_id(row.get('Campaign ID', ''))
        if not cid:
            continue
        camp_info[cid] = (
            str(row.get('Campaign Name', '')).strip(),
            _STRATEGY_MAP.get(str(row.get('Bidding Strategy', '')).strip(), ''),
            _safe_id(row.get('Portfolio ID', '')),
        )

    pct_by_camp: dict = {}
    for _, row in xls[xls['Entity'] == 'Bidding Adjustment'].iterrows():
        cid = _safe_id(row.get('Campaign ID', ''))
        pct_key = _PLACEMENT_MAP.get(str(row.get('Placement', '')).strip())
        if not cid or not pct_key:
            continue
        pct_by_camp.setdefault(cid, {'top_pct': 0, 'rest_pct': 0, 'pp_pct': 0})
        try:
            pct_by_camp[cid][pct_key] = int(float(row.get('Percentage', 0) or 0))
        except (ValueError, TypeError):
            pass

    rows = []
    for cid, pcts in pct_by_camp.items():
        info = camp_info.get(cid)
        if not info or not info[0]:
            continue
        cname, strategy, pid = info
        rows.append({'campaign_name': cname, 'campaign_id': cid,
                     'portfolio_id': pid, 'bidding_strategy': strategy, **pcts})
    db.upsert_placement_pct(rows, country_code)
    return {'updated': len(rows), 'filename': files[0].name}


# ── API: 导出 ─────────────────────────────────────────────────────────────────

@app.get('/api/reports/{report_id}/export/html')
def api_export_html(report_id: int):
    """导出为可离线分享的单文件 HTML"""
    r = db.get_report(report_id)
    if not r:
        raise HTTPException(404)

    acos_targets = db.get_config('acos_targets', {})
    data = json.loads(r['data'])
    html = export.to_html_export(data, r['title'], acos_targets)

    weeks = r['weeks']
    fname = f'广告漏斗分析_{weeks[0]}-{weeks[-1]}.html' if weeks else f'report_{report_id}.html'
    fname_encoded = fname.encode('utf-8').hex()  # 避免中文文件名编码问题

    return StreamingResponse(
        io.BytesIO(html.encode('utf-8')),
        media_type='text/html; charset=utf-8',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{fname}"}
    )


@app.get('/api/reports/{report_id}/export/json')
def api_export_json(report_id: int):
    """导出原始 JSON 数据包"""
    r = db.get_report(report_id)
    if not r:
        raise HTTPException(404)

    return StreamingResponse(
        io.BytesIO(r['data'].encode('utf-8')),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename=ads_compact_{report_id}.json'}
    )


@app.get('/api/reports/{report_id}/export/csv')
def api_export_csv(report_id: int):
    """导出广告活动汇总 CSV"""
    r = db.get_report(report_id)
    if not r:
        raise HTTPException(404)

    data = json.loads(r['data'])
    csv  = export.to_csv(data)

    weeks = r['weeks']
    fname = f'ads_{weeks[0]}-{weeks[-1]}.csv' if weeks else f'ads_{report_id}.csv'

    return StreamingResponse(
        io.BytesIO(('﻿' + csv).encode('utf-8')),  # BOM for Excel 中文兼容
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{fname}"}
    )


# ── API: 配置 ─────────────────────────────────────────────────────────────────

@app.get('/api/config/acos-targets')
def api_get_acos_targets():
    return db.get_config('acos_targets', {})


@app.post('/api/config/acos-targets')
def api_set_acos_targets(targets: dict):
    db.set_config('acos_targets', targets)
    return {'ok': True}


# ── API: 投放数据入库（Mode 1 前置步骤）─────────────────────────────────────

@app.post('/api/analysis/mode1/import-data')
async def api_mode1_import_data(
    ap_file:       UploadFile = File(..., description='推广商品报告 xlsx（每日格式）'),
    tar_file:      UploadFile = File(..., description='投放报告 xlsx（每日格式，含 Date 列）'),
    report_country: str       = Form(..., description='当前页面国家（中文，如 美国）'),
):
    """
    将每周的推广商品报告 + 投放报告增量写入数据库（raw_ap / raw_tar）。
    不做分析，仅入库。每周上传一次上周数据即可。
    """
    ap_bytes  = await ap_file.read()
    tar_bytes = await tar_file.read()

    # P0: 重型同步操作放入线程池，释放事件循环，避免 nginx 502
    # P1: 读完 DataFrame 后立即释放原始字节，降低内存峰值
    loop = asyncio.get_event_loop()

    def _do_import():
        nonlocal ap_bytes, tar_bytes
        try:
            ap_df  = pd.read_excel(io.BytesIO(ap_bytes))
            tar_df = pd.read_excel(io.BytesIO(tar_bytes))
        except Exception as e:
            raise ValueError(f'文件读取失败: {e}')
        finally:
            del ap_bytes, tar_bytes
            gc.collect()

        _check_file_country(ap_df,  report_country, 'AP 文件')
        _check_file_country(tar_df, report_country, 'TAR 文件')
        return db.upsert_tar_ap(tar_df, ap_df)

    try:
        stats = await loop.run_in_executor(None, _do_import)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f'入库失败: {e}')

    # 入库后返回当前库内数据范围（按国家过滤）
    country_values = _resolve_country(report_country)
    db_stats = db.get_tar_ap_stats(country_values)
    return {
        'ok':       True,
        'imported': stats,
        'db_stats': db_stats,
    }


@app.get('/api/analysis/mode1/data-stats')
def api_mode1_data_stats(country: str = ''):
    """返回库内 raw_tar / raw_ap 的数据覆盖范围（按国家过滤）。"""
    country_values = _resolve_country(country)
    return db.get_tar_ap_stats(country_values)


# ── API: 多轮投放结构分析（Mode 1）───────────────────────────────────────────

@app.post('/api/analysis/mode1')
async def api_mode1_analysis(
    product_target:       str   = Form(...),
    target_acos:          float = Form(...),        # 百分比值，如 20.0 表示 20%
    avg_clicks_per_order: float = Form(...),
    core_sales_share:     float = Form(0.2),
    report_country:       str   = Form(''),
):
    """
    Mode 1 多轮分析：从数据库读取最近 6 个日历周数据，
    跑 R1(1周) / R2(3周) / R3(6周) 三轮，投票得出 consensus。
    请先通过 /api/analysis/mode1/import-data 导入数据。
    """
    try:
        asins = targeting_analysis.get_asins_for_product(product_target)
    except FileNotFoundError as e:
        raise HTTPException(500, str(e))
    if not asins:
        raise HTTPException(400, f'产品目录中未找到 {product_target.split("_")[0]} 的 ASIN，请检查 product_catalog.xlsx')

    country_values = _resolve_country(report_country) if report_country else None
    tar_df, ap_df, week_sundays = db.get_tar_ap_for_analysis(n_weeks=6, country_values=country_values)
    if tar_df is None:
        raise HTTPException(400, '数据库中暂无投放数据，请先通过「导入数据」上传报告文件')
    if len(week_sundays) < 1:
        raise HTTPException(400, '数据不足，无法确定完整日历周，请补充上传数据')

    try:
        result = targeting_analysis.run_multi_round_analysis(
            tar_df               = tar_df,
            ap_df                = ap_df,
            week_sundays         = week_sundays,
            product_target       = product_target,
            asins                = asins,
            target_acos          = target_acos / 100,
            avg_clicks_per_order = avg_clicks_per_order,
            core_sales_share     = core_sales_share,
        )
    except Exception as e:
        traceback.print_exc()   # 完整堆栈打印到 uvicorn 控制台
        raise HTTPException(500, f'分析失败: {e}')

    # R3 error 检查（最宽时间窗口，若报错通常是数据问题）
    if result.get('R3', {}).get('error'):
        raise HTTPException(400, result['R3']['error'])

    return result


# ── API: Mode 1 → 六轮分析 ────────────────────────────────────────────────────

@app.post('/api/analysis/mode1/bid-optimize-6r')
async def api_mode1_bid_optimize_6r(
    product_target:       str   = Form(...),
    target_acos:          float = Form(...),        # 百分比值，如 20.0 表示 20%
    avg_clicks_per_order: float = Form(...),
    core_sales_share:     float = Form(0.2),
    report_country:       str   = Form(''),
    up_orders_threshold:  int   = Form(2,   description='提价入围最低订单数，默认 2'),
):
    """
    Mode 1 六轮分析：从数据库读取最近 6 个日历周数据，
    跑 R1(1周)~R6(6周) 六轮，≥3/6 命中且非向好趋势 → 进入 consensus。
    请先通过 /api/analysis/mode1/import-data 导入数据。
    """
    try:
        asins = targeting_analysis.get_asins_for_product(product_target)
    except FileNotFoundError as e:
        raise HTTPException(500, str(e))
    if not asins:
        raise HTTPException(400, f'产品目录中未找到 {product_target.split("_")[0]} 的 ASIN，请检查 product_catalog.xlsx')

    country_values = _resolve_country(report_country) if report_country else None
    tar_df, ap_df, week_sundays = db.get_tar_ap_for_analysis(n_weeks=6, country_values=country_values)
    if tar_df is None:
        raise HTTPException(400, '数据库中暂无投放数据，请先通过「导入数据」上传报告文件')
    if len(week_sundays) < 1:
        raise HTTPException(400, '数据不足，无法确定完整日历周，请补充上传数据')

    try:
        result = targeting_analysis.run_multi_round_analysis_6r(
            tar_df               = tar_df,
            ap_df                = ap_df,
            week_sundays         = week_sundays,
            product_target       = product_target,
            asins                = asins,
            target_acos          = target_acos / 100,
            avg_clicks_per_order = avg_clicks_per_order,
            core_sales_share     = core_sales_share,
            up_orders_threshold  = up_orders_threshold,
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f'六轮分析失败: {e}')

    if result.get('R6', {}).get('error'):
        raise HTTPException(400, result['R6']['error'])

    return result


# ── API: Mode 1 → 更新 Bulk 文件 ──────────────────────────────────────────────

@app.post('/api/analysis/mode1/export-bulk')
async def api_mode1_export_bulk(
    bulk_file:        UploadFile = File(..., description='Amazon Bulk 文件（xlsx，文件名须以 Bulk 开头）'),
    rows_json:        str        = Form(..., description='consensus rows JSON（前端回传）'),
    product_target:   str        = Form('',  description='产品标识，用于文件名'),
    report_start:     str        = Form('',  description='报告开始日期，用于文件名'),
    report_end:       str        = Form('',  description='报告结束日期，用于文件名'),
    orders_threshold:    int        = Form(10,  description='降价订单数筛选阈值，默认 10'),
    up_orders_threshold: int        = Form(2,   description='提价订单数筛选阈值，默认 2'),
    history_guard_days:  int        = Form(14,  description='历史调价保护天数，默认 14'),
    report_country:      str        = Form('',  description='当前页面国家（中文，如 美国），用于文件名'),
):
    """
    将 Mode 1 分析结果写回 Amazon Bulk 文件，同时生成更新版 targeting_labels CSV。
    筛选条件：label 含 "高ACoS出单" 或 "高点击不出单"，且 orders < 10。

    返回 JSON（两个文件均以 base64 编码）：
      { bulk_b64, bulk_filename, label_b64, label_filename, log }
    """
    try:
        rows = json.loads(rows_json)
    except Exception:
        raise HTTPException(400, 'rows_json 格式错误，请重新运行分析')

    bulk_bytes = await bulk_file.read()

    history_map = db.get_targeting_history(product_target, report_country)

    try:
        bulk_out_bytes, label_csv_bytes, log, details = bulk_update.apply_mode1_to_bulk(
            bulk_bytes, rows,
            product_target      = product_target,
            report_start        = report_start,
            report_end          = report_end,
            orders_threshold    = orders_threshold,
            up_orders_threshold = up_orders_threshold,
            history_map         = history_map,
            history_guard_days  = history_guard_days,
        )
    except Exception as e:
        raise HTTPException(500, f'Bulk 更新失败: {e}')

    today        = datetime.now().strftime('%Y%m%d')
    cc           = _country_code(report_country)
    country_part = f'_{cc}' if cc else ''
    pt_part      = f'_{product_target}' if product_target else ''

    bulk_filename  = f'bulk{country_part}{pt_part}_{today}_updated.xlsx'
    label_filename = f'targeting_labels{country_part}{pt_part}_{report_start}_{report_end}_{today}_updated.csv'

    return {
        'bulk_b64':       base64.b64encode(bulk_out_bytes).decode('ascii'),
        'bulk_filename':  bulk_filename,
        'label_b64':      base64.b64encode(label_csv_bytes).decode('ascii'),
        'label_filename': label_filename,
        'log':            log[:50],
        'details':        details,      # 结构化明细，供 confirm-update 写库
    }


# ── API: 平均出单点击数配置 ────────────────────────────────────────────────────

@app.get('/api/config/avg-clicks')
def api_get_avg_clicks():
    return db.get_config('avg_clicks', {})


@app.post('/api/config/avg-clicks')
def api_set_avg_clicks(data: dict):
    db.set_config('avg_clicks', data)
    return {'ok': True}


# ── API: 目标 ACoS 配置 ────────────────────────────────────────────────────────

@app.get('/api/config/acos-targets')
def api_get_acos_targets():
    return db.get_config('acos_targets', {})


@app.post('/api/config/acos-targets')
def api_set_acos_targets(data: dict):
    db.set_config('acos_targets', data)
    return {'ok': True}


@app.post('/api/analysis/mode1/confirm-update')
async def api_mode1_confirm_update(body: dict):
    """
    用户确认已将 Bulk 文件上传到亚马逊广告后台，记录本次调价操作。
    请求体字段：product_target, bulk_filename, label_filename,
               orders_threshold, updated_count, log_lines
    """
    try:
        ta = body.get('target_acos')
        ac = body.get('avg_clicks_per_order')
        log_id = db.save_bid_update_log(
            product_target       = body.get('product_target', ''),
            bulk_filename        = body.get('bulk_filename',  ''),
            label_filename       = body.get('label_filename', ''),
            report_start         = body.get('report_start',  ''),
            report_end           = body.get('report_end',    ''),
            orders_threshold     = int(body.get('orders_threshold', 10)),
            up_orders_threshold  = int(body.get('up_orders_threshold', 2)),
            updated_count        = int(body.get('updated_count', 0)),
            log_lines            = body.get('log_lines', []),
            target_acos          = float(ta) if ta is not None else None,
            avg_clicks_per_order = float(ac) if ac is not None else None,
            country              = body.get('country', ''),
        )
        db.save_bid_update_details(log_id, body.get('details', []))
    except Exception as e:
        raise HTTPException(500, f'记录失败: {e}')

    return {'ok': True, 'log_id': log_id}


@app.get('/api/analysis/mode1/update-logs')
def api_mode1_update_logs(limit: int = 100, country: str = ''):
    """返回竞价更新历史记录列表（不含明细）。country 非空时只返回该国家记录。"""
    return db.list_bid_update_logs(limit=limit, country=country)


@app.get('/api/analysis/mode1/update-logs/{log_id}/details')
def api_mode1_update_log_details(log_id: int):
    """返回某次竞价更新的所有明细行（每行对应 Bulk 中一个 Operation=Update 的条目）。"""
    return db.get_bid_update_details(log_id)


@app.get('/api/analysis/mode1/campaign-weekly-stats')
def api_campaign_weekly_stats(country: str, date_from: str, date_to: str):
    """返回指定日期范围内各广告活动的聚合指标，用于 L3 周次筛选。"""
    result = db.get_campaign_stats_by_date_range(country, date_from, date_to)
    return {'data': result}


@app.get('/api/analysis/mode1/campaign-trend')
def api_campaign_trend(campaign: str, country: str, date_from: str, date_to: str):
    """返回单一广告活动的周趋势、日趋势和调价事件，用于 L3 趋势面板。"""
    return db.get_campaign_trend(campaign, country, date_from, date_to)


@app.get('/api/analysis/product-trend')
def api_product_trend(product: str, country: str, date_from: str, date_to: str):
    """返回单品日维度广告指标 + 广告类型分组，用于单品趋势分析面板。"""
    engine = db.get_engine()
    with engine.connect() as conn:
        daily_rows = conn.execute(text("""
            SELECT 日期,
                   SUM(曝光量) AS im, SUM(点击) AS cl, SUM(广告订单) AS or_,
                   SUM(花费) AS sp, SUM(广告销售额) AS sl
            FROM raw_camp_lx
            WHERE 广告活动 LIKE :prefix AND 国家 = :country
              AND 日期 BETWEEN :date_from AND :date_to
            GROUP BY 日期
            ORDER BY 日期
        """), {"prefix": f"{product}%", "country": country, "date_from": date_from, "date_to": date_to}).fetchall()

        type_rows = conn.execute(text("""
            SELECT 日期,
                   CASE
                     WHEN 广告活动 LIKE '%_SB%' OR 广告活动 LIKE 'SB%' THEN 'SB'
                     WHEN 广告活动 LIKE '%_SD%' OR 广告活动 LIKE 'SD%' THEN 'SD'
                     ELSE 'SP'
                   END AS 类型,
                   SUM(曝光量) AS im, SUM(点击) AS cl, SUM(广告订单) AS or_,
                   SUM(花费) AS sp, SUM(广告销售额) AS sl
            FROM raw_camp_lx
            WHERE 广告活动 LIKE :prefix AND 国家 = :country
              AND 日期 BETWEEN :date_from AND :date_to
            GROUP BY 日期,
                   CASE
                     WHEN 广告活动 LIKE '%_SB%' OR 广告活动 LIKE 'SB%' THEN 'SB'
                     WHEN 广告活动 LIKE '%_SD%' OR 广告活动 LIKE 'SD%' THEN 'SD'
                     ELSE 'SP'
                   END
            ORDER BY 日期, sp DESC
        """), {"prefix": f"{product}%", "country": country, "date_from": date_from, "date_to": date_to}).fetchall()

        camp_rows = conn.execute(text("""
            SELECT 日期, 广告活动,
                   SUM(曝光量) AS im, SUM(点击) AS cl, SUM(广告订单) AS or_,
                   SUM(花费) AS sp, SUM(广告销售额) AS sl
            FROM raw_camp_lx
            WHERE 广告活动 LIKE :prefix AND 国家 = :country
              AND 日期 BETWEEN :date_from AND :date_to
            GROUP BY 日期, 广告活动
            ORDER BY 日期, sp DESC
        """), {"prefix": f"{product}%", "country": country, "date_from": date_from, "date_to": date_to}).fetchall()

    daily = []
    for r in daily_rows:
        date, im, cl, or_, sp, sl = str(r[0]), float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0)
        daily.append({
            "date": date, "im": im, "cl": cl, "or_": or_, "sp": round(sp, 2), "sl": round(sl, 2),
            "ctr": round(cl / im * 100, 2) if im > 0 else None,
            "cvr": round(or_ / cl * 100, 2) if cl > 0 else None,
            "acos": round(sp / sl * 100, 2) if sl > 0 else None,
        })

    by_type: dict = {}
    for r in type_rows:
        date, typ, im, cl, or_, sp, sl = str(r[0]), r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0), float(r[6] or 0)
        by_type.setdefault(date, []).append({
            "type": typ, "im": im, "cl": cl, "or_": or_, "sp": round(sp, 2), "sl": round(sl, 2)
        })

    # 每日按花费排序，每天最多保留 top 8 条活动
    by_camp: dict = {}
    for r in camp_rows:
        date, camp, im, cl, or_, sp, sl = str(r[0]), str(r[1]), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0), float(r[6] or 0)
        lst = by_camp.setdefault(date, [])
        if len(lst) < 8:
            lst.append({"n": camp, "im": im, "cl": cl, "or_": or_, "sp": round(sp, 2), "sl": round(sl, 2)})

    return {"daily": daily, "by_type": by_type, "by_camp": by_camp}


@app.get('/api/health')
def health():
    reports = db.list_reports()
    return {'status': 'ok', 'report_count': len(reports)}


# ── API: 数据库查询 ────────────────────────────────────────────────────────────

@app.post('/api/db/query')
def db_sql_query(body: dict):
    """执行任意 SQL 语句并返回结果"""
    sql = (body.get('sql') or '').strip()
    if not sql:
        raise HTTPException(400, 'SQL 不能为空')
    try:
        with db.get_conn() as conn:
            cur = conn.execute(sql)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = [list(r) for r in cur.fetchmany(2000)]  # 最多返回 2000 行
                return {'cols': cols, 'rows': rows, 'rowcount': len(rows)}
            else:
                conn.commit()
                return {'cols': [], 'rows': [], 'rowcount': cur.rowcount,
                        'message': f'执行成功，影响 {cur.rowcount} 行'}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get('/api/db/schema')
def db_schema():
    """返回数据库表结构"""
    with db.get_conn() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        result = {}
        for (tbl,) in tables:
            cols = conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()
            result[tbl] = [{'cid': c[0], 'name': c[1], 'type': c[2]} for c in cols]
    return result
