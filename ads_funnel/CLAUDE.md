# ads_funnel — v2.0 开发文档

广告漏斗分析 v2.0：FastAPI 后端 + MySQL 数据库 + Web UI。

> v1.0 旧版脚本已归档至 `../legacy/`，本目录为当前活跃版本。

## 目录结构

```
ads_funnel/
├── start.py           # 一键启动入口
├── requirements.txt   # 依赖
├── db_config.json     # MySQL 连接配置（参考 db_config.example.json）
├── template.html      # 报告 HTML 模板（含所有 JS/CSS）
├── CLAUDE.md          # 本文件
├── api/
│   ├── main.py               # FastAPI 路由
│   ├── db.py                 # MySQL 操作层（SQLAlchemy + PyMySQL）
│   ├── gen_data.py           # Excel DataFrame → 结构化 dict
│   ├── export.py             # HTML / CSV 导出
│   ├── targeting_analysis.py # Mode 1 竞价分析逻辑
│   ├── bulk_update.py        # Bulk 文件竞价写回
│   └── __init__.py
└── frontend/
    └── index.html     # Web UI（报告列表 + 导入）
```

## 启动

```bash
python start.py
# 自动安装依赖，启动服务，打开浏览器 → http://127.0.0.1:5001
```

或手动启动（需在 ads_funnel/ 目录下执行）：
```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --host 0.0.0.0 --port 5001
```

## API 路由

**报告管理**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web UI 首页 |
| GET | `/api/reports` | 报告列表 |
| GET | `/api/reports/{id}` | 报告详情 |
| POST | `/api/import` | 上传 Excel，解析入库 |
| DELETE | `/api/reports/{id}` | 删除报告 |
| GET | `/api/reports/{id}/export/html` | 导出离线 HTML |
| GET | `/api/reports/{id}/export/json` | 导出 JSON 数据包 |
| GET | `/api/reports/{id}/export/csv` | 导出 CSV |

**配置**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/config/acos-targets` | ACoS 目标配置 |
| GET/POST | `/api/config/avg-clicks` | 平均出单点击数配置 |

**Mode 1 竞价分析**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/analysis/mode1/import-data` | 上传投放报告/推广商品报告入库 |
| GET | `/api/analysis/mode1/data-stats` | 查询数据库中已有数据的时间范围 |
| POST | `/api/analysis/mode1` | 3 轮竞价分析 |
| POST | `/api/analysis/mode1/bid-optimize-6r` | 6 轮竞价分析 |
| POST | `/api/analysis/mode1/export-bulk` | 将分析结果写回 Bulk 文件 |
| POST | `/api/analysis/mode1/confirm-update` | 确认更新，写入 bid_update_log |
| GET | `/api/analysis/mode1/update-logs` | 查询竞价更新历史 |
| GET | `/api/analysis/mode1/update-logs/{id}/details` | 查询某次更新明细（含 old_bid / new_bid / adj_pct） |
| GET | `/api/analysis/mode1/campaign-weekly-stats` | 按日期范围从 raw_camp_lx 聚合各活动指标（L3 周次筛选） |
| GET | `/api/analysis/mode1/campaign-trend` | 单活动周趋势 + 日趋势 + 调价事件（L3 趋势面板） |

**工具**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/db/query` | 直接执行 SQL 查询（调试用） |
| GET | `/api/db/schema` | 查询数据库表结构 |

## 数据库结构（MySQL）

```sql
reports           (id, title, country, weeks, wk_dates, data LONGTEXT, camp_file, port_file, created_at)
                  -- data 字段包含完整 ads_compact JSON，含 wk_iso_dates
config            (key, value LONGTEXT, updated_at)
raw_camp_lx       -- 领星广告活动每日明细（国家列值为 'UK'/'US'/'DE'，非全称）
raw_port_lx       -- 领星广告组合每日明细
raw_tar           -- 投放报告每日原始数据（Mode 1 分析用）
raw_ap            -- 推广商品报告每日原始数据（Mode 1 分析用）
bid_update_log    -- 竞价更新记录（country 字段同 raw_camp_lx 格式）
bid_update_detail -- 竞价更新明细行（campaign / ad_group / targeting / old_bid / new_bid / adj_pct）
```

- `data` 列存储完整的 `ads_compact` JSON（LONGTEXT）
- `config` 中的 `acos_targets` key 存储各产品 ACoS 目标值

## 数据处理流程

```
POST /api/import
  ↓ 读取上传的两个 Excel（camp_file + port_file）
  ↓ api/gen_data.py: process(df_c, df_p) → dict
  ↓ api/db.py: save_report(...)
  ↓ MySQL（db_config.json 中配置连接信息）
```

## 报告渲染流程

```
GET /dashboard?id=N
  ↓ db.get_report(id) → data dict
  ↓ export.to_html_webapp(data, title, acos_targets, report_id)
     → template.html 中注入 JS 变量 RAW + 顶部导航栏
  ↓ 返回完整 HTML 页面
