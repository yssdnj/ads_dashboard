# 批量竞价优化-6轮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「批量竞价优化-6轮」tab，实现每轮递增一周的六轮分析 + 向好趋势排除规则，原有三轮 tab 完全不动。

**Architecture:** 后端新增独立函数 `run_multi_round_analysis_6r()` 和独立路由 `/api/analysis/mode1/bid-optimize-6r`；前端新增独立 tab，7 个子标签（Consensus 优先）及页面规则说明。所有新增代码与现有代码完全隔离，不修改任何现有函数/路由/HTML 元素。

**Tech Stack:** Python / FastAPI / pandas / SQLAlchemy / Vanilla JS / HTML+CSS

---

## 文件改动范围

| 文件 | 操作 | 内容 |
|------|------|------|
| `ads_funnel/api/targeting_analysis.py` | 追加 | `_is_improving_6r()` + `run_multi_round_analysis_6r()` |
| `ads_funnel/api/main.py` | 追加 | `POST /api/analysis/mode1/bid-optimize-6r` + `_mode1_6r_cache` |
| `ads_funnel/template.html` | 追加 | 「批量竞价优化-6轮」tab 按钮 + 完整 HTML/JS（新 ID 前缀 `tgt6-`） |

**绝对不修改：**
- `run_multi_round_analysis()`（三轮函数）
- `/api/analysis/mode1/run`（三轮路由）
- `#ana-targeting` div 及其所有子元素
- `switchRoundTab()` / `runMode1Analysis()` 等现有 JS 函数

---

## Task 1：后端 — 新增 `run_multi_round_analysis_6r()`

**Files:**
- Modify: `ads_funnel/api/targeting_analysis.py`（文件末尾追加）

- [ ] **Step 1：追加辅助函数 `_is_improving_6r()`**

在 `targeting_analysis.py` 末尾（`run_multi_round_analysis` 函数结束后）追加：

```python
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 六轮分析（R1=1w … R6=6w，每轮递增一周 + 向好趋势排除）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_improving_6r(hit_set: set[str]) -> bool:
    """
    判断是否为向好趋势（不应降价）。
    - =3/6 命中且仅 {R4,R5,R6}：近 3 周干净，明显好转
    - =4/6 命中且仅 {R3,R4,R5,R6}（R1/R2 均未中）：近 2 周干净，趋势向好
    ≥5/6 命中时无条件返回 False（信号足够强）。
    """
    if len(hit_set) == 3 and hit_set == {'R4', 'R5', 'R6'}:
        return True
    if len(hit_set) == 4 and hit_set == {'R3', 'R4', 'R5', 'R6'}:
        return True
    return False
```

- [ ] **Step 2：追加主函数 `run_multi_round_analysis_6r()`**

紧接 Step 1 代码后追加：

