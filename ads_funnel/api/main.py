"""
main.py — FastAPI 后端
启动: uvicorn api.main:app --reload  （从 ads_funnel/ 目录执行）
"""

import io, json
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db, export, gen_data

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title='广告漏斗分析 v2.0', version='2.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'], allow_methods=['*'], allow_headers=['*'],
)

FRONTEND = Path(__file__).parent.parent / 'frontend'


@app.on_event('startup')
def startup():
    db.init_db()
    db.rebuild_from_raw()       # 从原始数据重建 JSON（若 raw 表有数据）
    print('Database ready:', db.DB_PATH)


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
    report_id = id if id else reports[0]['id']
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

    try:
        df_c = pd.read_excel(io.BytesIO(camp_bytes))
        df_p = pd.read_excel(io.BytesIO(port_bytes))
    except Exception as e:
        raise HTTPException(400, f'Excel 读取失败: {e}')

    try:
        db.upsert_raw(df_c, df_p)
        report_ids = db.rebuild_from_raw()
    except Exception as e:
        raise HTTPException(500, f'数据处理失败: {e}')

    if not report_ids:
        raise HTTPException(500, 'raw 数据为空，导入失败')

    reports = [db.get_report(rid) for rid in report_ids]
    return {
        'ids':     report_ids,
        'reports': [{'id': r['id'], 'title': r['title'], 'weeks': r['weeks']} for r in reports if r],
    }


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