```

## 数据结构（ads_compact dict）

```
{
  "wk":          ["W15","W16",...],
  "wk_dates":    {"W15":"4/6-4/12",...},
  "ov":          {t:{sp,sl,cl,im,or,ac,ro,ct,cv,cp}, w:[...]},
  "prods":       {"SL":{t,w}, ...},
  "cats":        {"1_SB":{t,w}, ...},
  "prod_cats":   {"SL":{"1_SB":{t,w},...},...},
  "ports":       [{n,p,c,t,wa},...],
  "camps":       [{n,po,p,c,ty,st,sp,sl,cl,im,or,ac,ro,ct,cv,cp,wa},...],
  "daily_ov":    {"2026-04-06":{sp,sl,cl,im,or},...},
  "daily_prod":  {"SL":{"2026-04-06":{...},...},...},
  "daily_cat":   {"1_SB":{"2026-04-06":{...},...},...},
  "daily_prod_cat": {"SL":{"1_SB":{"date":{...}},...},...},
  "daily_port":  {"SL_SB":{"2026-04-06":{...},...},...}
}
```

指标字段：`sp`=花费, `sl`=销售额, `cl`=点击, `im`=曝光, `or`=订单, `ac`=ACoS%, `ro`=ROAS, `ct`=CTR%, `cv`=CVR%, `cp`=CPC

## 商品 / 类别分类规则（api/gen_data.py 中修改）

```python
PRODS = ['SL','DL','DSL2','Toy','ToyDH','MFL','SFM','ShortL','WB']
```

| 广告组合名称关键词 | 类别 |
|-------------------|------|
| `_SB` / `SB` 开头 | 1_SB |
| `_SD` / `SD` 开头 | 2_SD |
| `KW精准` / `KW防守` | 3_SP_KW精准 |
| `KW拓展` / `KW扩展` | 4_SP_KW拓展 |
| `ASIN精准` / `ASIN进攻` / `ASIN防守` | 5_SP_ASIN精准 |
| `ASIN拓展` | 6_SP_ASIN拓展 |

> 匹配前 ASCII 字母转大写（解决 Asin/ASIN 大小写混用），中文字符保持原样。

## 修改模板

`template.html` 包含完整的 JS + CSS + HTML 结构，直接编辑即可。  
修改后重启服务（`--reload` 模式下自动生效），刷新报告页面即可看到变化。

## Mode 1 竞价分析与导出流程

### 分析端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/analysis/mode1` | 3 轮竞价分析 |
| POST | `/api/analysis/mode1/6r` | 6 轮竞价分析 |
| POST | `/api/analysis/mode1/export-bulk` | 将分析结果写回 Bulk 文件 |
| POST | `/api/analysis/mode1/confirm-update` | 确认更新，写入 bid_update_log |

### 导出数据传递方式（前端 → 后端）

分析结果**不在服务器内存中缓存**。流程如下：

1. 分析端点返回 `consensus.rows`（每条 targeting 的标签 + 调价幅度等）
2. 前端将 rows 存入 JS 变量：
   - 3R：`window._mode1ExportMeta = { rows, product_target, report_start, report_end }`
   - 6R：`window._tgt6ExportMeta = { rows, product_target, report_start, report_end }`
3. 用户点击"导出 Bulk"时，前端将 `rows_json`（JSON 序列化的 rows 数组）连同 Bulk 文件一起 POST 到 `export-bulk` 端点
4. 后端解析 `rows_json`，筛选符合条件的行（高ACoS出单/高点击不出单 且 orders < 阈值），写入 Bulk 文件并返回

### 服务端内存缓存

| 变量 | 位置 | 内容 | 有效期 |
|------|------|------|--------|
| `_catalog_df` | `targeting_analysis.py` | `product_catalog.xlsx` | 1 小时 TTL，超时重新读盘 |

> **注意**：`_mode1_cache` / `_mode1_6r_cache` 已删除。分析结果不在服务器持久化，改为前端将 `rows` 回传给 `export-bulk`（`rows_json` 字段）。

## 前端关键变量（template.html）

| 变量 | 来源 | 说明 |
|------|------|------|
| `RAW` | reports.data | 完整 ads_compact dict（含 camps / ports / wk / wk_iso_dates 等） |
| `WEEKS` | reports.data.wk | 周次数组，如 `["W1","W2",...]` |
| `WK_DATES` | reports.data.wk_dates | 周次 → 显示字符串，如 `{"W1":"12/29-1/4"}` |
| `WK_ISO_DATES` | reports.data.wk_iso_dates | 周次 → ISO 日期范围，如 `{"W1":["2025-12-29","2026-01-04"]}` |
| `REPORT_COUNTRY` | reports.title 首词 | 国家代码，如 `'UK'`、`'US'`、`'DE'` |
| `REPORT_ID` | reports.id | 当前报告 ID（Web App 模式） |
| `_charts` | 前端全局 | Chart.js 实例 dict，key = canvas id，用 `destroyChart(id)` 销毁 |
| `l3TrendCache` | 前端全局 | L3 趋势数据缓存，key = 活动名，切换周次时清除 |

## 协作规则

1. **后端改动**：涉及 `api/` 目录下任何 Python 文件的修改，必须先与用户明确需求、确认方案，再执行代码修改。
2. **前端改动**：涉及 `template.html` / `frontend/` 的修改，可根据改动量自行判断是否需要提前确认，改动较大时建议先出方案。
3. **禁止自动提交**：代码修改完成后，不得自动 push 到 GitHub。只有收到明确指令（如"提交代码到 github"或类似描述）时，才执行 commit + push。
4. **测试文件**：统一放在 `tests/` 目录（已加入 `.gitignore`），不入库。
5. **服务重启（Windows）**：用 PowerShell `Stop-Process -Name python -Force` 杀进程，bash 的 `kill` 命令 PID 与 Windows 不一致，不可靠。