```python
def run_multi_round_analysis_6r(
    tar_df:               pd.DataFrame,
    ap_df:                pd.DataFrame,
    week_sundays:         list,           # 升序 Timestamp 列表，最多 6 个
    product_target:       str,
    asins:                list[str],
    target_acos:          float,
    avg_clicks_per_order: float,
    core_sales_share:     float = 0.20,
) -> dict:
    """
    六轮多窗口分析 + 投票 consensus（含向好趋势排除）。

    轮次（日历周，周日开始）：
      R1 — 最近 1 周  R2 — 最近 2 周  R3 — 最近 3 周
      R4 — 最近 4 周  R5 — 最近 5 周  R6 — 全部（最多 6 周）

    Consensus 规则：
      - ≥3/6 轮命中 → 候选
      - =3/6 且仅 {R4,R5,R6} 命中 → 排除（向好）
      - =4/6 且仅 {R3,R4,R5,R6} 命中 → 排除（向好）
      - adj_pct 取各命中轮绝对值最小（最保守）
      - metrics 来自 R6，label 来自 R1
    """
    n = len(week_sundays)
    to_sunday = week_sundays[-1]

    # 六轮起始周（数据不足时退化到 week_sundays[0]，不报错）
    r_from = {
        'R1': week_sundays[-1],
        'R2': week_sundays[max(-2, -n)],
        'R3': week_sundays[max(-3, -n)],
        'R4': week_sundays[max(-4, -n)],
        'R5': week_sundays[max(-5, -n)],
        'R6': week_sundays[0],
    }

    rounds: dict[str, dict] = {}
    for rname, from_sunday in r_from.items():
        t, a = _filter_by_week_range(tar_df, ap_df, from_sunday, to_sunday)
        week_end = to_sunday + pd.Timedelta(days=6)
        date_label = f'{from_sunday.strftime("%m/%d")}-{week_end.strftime("%m/%d")}'
        if t.empty or a.empty:
            rounds[rname] = {
                'rows': [], 'summary': {}, 'layers': {},
                'date_start': from_sunday.strftime('%Y-%m-%d'),
                'date_end':   week_end.strftime('%Y-%m-%d'),
                'week_label': date_label,
                'error': '该时间窗口内数据为空',
            }
            continue
        res = run_analysis(
            ap_df                = a,
            tar_df               = t,
            product_target       = product_target,
            asins                = asins,
            target_acos          = target_acos,
            avg_clicks_per_order = avg_clicks_per_order,
            core_sales_share     = core_sales_share,
            analysis_days        = 0,
        )
        rounds[rname] = {
            'rows':         res.get('rows', []),
            'summary':      res.get('summary', {}),
            'layers':       res.get('layers', {}),
            'label_counts': res.get('label_counts', {}),
            'date_start':   from_sunday.strftime('%Y-%m-%d'),
            'date_end':     week_end.strftime('%Y-%m-%d'),
            'week_label':   date_label,
            'error':        res.get('error'),
        }

    # ── 投票 consensus ──────────────────────────────────────────────────────────
    vote: dict[tuple, dict] = {}          # key → {rname: adj_pct_decimal}
    r6_row_map: dict[tuple, dict] = {}    # metrics 来源
    r1_row_map: dict[tuple, dict] = {}    # label 来源

    for rname in ('R6', 'R5', 'R4', 'R3', 'R2', 'R1'):
        for row in rounds[rname].get('rows', []):
            if row.get('label') not in _ADJ_LABELS:
                continue
            adj_dec = _parse_adj_pct(row.get('adj_pct'))
            if adj_dec is None:
                continue
            key = (row.get('campaign'), row.get('ad_group'), row.get('targeting'))
            if key not in vote:
                vote[key] = {}
            vote[key][rname] = adj_dec
            if rname == 'R6':
                r6_row_map[key] = row
            if rname == 'R1':
                r1_row_map[key] = row

    consensus_rows: list[dict] = []
    for key, hit_map in vote.items():
        hit_set = set(hit_map.keys())

        # 门槛：≥3/6
        if len(hit_set) < 3:
            continue

        # 排除向好趋势
        if _is_improving_6r(hit_set):
            continue

        # 最保守 adj_pct
        best_adj = min(hit_map.values(), key=abs)
        hit_rounds_str = ','.join(sorted(hit_map.keys()))

        # metrics 取 R6，label 取 R1，fallback 任意命中轮
        base = r6_row_map.get(key) or r1_row_map.get(key) or next(
            (rounds[r]['rows'] for r in hit_set if rounds[r].get('rows')), [{}]
        )
        if isinstance(base, list):
            base = next(
                (ro for ro in base
                 if (ro.get('campaign'), ro.get('ad_group'), ro.get('targeting')) == key),
                {}
            )
        row = dict(base)
        row['label']      = (r1_row_map.get(key) or base).get('label', base.get('label', ''))
        row['adj_pct']    = f'{int(best_adj * 100)}%'
        row['action']     = '↘ 降价'
        row['reason']     = f'六轮分析命中（{hit_rounds_str}），取最保守降幅'
        row['hit_rounds'] = hit_rounds_str
        consensus_rows.append(row)

    r6 = rounds.get('R6', {})
    return {
        'R1': rounds.get('R1', {}),
        'R2': rounds.get('R2', {}),
        'R3': rounds.get('R3', {}),
        'R4': rounds.get('R4', {}),
        'R5': rounds.get('R5', {}),
        'R6': r6,
        'consensus': {
            'rows':  consensus_rows,
            'count': len(consensus_rows),
        },
        'product_target': product_target,
        'report_start':   r6.get('date_start', ''),
        'report_end':     r6.get('date_end',   ''),
    }
```

- [ ] **Step 3：验证语法无误**

```bash
cd C:\Users\admin\Desktop\python\ads_dashboard
python -c "from ads_funnel.api import targeting_analysis; print('OK')"
```

期望输出：`OK`，无报错。

- [ ] **Step 4：commit**

