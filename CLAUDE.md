# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

广告漏斗分析系统 v2.0：FastAPI 后端 + MySQL 数据库 + 单页 Web UI，用于亚马逊广告数据的漏斗分析和竞价优化。

## 启动方式

```bash
# 从项目根目录启动（推荐）
python ads_funnel/start.py
# 自动检查并安装依赖，启动服务，自动打开浏览器 → http://localhost:5001

# 手动启动（从 ads_funnel/ 目录执行）
uvicorn api.main:app --host 0.0.0.0 --port 5001 --reload
```

## 数据库配置

`ads_funnel/db_config.json`（参考 `db_config.example.json`）：
```json
{ "host": "127.0.0.1", "port": 3306, "user": "...", "password": "...", "database": "ads_funnel" }
```

db.py 使用 SQLAlchemy + PyMySQL，通过 `get_engine()` 单例获取连接。

## 核心架构

```
ads_funnel/
├── start.py          # 一键启动
├── template.html     # 报告渲染模板（完整 JS+CSS，每次请求从磁盘读取并注入数据）
├── api/
│   ├── main.py       # FastAPI 路由，服务端缓存 _mode1_cache / _mode1_6r_cache
│   ├── db.py         # MySQL 操作层（SQLAlchemy）
│   ├── gen_data.py   # Excel DataFrame → ads_compact dict
│   ├── export.py     # ads_compact → HTML / CSV
│   ├── targeting_analysis.py  # 投放结构分析 + 多轮竞价建议
│   └── bulk_update.py         # 将分析结果写回 Amazon Bulk xlsx
└── frontend/
    └── index.html    # 无数据时的空状态页（上传入口）
```

`legacy/` 目录为 v1.0 冻结存档，请勿修改。

## 数据处理流程

### 漏斗报告（主功能）

```
POST /api/import (领星导出: 广告活动每日明细 + 广告组合每日明细)
  → db.upsert_raw()           写入 raw_camp_lx / raw_port_lx（按唯一主键 upsert）
  → db.rebuild_from_raw()     按国家分组，每国家调用 gen_data.process() 重建 JSON
  → reports 表 UPDATE/INSERT  每国家对应一条 report 记录（country 列去重）
```

`GET /` 直接服务端渲染：读取 report 数据 → `export.to_html_webapp()` → 注入 `template.html`。

### Mode 1 竞价优化

```
POST /api/analysis/mode1/import-data   每周上传：推广商品报告 + 投放报告 → raw_tar / raw_ap
POST /api/analysis/mode1              读取最近 6 个日历周，跑 R1/R2/R3 三轮分析
POST /api/analysis/mode1/bid-optimize-6r  跑 R1~R6 六轮分析
POST /api/analysis/mode1/export-bulk  将分析结果 + 竞价建议写回 Amazon Bulk xlsx
POST /api/analysis/mode1/confirm-update  记录已上传到亚马逊，入 bid_update_log
```

分析结果临时缓存在 `_mode1_cache` / `_mode1_6r_cache`（内存 dict，进程重启后失效）。

## 数据结构（ads_compact dict）

```
wk          ["W15","W16",...]
wk_dates    {"W15":"4/6-4/12",...}
ov          {t:{sp,sl,cl,im,or_,ac,ro,ct,cv,cp}, w:[...周列表...]}
prods       {"SL":{t,w},...}
cats        {"1_SB":{t,w},...}
prod_cats   {"SL":{"1_SB":{t,w},...},...}
ports       [{n,p,c,t,wa},...]          wa=每周ACoS列表
camps       [{n,po,p,c,ty,st,...,wa},...] 按花费降序
daily_*     按日期键的每日汇总
```

指标字段：`sp`=花费, `sl`=销售额, `cl`=点击, `im`=曝光, `or_`=订单, `ac`=ACoS%, `ro`=ROAS, `ct`=CTR%, `cv`=CVR%, `cp`=CPC

## 商品/类别分类（gen_data.py:classify_port）

产品列表在 `gen_data.py` 顶部的 `PRODS` 配置。分类规则基于广告组合名称关键词匹配：
- `_SB` / `SB`开头 → `1_SB`；`_SD` / `SD`开头 → `2_SD`
- `KW精准`/`KW防守` → `3_SP_KW精准`；`KW拓展` → `4_SP_KW拓展`
- `ASIN精准`/`ASIN进攻`/`ASIN防守` → `5_SP_ASIN精准`；`ASIN拓展` → `6_SP_ASIN拓展`

## 模板修改规则

`template.html` 包含完整 JS+CSS，每次 HTTP 请求由 `export.py:_build_html()` 读取并用正则注入以下变量：
- `const RAW = __RAW_JSON__;` → 实际数据
- `const WEEKS` / `const WK_DATES` → 周次信息
- `let acosTargets = {...}` → ACoS 目标值

修改模板后，`--reload` 模式下刷新页面即可看到效果（无需重启）。

## 协作规则

1. **后端改动**（`api/` 目录）：必须先与用户明确需求、确认方案，再执行代码修改。
2. **前端改动**（`template.html` / `frontend/`）：改动较大时建议先出方案。
3. **禁止自动提交**：仅在收到明确指令时才执行 commit + push。
