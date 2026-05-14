"""
gen_data.py — Excel DataFrame → ads_compact dict
接受 pandas DataFrame，返回完整数据字典（供 API 存入数据库）
"""

import pandas as pd
import numpy as np

# ── 商品配置（按项目修改） ────────────────────────────────────────────────────
PRODS = ['SL', 'DL', 'DSL2', 'Toy', 'ToyDH', 'MFL', 'SFM', 'ShortL', 'WB']

TYPE_MAP = {'SP': 'SP', 'SB': 'SB2', 'SB2': 'SB2', 'SD': 'SD'}


def classify_port(name: str):
    """根据广告组合名称推断 (商品, 类别)"""
    prod, cat = 'Other', '7_Other'
    n = str(name)
    for p in PRODS:
        if n.startswith(p + '_') or n == p:
            prod = p
            break
    if   '_SB' in n or n.startswith('SB'):          cat = '1_SB'
    elif '_SD' in n or n.startswith('SD'):           cat = '2_SD'
    elif 'KW精准' in n:                               cat = '3_SP_KW精准'
    elif 'KW拓展' in n:                               cat = '4_SP_KW拓展'
    elif 'ASIN精准' in n or 'ASIN进攻' in n:          cat = '5_SP_ASIN精准'
    elif 'ASIN拓展' in n or 'ASIN防守' in n:          cat = '6_SP_ASIN拓展'
    return prod, cat


# ── 指标计算 ──────────────────────────────────────────────────────────────────