```bash
git add ads_funnel/api/targeting_analysis.py
git commit -m "feat: add run_multi_round_analysis_6r() with 6-round voting and improving-trend exclusion"
```

---

## Task 2：后端 — 新增路由 `/api/analysis/mode1/bid-optimize-6r`

**Files:**
- Modify: `ads_funnel/api/main.py`

- [ ] **Step 1：在 `main.py` 中找到 `_mode1_cache` 定义处，在其后追加 `_mode1_6r_cache`**

找到：
```python
_mode1_cache: dict[str, dict] = {}
```

在该行**之后**追加：
```python
_mode1_6r_cache: dict[str, dict] = {}   # 六轮分析结果缓存
```

- [ ] **Step 2：在 `/api/analysis/mode1/export-bulk` 路由之前，追加新路由**

找到：
```python
# ── API: Mode 1 → 更新 Bulk 文件 ──────────────────────────────────────────────

@app.post('/api/analysis/mode1/export-bulk')
```

在该注释行**之前**插入：

```python
# ── API: Mode 1 → 六轮分析 ────────────────────────────────────────────────────

@app.post('/api/analysis/mode1/bid-optimize-6r')
async def api_mode1_bid_optimize_6r(
    product_target:       str   = Form(...),
    target_acos:          float = Form(...),        # 百分比值，如 20.0 表示 20%
    avg_clicks_per_order: float = Form(...),
    core_sales_share:     float = Form(0.2),
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

    tar_df, ap_df, week_sundays = db.get_tar_ap_for_analysis(n_weeks=6)
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
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f'六轮分析失败: {e}')

    if result.get('R6', {}).get('error'):
        raise HTTPException(400, result['R6']['error'])

    cid = str(uuid.uuid4())
    _mode1_6r_cache[cid] = {
        'rows':           result['consensus']['rows'],
        'product_target': result.get('product_target', ''),
        'report_start':   result.get('report_start', ''),
        'report_end':     result.get('report_end', ''),
    }
    result['cache_id'] = cid
    return result

```

- [ ] **Step 3：验证服务器启动无报错**

```bash
cd C:\Users\admin\Desktop\python\ads_dashboard
python -m uvicorn ads_funnel.api.main:app --port 5001 --reload
```

期望：`Application startup complete.`，无 ImportError / SyntaxError。

- [ ] **Step 4：commit**

```bash
git add ads_funnel/api/main.py
git commit -m "feat: add POST /api/analysis/mode1/bid-optimize-6r route"
```

---

## Task 3：前端 — 新增「批量竞价优化-6轮」Tab

**Files:**
- Modify: `ads_funnel/template.html`

本 Task 分三个子步骤：Tab 按钮、HTML 结构、JavaScript 逻辑。

---

### Task 3a：添加 Tab 按钮

- [ ] **Step 1：在「批量竞价优化」按钮后追加新按钮**

找到：
```html
      <button class="ana-view-btn" onclick="switchAnaView('targeting')" id="anabtn-targeting">批量竞价优化</button>
```

改为：
```html
      <button class="ana-view-btn" onclick="switchAnaView('targeting')" id="anabtn-targeting">批量竞价优化</button>
      <button class="ana-view-btn" onclick="switchAnaView('targeting6')" id="anabtn-targeting6">批量竞价优化-6轮</button>
```

- [ ] **Step 2：在 `#ana-history` div 后追加新内容容器**

找到：
```html
    <div id="ana-history" style="display:none"></div>
    <div id="ana-targeting" style="display:none">
```

在 `<div id="ana-targeting"` 之前插入：
```html
    <div id="ana-targeting6" style="display:none"></div>
```

---

### Task 3b：添加六轮 Tab 完整 HTML 结构

- [ ] **Step 1：将 `#ana-targeting6` div 的内容替换为完整 HTML**

找到：
```html
    <div id="ana-targeting6" style="display:none"></div>
```

替换为：

