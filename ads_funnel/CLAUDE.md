# ads_funnel — v2.0 开发文档

广告漏斗分析 v2.0：FastAPI 后端 + SQLite 数据库 + Web UI。

> v1.0 旧版脚本已归档至 `../legacy/`，本目录为当前活跃版本。

## 目录结构

```
ads_funnel/
├── start.py           # 一键启动入口
├── requirements.txt   # 依赖
├── ads_funnel.db      # SQLite 数据库
├── template.html      # 报告 HTML 模板（含所有 JS/CSS）
├── CLAUDE.md          # 本文件
├── api/
│   ├── main.py        # FastAPI 路由
│   ├── db.py          # SQLite 操作层
│   ├── gen_data.py    # Excel DataFrame → 结构化 dict
│   ├── export.py      # HTML / CSV 导出
│   └── __init__.py
└── frontend/
    └── index.html     # Web UI（报告列表 + 导入）
```

## 启动

```bash
python start.py
# 自动安装依赖，启动服务，打开浏览器 → http://localhost:8000
```

或手动启动：
```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

## API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web UI 首页 |
| GET | `/dashboard?id=<n>` | 报告详情页（服务端渲染） |
| GET | `/api/reports` | 报告列表 |
| POST | `/api/import` | 上传 Excel，解析入库 |
| DELETE | `/api/reports/{id}` | 删除报告 |
| GET | `/api/reports/{id}/export/html` | 导出离线 HTML |
| GET | `/api/reports/{id}/export/json` | 导出 JSON 数据包 |
| GET | `/api/reports/{id}/export/csv` | 导出 CSV |
| GET/POST | `/api/config/acos-targets` | ACoS 目标配置 |

## 数据库结构

```sql
reports (id, title, weeks, wk_dates, data, camp_file, port_file, created_at)
config  (key, value, updated_at)
```

- `data` 列存储完整的 `ads_compact` JSON（序列化为 TEXT）
- `config` 中的 `acos_targets` key 存储各产品 ACoS 目标值

## 数据处理流程

```
POST /api/import
  ↓ 读取上传的两个 Excel（camp_file + port_file）
  ↓ api/gen_data.py: process(df_c, df_p) → dict
  ↓ api/db.py: save_report(...)
  ↓ SQLite ads_funnel.db
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
| `KW拓展` | 4_SP_KW拓展 |
| `ASIN精准` / `ASIN进攻` / `ASIN防守` | 5_SP_ASIN精准 |
| `ASIN拓展` | 6_SP_ASIN拓展 |

## 修改模板

`template.html` 包含完整的 JS + CSS + HTML 结构，直接编辑即可。  
修改后重启服务（`--reload` 模式下自动生效），刷新报告页面即可看到变化。

## 协作规则

1. **后端改动**：涉及 `api/` 目录下任何 Python 文件的修改，必须先与用户明确需求、确认方案，再执行代码修改。
2. **前端改动**：涉及 `template.html` / `frontend/` 的修改，可根据改动量自行判断是否需要提前确认，改动较大时建议先出方案。
3. **禁止自动提交**：代码修改完成后，不得自动 push 到 GitHub。只有收到明确指令（如"提交代码到 github"或类似描述）时，才执行 commit + push。