def _safe(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 0.0
    return round(float(v), 4)


def _parse_pct(v):
    if pd.isna(v):
        return None
    s = str(v).strip()
    if s in ('--', '有花费无销售额', '', 'nan'):
        return None
    try:
        return round(float(s.rstrip('%')), 4)
    except Exception:
        return None


def _metrics(grp: pd.DataFrame) -> dict:
    sp  = _safe(grp['花费'].sum())
    sl  = _safe(grp['广告销售额'].sum())
    cl  = int(grp['点击'].sum())
    im  = int(grp['曝光量'].sum())
    or_ = int(grp['广告订单'].sum())
    ac  = round(sp / sl * 100, 2) if sl > 0 else None
    ro  = round(sl / sp, 2)       if sp > 0 else None
    ct  = round(cl / im * 100, 4) if im > 0 else None
    cv  = round(or_ / cl * 100, 4) if cl > 0 else None
    cp  = round(sp / cl, 4)        if cl > 0 else None
    return dict(sp=sp, sl=sl, cl=cl, im=im, or_=or_,
                ac=ac, ro=ro, ct=ct, cv=cv, cp=cp)


def _weekly_metrics(df: pd.DataFrame, weeks: list) -> list:
    return [
        _metrics(df[df['week'] == w]) if not df[df['week'] == w].empty else None
        for w in weeks
    ]


def _weekly_acos(df: pd.DataFrame, weeks: list) -> list:
    wa = []
    for w in weeks:
        sub = df[df['week'] == w]
        if sub.empty:
            wa.append(None)
            continue
        sp = _safe(sub['花费'].sum())
        sl = _safe(sub['广告销售额'].sum())
        wa.append(round(sp / sl * 100, 2) if sl > 0 else None)
    return wa


def _daily_agg(df: pd.DataFrame, mask=None) -> dict:
    sub = df[mask] if mask is not None else df
    out = {}
    for dt, grp in sub.groupby('日期'):
        out[dt.strftime('%Y-%m-%d')] = _metrics(grp)
    return out


def _camp_status(sp, cl, im, or_) -> str:
    if or_ > 0: return 'converting'
    if cl > 0:  return 'click_no_order'
    if im > 0:  return 'imp_only'
    return 'no_imp'


# ── 主处理函数 ────────────────────────────────────────────────────────────────

def process(df_c: pd.DataFrame, df_p: pd.DataFrame) -> dict:
    """
    df_c: 广告活动每日明细（领星"全部"导出）
    df_p: 广告组合每日明细（领星"广告组合"导出）
    返回: 完整的 ads_compact dict
    """
    # ── 日期解析 ────────────────────────────────────────────────────────────
    df_c = df_c.copy()
    df_p = df_p.copy()
    df_c['日期'] = pd.to_datetime(df_c['日期'])
    df_p['日期'] = pd.to_datetime(df_p['日期'])

    for col in ['ACoS', 'CTR', 'CVR']:
        if col in df_c.columns: df_c[col] = df_c[col].apply(_parse_pct)
        if col in df_p.columns: df_p[col] = df_p[col].apply(_parse_pct)

    # ── 自动检测周次 ─────────────────────────────────────────────────────────
    iso_weeks = sorted(df_c['日期'].apply(lambda d: d.isocalendar().week).unique())
    WEEKS = [f'W{w}' for w in iso_weeks]

    def get_week(dt): return f'W{dt.isocalendar().week}'
    df_c['week'] = df_c['日期'].apply(get_week)
    df_p['week'] = df_p['日期'].apply(get_week)
    df_c = df_c[df_c['week'].isin(WEEKS)].copy()
    df_p = df_p[df_p['week'].isin(WEEKS)].copy()

    # 周次日期范围 {W15: '4/6-4/12', ...}
    wk_dates = {}
    for w in WEEKS:
        sub = df_c[df_c['week'] == w]['日期']
        if sub.empty: continue
        d0, d1 = sub.min(), sub.max()
        wk_dates[w] = f'{d0.month}/{d0.day}-{d1.month}/{d1.day}'

    # ── 商品/类别分类 ────────────────────────────────────────────────────────
    df_c['prod'], df_c['cat'] = zip(*df_c['广告组合'].apply(classify_port))
    df_p['prod'], df_p['cat'] = zip(*df_p['广告组合'].apply(classify_port))

    # ── 总体(OV) ─────────────────────────────────────────────────────────────
    ov = dict(t=_metrics(df_c), w=_weekly_metrics(df_c, WEEKS))

    # ── 商品 ─────────────────────────────────────────────────────────────────
    prods = {}
    for prod in sorted(df_c['prod'].unique()):
        sub = df_c[df_c['prod'] == prod]
        prods[prod] = dict(t=_metrics(sub), w=_weekly_metrics(sub, WEEKS))

    # ── 类别 ─────────────────────────────────────────────────────────────────
    cats = {}
    for cat in sorted(df_c['cat'].unique()):
        sub = df_c[df_c['cat'] == cat]
        cats[cat] = dict(t=_metrics(sub), w=_weekly_metrics(sub, WEEKS))

    # ── 商品×类别 ────────────────────────────────────────────────────────────
    prod_cats = {}
    for prod in sorted(df_c['prod'].unique()):
        prod_cats[prod] = {}
        sub_p = df_c[df_c['prod'] == prod]
        for cat in sorted(sub_p['cat'].unique()):
            sub_pc = sub_p[sub_p['cat'] == cat]
            prod_cats[prod][cat] = dict(
                t=_metrics(sub_pc), w=_weekly_metrics(sub_pc, WEEKS)
            )

    # ── 广告组合 ─────────────────────────────────────────────────────────────
    ports = []
    for port_name in sorted(df_p['广告组合'].unique()):
        sub = df_p[df_p['广告组合'] == port_name]
        prod, cat = classify_port(port_name)
        ports.append(dict(
            n=str(port_name), p=prod, c=cat,
            t=_metrics(sub), wa=_weekly_acos(sub, WEEKS)
        ))

    # ── 广告活动 ─────────────────────────────────────────────────────────────
    camp_col = '广告活动' if '广告活动' in df_c.columns else '活动名称'
    type_col = '类型'     if '类型'     in df_c.columns else None
    camps = []

    for camp_name, grp in df_c.groupby(camp_col):
        prod, cat  = classify_port(grp['广告组合'].iloc[0])
        port_name  = str(grp['广告组合'].iloc[0])
        ty_raw     = grp[type_col].iloc[0] if type_col else 'SP'
        ty         = TYPE_MAP.get(str(ty_raw).strip(), 'SP')
        m          = _metrics(grp)
        st         = _camp_status(m['sp'], m['cl'], m['im'], m['or_'])
        camps.append(dict(
            n=str(camp_name), po=port_name, p=prod, c=cat, ty=ty, st=st,
            sp=m['sp'], sl=m['sl'], cl=m['cl'], im=m['im'], or_=m['or_'],
            ac=m['ac'], ro=m['ro'], ct=m['ct'], cv=m['cv'], cp=m['cp'],
            wa=_weekly_acos(grp, WEEKS)
        ))

    camps.sort(key=lambda x: x['sp'], reverse=True)

    # ── 日明细 ───────────────────────────────────────────────────────────────
    daily_ov = _daily_agg(df_c)

    daily_prod = {}
    for prod in df_c['prod'].unique():
        daily_prod[prod] = _daily_agg(df_c, df_c['prod'] == prod)

    daily_cat = {}
    for cat in df_c['cat'].unique():
        daily_cat[cat] = _daily_agg(df_c, df_c['cat'] == cat)

    daily_prod_cat = {}
    for prod in df_c['prod'].unique():
        daily_prod_cat[prod] = {}
        sub_p = df_c[df_c['prod'] == prod]
        for cat in sub_p['cat'].unique():
            daily_prod_cat[prod][cat] = _daily_agg(sub_p, sub_p['cat'] == cat)

    daily_port = {}
    for port_name in df_p['广告组合'].unique():
        daily_port[str(port_name)] = _daily_agg(df_p, df_p['广告组合'] == port_name)

    return dict(
        wk=WEEKS,
        wk_dates=wk_dates,
        ov=ov,
        cats=cats,
        prods=prods,
        ports=ports,
        camps=camps,
        prod_cats=prod_cats,
        daily_ov=daily_ov,
        daily_prod=daily_prod,
        daily_cat=daily_cat,
        daily_prod_cat=daily_prod_cat,
        daily_port=daily_port,
    )