```html
    <div id="ana-targeting6" style="display:none">
<div class="tgt-wrap" style="margin-top:12px">

  <!-- ═══ 规则说明 ═══ -->
  <div class="tgt-card" style="margin-bottom:10px;background:var(--tgbg,#f0fdf4);border:1.5px solid var(--tg,#16a34a)">
    <div style="font-size:13px;font-weight:700;color:var(--tgd,#15803d);margin-bottom:8px">📋 六轮分析规则说明</div>
    <div style="font-size:12px;color:#374151;line-height:1.8">
      <div><strong>轮次定义：</strong>R1=最近1周 · R2=最近2周 · … · R6=最近6周（数据最全）</div>
      <div style="margin-top:4px"><strong>Consensus 门槛：</strong>≥ 3/6 轮命中调价标签</div>
      <div style="margin-top:4px"><strong>向好趋势排除（不降价）：</strong>
        <span style="display:inline-block;background:#fef9c3;border-radius:4px;padding:1px 6px;margin:0 2px">命中=3且仅R4~R6</span>
        或
        <span style="display:inline-block;background:#fef9c3;border-radius:4px;padding:1px 6px;margin:0 2px">命中=4且仅R3~R6（R1/R2 近期已好转）</span>
      </div>
      <div style="margin-top:4px"><strong>降幅策略：</strong>取各命中轮中绝对值最小（最保守）的降幅 · 指标来自 R6 · 标签来自 R1</div>
    </div>
  </div>

  <!-- ═══ 模块一：分析执行 ═══ -->
  <div class="tgt-module-hd" onclick="tgt6ToggleModule(1)">
    <div class="tgt-module-badge m1">01</div>
    <div class="tgt-module-title">分析执行</div>
    <div class="tgt-module-sub">选择产品 · 设置参数 · 执行六轮分析</div>
    <div class="tgt-module-chev" id="tgt6-m1-chev">▼</div>
  </div>
  <div class="tgt-module-body" id="tgt6-m1-body">
    <div class="tgt-card">
      <div class="tgt-form-row">
        <label class="tgt-label">产品目标</label>
        <select id="tgt6-product" class="tgt-select"></select>
      </div>
      <div class="tgt-form-row">
        <label class="tgt-label">目标 ACoS (%)</label>
        <input id="tgt6-acos" type="number" class="tgt-input" value="20" min="1" max="100">
      </div>
      <div class="tgt-form-row">
        <label class="tgt-label">平均出单点击数</label>
        <input id="tgt6-cpo" type="number" class="tgt-input" value="10" min="1" max="999">
      </div>
    </div>
    <button class="tgt-run-btn" id="tgt6-run-btn" onclick="runMode1Analysis6r()" style="margin-top:8px">
      🚀 开始六轮分析
    </button>
    <div class="tgt-prog" id="tgt6-prog"><div class="tgt-prog-fill" id="tgt6-prog-fill" style="width:0%"></div></div>
    <div id="tgt6-status" style="font-size:12px;color:var(--g6);margin-top:6px;min-height:18px"></div>
  </div>

  <!-- ═══ 模块二：分析结果 ═══ -->
  <div class="tgt-module-hd" onclick="tgt6ToggleModule(2)" style="margin-top:10px">
    <div class="tgt-module-badge m2">02</div>
    <div class="tgt-module-title">分析结果</div>
    <div class="tgt-module-sub">Consensus · R1~R6 六轮结果</div>
    <div class="tgt-module-chev" id="tgt6-m2-chev">▶</div>
  </div>
  <div class="tgt-module-body closed" id="tgt6-m2-body">
    <div class="round-tabs" id="tgt6-round-tabs" style="display:none">
      <button class="round-tab" id="tgt6-rtab-consensus" onclick="switchRoundTab6r('consensus')">Consensus<span class="rt-badge" id="tgt6-rtab-consensus-cnt"></span></button>
      <button class="round-tab" id="tgt6-rtab-R1" onclick="switchRoundTab6r('R1')">R1<span class="rt-sub">1周</span></button>
      <button class="round-tab" id="tgt6-rtab-R2" onclick="switchRoundTab6r('R2')">R2<span class="rt-sub">2周</span></button>
      <button class="round-tab" id="tgt6-rtab-R3" onclick="switchRoundTab6r('R3')">R3<span class="rt-sub">3周</span></button>
      <button class="round-tab" id="tgt6-rtab-R4" onclick="switchRoundTab6r('R4')">R4<span class="rt-sub">4周</span></button>
      <button class="round-tab" id="tgt6-rtab-R5" onclick="switchRoundTab6r('R5')">R5<span class="rt-sub">5周</span></button>
      <button class="round-tab" id="tgt6-rtab-R6" onclick="switchRoundTab6r('R6')">R6<span class="rt-sub">6周</span></button>
    </div>
    <div class="round-panel" id="tgt6-panel-consensus"><div class="tgt-placeholder">运行分析后结果将在此显示</div></div>
    <div class="round-panel" id="tgt6-panel-R1"></div>
    <div class="round-panel" id="tgt6-panel-R2"></div>
    <div class="round-panel" id="tgt6-panel-R3"></div>
    <div class="round-panel" id="tgt6-panel-R4"></div>
    <div class="round-panel" id="tgt6-panel-R5"></div>
    <div class="round-panel" id="tgt6-panel-R6"></div>
  </div>

  <!-- ═══ 模块三：竞价批量更新 ═══ -->
  <div class="tgt-module-hd" onclick="tgt6ToggleModule(3)" style="margin-top:10px">
    <div class="tgt-module-badge m3">03</div>
    <div class="tgt-module-title">竞价批量更新</div>
    <div class="tgt-module-sub">筛选 → 调价 → 写回 Bulk · 先完成模块一分析再执行</div>
    <div class="tgt-module-chev" id="tgt6-m3-chev">▶</div>
  </div>
  <div class="tgt-module-body closed" id="tgt6-m3-body">
    <div class="tgt-card" style="margin-top:8px">
      <div class="tgt-info-section">
        <div class="tgt-info-hd">📌 筛选条件（与三轮版相同）</div>
        <div class="tgt-info-row">
          <span class="ir-label">条件 ①  标签</span>
          <span>包含 <span class="tgt-info-tag y">⚠️ 高ACoS出单</span> 或 <span class="tgt-info-tag r">🔴 高点击不出单</span></span>
        </div>
        <div class="tgt-info-row" style="align-items:center">
          <span class="ir-label">条件 ②  订单数</span>
          <span>orders &lt; </span>
          <input id="tgt6-orders-thresh" type="number" min="1" max="999" value="10"
            style="width:64px;padding:4px 8px;font-size:13px;font-weight:700;border:1.5px solid var(--tg);border-radius:6px;background:var(--tgbg);color:var(--tgd);text-align:center;outline:none">
        </div>
      </div>
      <div class="tgt-info-section" style="margin-top:10px">
        <div class="tgt-info-hd">📂 上传 Bulk 文件</div>
        <input type="file" id="tgt6-bulk-file" accept=".xlsx"
          style="margin-top:6px;font-size:13px">
      </div>
      <button class="tgt-run-btn" id="tgt6-export-btn" onclick="exportBulk6r()" style="margin-top:10px">
        📥 生成调价 Bulk
      </button>
      <div id="tgt6-export-status" style="font-size:12px;color:var(--g6);margin-top:6px;min-height:18px"></div>
    </div>
  </div>

</div><!-- /.tgt-wrap -->
    </div><!-- /#ana-targeting6 -->
```

---

### Task 3c：添加 JavaScript 逻辑

- [ ] **Step 1：找到现有 JS 中 `runMode1Analysis` 函数末尾，在其后追加六轮相关 JS**

找到（接近文件末尾的 JS 区段）：
```javascript
async function runMode1Analysis(){
```

在该函数**完整结束**（找到对应的闭合 `}`）之后，追加以下代码块：

```javascript
/* ══════════════════════════════════════════════════════
   六轮分析 JS
══════════════════════════════════════════════════════ */

// 模块折叠（六轮专用，ID 前缀 tgt6-）
function tgt6ToggleModule(n){
  const body = document.getElementById(`tgt6-m${n}-body`);
  const chev = document.getElementById(`tgt6-m${n}-chev`);
  if(!body) return;
  const closed = body.classList.toggle('closed');
  if(chev) chev.textContent = closed ? '▶' : '▼';
}

// 初始化产品下拉（复用三轮版的产品列表）
function initTgt6Products(){
  const src = document.getElementById('tgt-product');
  const dst = document.getElementById('tgt6-product');
  if(!src || !dst) return;
  dst.innerHTML = src.innerHTML;
}

// 轮次 Tab 切换（六轮专用）
function switchRoundTab6r(rname){
  ['consensus','R1','R2','R3','R4','R5','R6'].forEach(r => {
    const tab   = document.getElementById(`tgt6-rtab-${r}`);
    const panel = document.getElementById(`tgt6-panel-${r}`);
    if(tab)   tab.classList.toggle('active', r === rname);
    if(panel) panel.style.display = r === rname ? '' : 'none';
  });
}

// 渲染单轮结果（复用三轮版的 _renderRoundPanel）
function _renderRoundPanel6r(rname, data){
  const panel = document.getElementById(`tgt6-panel-${rname}`);
  if(!panel) return;
  // 复用三轮版渲染逻辑（_renderRoundPanel 已在页面定义）
  _renderRoundPanel(panel, data, rname === 'consensus');
}

// 执行六轮分析
async function runMode1Analysis6r(){
  const product = document.getElementById('tgt6-product')?.value;
  const acos    = parseFloat(document.getElementById('tgt6-acos')?.value);
  const cpo     = parseFloat(document.getElementById('tgt6-cpo')?.value);
  if(!product){ alert('请选择产品目标'); return; }
  if(isNaN(acos)||acos<=0){ alert('请输入有效的目标 ACoS'); return; }
  if(isNaN(cpo)||cpo<=0){   alert('请输入有效的平均出单点击数'); return; }

  const btn    = document.getElementById('tgt6-run-btn');
  const status = document.getElementById('tgt6-status');
  const prog   = document.getElementById('tgt6-prog-fill');
  btn.disabled = true;
  status.textContent = '⏳ 六轮分析中…';
  if(prog) prog.style.width = '30%';

  const fd = new FormData();
  fd.append('product_target',       product);
  fd.append('target_acos',          acos);
  fd.append('avg_clicks_per_order', cpo);

  try{
    if(prog) prog.style.width = '60%';
    const resp = await fetch('api/analysis/mode1/bid-optimize-6r', {method:'POST', body:fd});
    if(!resp.ok){ const e=await resp.json(); throw new Error(e.detail||'分析失败'); }
    const d = await resp.json();
    if(prog) prog.style.width = '100%';

    // 保存 cache_id 供导出使用
    window._tgt6CacheId = d.cache_id;

    // 显示轮次 Tab 栏，展开模块二
    const tabs = document.getElementById('tgt6-round-tabs');
    if(tabs) tabs.style.display = '';
    const m2body = document.getElementById('tgt6-m2-body');
    if(m2body) m2body.classList.remove('closed');
    const m2chev = document.getElementById('tgt6-m2-chev');
    if(m2chev) m2chev.textContent = '▼';

    // 渲染各轮
    ['R1','R2','R3','R4','R5','R6'].forEach(r => _renderRoundPanel6r(r, d[r]));
    _renderRoundPanel6r('consensus', d.consensus);

    // 更新 Consensus 徽章数字
    const cnt = document.getElementById('tgt6-rtab-consensus-cnt');
    if(cnt) cnt.textContent = d.consensus?.count ?? '';

    // 默认选中 Consensus
    switchRoundTab6r('consensus');

    status.style.color = 'var(--gn)';
    status.textContent = `✅ 六轮分析完成，Consensus ${d.consensus?.count ?? 0} 条`;
  }catch(e){
    status.style.color = 'var(--rd)';
    status.textContent = '❌ ' + e.message;
  }finally{
    btn.disabled = false;
    if(prog) prog.style.width = '0%';
  }
}

// 导出六轮 Bulk
async function exportBulk6r(){
  const cacheId   = window._tgt6CacheId;
  const bulkFile  = document.getElementById('tgt6-bulk-file')?.files[0];
  const threshold = document.getElementById('tgt6-orders-thresh')?.value || '10';
  const statusEl  = document.getElementById('tgt6-export-status');
  const btn       = document.getElementById('tgt6-export-btn');

  if(!cacheId){ alert('请先执行六轮分析'); return; }
  if(!bulkFile){ alert('请上传 Bulk 文件'); return; }

  btn.disabled = true;
  statusEl.textContent = '⏳ 生成中…';

  const fd = new FormData();
  fd.append('bulk_file',        bulkFile);
  fd.append('cache_id',         cacheId);
  fd.append('orders_threshold', threshold);

  try{
    // 复用现有 export-bulk 端点（cache_id 来自六轮分析）
    const resp = await fetch('api/analysis/mode1/export-bulk', {method:'POST', body:fd});
    if(!resp.ok){ const e=await resp.json(); throw new Error(e.detail||'导出失败'); }
    const d = await resp.json();

    // 下载 Bulk 文件
    if(d.bulk_b64){
      const a = document.createElement('a');
      a.href     = 'data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,' + d.bulk_b64;
      a.download = d.bulk_filename || 'bulk_updated.xlsx';
      a.click();
    }
    // 下载 Label CSV
    if(d.label_b64){
      const a = document.createElement('a');
      a.href     = 'data:text/csv;charset=utf-8;base64,' + d.label_b64;
      a.download = d.label_filename || 'targeting_labels.csv';
      a.click();
    }

    statusEl.style.color = 'var(--gn)';
    statusEl.textContent = `✅ 已下载 Bulk 文件（${d.log||''}）`;
  }catch(e){
    statusEl.style.color = 'var(--rd)';
    statusEl.textContent = '❌ ' + e.message;
  }finally{
    btn.disabled = false;
  }
}
```

- [ ] **Step 2：在 `switchAnaView` 函数中注册 `targeting6`**

找到 `switchAnaView` 函数（控制 ana-content 下各 view 的显示/隐藏），确认该函数通过 `['funnel','recs','targeting','history']` 之类的数组或逐个 id 切换。

若函数使用 id 列表数组，在数组中加入 `'targeting6'`：

```javascript
// 找到类似：
const views = ['funnel','recs','targeting','history'];
// 改为：
const views = ['funnel','recs','targeting','targeting6','history'];
```

若函数是逐个 `document.getElementById('ana-xxx').style.display` 形式，则仿照其他 view 补充：
```javascript
document.getElementById('ana-targeting6').style.display = (v==='targeting6') ? '' : 'none';
document.getElementById('anabtn-targeting6').classList.toggle('active', v==='targeting6');
```

- [ ] **Step 3：在页面初始化处（`DOMContentLoaded` 或等效位置）调用产品列表同步**

找到现有三轮版初始化产品下拉的逻辑（通常是 `fetch` 获取产品列表后 `populateTgtProducts()` 之类），在同一位置追加：

```javascript
initTgt6Products();
```

若三轮版产品下拉是在 `switchAnaView('targeting')` 被调用时才填充，则在 `switchAnaView` 函数中当 `v==='targeting6'` 时也调用 `initTgt6Products()`。

---

### Task 3d：验证前端

- [ ] **Step 1：启动服务，打开浏览器验证**

```bash
cd C:\Users\admin\Desktop\python\ads_dashboard
python -m uvicorn ads_funnel.api.main:app --port 5001 --reload
```

打开 `http://localhost:5001/ads/`，进入任意报告的「分析与建议」。

验证清单：
- [ ] 顶部 Tab 栏出现「批量竞价优化-6轮」按钮
- [ ] 点击后切换到六轮 tab，原「批量竞价优化」tab 不受影响
- [ ] 绿色规则说明卡片正确显示（六轮定义 / 门槛 / 排除规则 / 降幅策略）
- [ ] 模块一产品下拉有数据，参数可输入
- [ ] 点击「开始六轮分析」能正常请求并渲染 7 个子标签（Consensus 默认选中）
- [ ] 模块三上传 Bulk 文件并导出，文件正常下载

- [ ] **Step 2：commit**

```bash
git add ads_funnel/template.html
git commit -m "feat: add 批量竞价优化-6轮 tab with 6-round UI, rule description, and JS logic"
```

---

## 自检（Spec 覆盖确认）

| Spec 要求 | 对应 Task |
|-----------|----------|
| R1~R6 每轮递增一周 | Task 1 Step 2（`r_from` 字典） |
| 数据不足时退化不报错 | Task 1 Step 2（`max(-n, -k)` 处理） |
| ≥3/6 门槛 | Task 1 Step 2（`len(hit_set) < 3` 判断） |
| 排除向好：=3 仅R4~R6 | Task 1 Step 1（`_is_improving_6r`） |
| 排除向好：=4 仅R3~R6 | Task 1 Step 1（`_is_improving_6r`） |
| ≥5/6 无条件进入 | Task 1 Step 1（函数不返回 True） |
| adj_pct 取最保守 | Task 1 Step 2（`min(..., key=abs)`） |
| metrics 来自 R6，label 来自 R1 | Task 1 Step 2（`r6_row_map` / `r1_row_map`） |
| 独立 API 路由 | Task 2 Step 2 |
| 原三轮路由不动 | 未触碰 `run_multi_round_analysis` 和 `/mode1/run` |
| Consensus 标签第一位 | Task 3b（tab 顺序） |
| 页面展示规则说明 | Task 3b（绿色规则卡片） |
| 原三轮 Tab 完全保留 | 仅追加，未修改现有 HTML/JS |
