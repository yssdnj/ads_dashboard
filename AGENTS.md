# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

> **Maintenance:** Ruthlessly edit this file over time — add new patterns as you discover them, remove stale or redundant content, keep it concise and accurate.
> **Complex tasks:** Start every complex task in plan mode before writing any code.
> **Agent review agent:** Every implementer subagent's output gets reviewed by two fresh subagents — spec compliance first, then code quality. Never skip either review.

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
├── template.html     # 报告渲染模板（含 Market Monitor 资源内联标记）
├── api/
│   ├── main.py       # FastAPI 路由
│   ├── db.py         # MySQL 操作层（SQLAlchemy）
│   ├── gen_data.py   # Excel DataFrame → ads_compact dict
│   ├── export.py     # ads_compact → HTML / CSV
│   ├── market_monitoring/    # 市场监控：schema/repository/collector/analytics/service/router
│   ├── targeting_analysis.py  # 投放结构分析 + 多轮竞价建议
│   └── bulk_update.py         # 将分析结果写回 Amazon Bulk xlsx
├── frontend/
│   ├── index.html    # 无数据时的空状态页（上传入口）
│   └── static/market-monitor.{css,js}  # Market Insights 独立资源，导出时内联
└── tests/            # 默认本地测试；Market Monitor 回归由 .gitignore 精确例外纳入版本控制
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

其他漏斗报告端点：`GET /api/reports`、`GET /api/reports/{id}`、`DELETE /api/reports/{id}`；导出：`/export/html`、`/export/json`、`/export/csv`；配置：`GET|POST /api/config/acos-targets`、`GET|POST /api/config/avg-clicks`。

### Mode 1 竞价优化

```
POST /api/analysis/mode1/import-data      每周上传：推广商品报告 + 投放报告 → raw_tar / raw_ap
POST /api/analysis/mode1                  读取最近 6 个日历周，跑 R1/R2/R3 三轮分析
POST /api/analysis/mode1/bid-optimize-6r  跑 R1~R6 六轮分析；返回 consensus（降价）+ consensus_up（提价）
POST /api/analysis/mode1/export-bulk      将分析结果 + 竞价建议写回 Amazon Bulk xlsx
POST /api/analysis/mode1/confirm-update   记录已上传到亚马逊，入 bid_update_log
GET  /api/analysis/mode1/data-stats       查看已导入数据的周次覆盖情况
GET  /api/analysis/mode1/update-logs      竞价更新历史记录列表
GET  /api/analysis/mode1/update-logs/{id}/details  某次更新的逐条明细（含 old_bid / new_bid / adj_pct）
```

**bid-optimize-6r 返回两组 consensus：**
- `consensus`（降价）：标签含「高ACoS出单」或「高点击不出单」，≥ 3/6 轮命中，指标取 R6；排除仅命中 R4~R6 的向好趋势
- `consensus_up`（提价）：标签为「低ACoS出单」，≥ 3/6 轮命中且 R3 必须命中，订单 ≥ `up_orders_threshold`（默认 2），指标取 R3

**分析结果不在服务器缓存**：前端将 `rows` 回传给 `export-bulk` 端点（`rows_json` 字段），服务端不持久化中间结果。

### L3 广告活动 Tab — 周次筛选 & 趋势面板

```
GET /api/analysis/mode1/campaign-weekly-stats  按日期范围从 raw_camp_lx 聚合各活动指标
GET /api/analysis/mode1/campaign-trend         单活动的周趋势 + 日趋势 + 调价事件列表
```

- **周次筛选**：选择 W1-W21 范围后，实时查 `raw_camp_lx`（领星广告活动每日明细表）得到各活动在该范围内的聚合指标，覆盖 RAW.camps 中的静态汇总数据
- **trend API** 返回 `{weekly, daily, bid_events}`，其中 `bid_events[].records` 包含每条调价的 targeting / old_bid / new_bid / adj_pct
- **日趋势标注**：调价日期在 x 轴显示橙色虚线（`chartjs-plugin-annotation@3`），悬停时通过 `chart.onHover` 展开每条原价→新价幅度详情
- **注意**：`raw_camp_lx` 的国家列值为 `'UK'`/`'US'`/`'DE'`（非 `'United Kingdom'` 等全称）；`reports` 表的 `title` 首词即国家代码

### Market Insights 市场监控

- 业务库为 `ads_funnel_market`；FastAPI 查询路由不调用 MCP/供应商接口，但 lifespan 会建库/建表、执行增量迁移并 upsert 默认市场和 seed ASIN，因此启动账号需要 DDL/DML 权限。
- 实现位于 `api/market_monitoring/`；三个旧 `market_monitor*.py` 仅为兼容 facade。
- 关键词唯一来源为 `docs/US_Dog Slip Leads_关键词洞察列表_2026-06_高中低关键词.xlsx`：`high=77`、`mid=40`、`low=139`；只能用导入脚本显式同步，服务启动不会 seed 关键词；旧 `strong` 不参与活动配置、接口和聚合。
- `/api/market-monitor/asins` 返回按父 ASIN 分组的代表子体。每日 ASIN 快照可持久化 nullable `image_url`；缺图由前端使用本地占位图。
- 采集与关键词导入操作见 `docs/market_monitor_daily_collection.md`。

## 数据结构（ads_compact dict）

```
wk            ["W15","W16",...]
wk_dates      {"W15":"4/6-4/12",...}
wk_iso_dates  {"W15":["2026-04-06","2026-04-12"],...}  ← L3 周次筛选使用
ov            {t:{sp,sl,cl,im,or_,ac,ro,ct,cv,cp}, w:[...周列表...]}
prods         {"SL":{t,w},...}
cats          {"1_SB":{t,w},...}
prod_cats     {"SL":{"1_SB":{t,w},...},...}
ports         [{n,p,c,t,wa},...]          wa=每周ACoS列表
camps         [{n,po,p,c,ty,st,...,wa},...] 按花费降序
daily_*       按日期键的每日汇总
```

指标字段：`sp`=花费, `sl`=销售额, `cl`=点击, `im`=曝光, `or_`=订单, `ac`=ACoS%, `ro`=ROAS, `ct`=CTR%, `cv`=CVR%, `cp`=CPC

## 商品/类别分类（gen_data.py:classify_port）

产品列表在 `gen_data.py` 顶部的 `PRODS` 配置。分类规则基于广告组合名称关键词匹配：
- `_SB` / `SB`开头 → `1_SB`；`_SD` / `SD`开头 → `2_SD`
- `KW精准`/`KW防守` → `3_SP_KW精准`；**`KW拓展`/`KW扩展`** → `4_SP_KW拓展`
- `ASIN精准`/`ASIN进攻`/`ASIN防守` → `5_SP_ASIN精准`；`ASIN拓展` → `6_SP_ASIN拓展`

匹配前 ASCII 字母转大写（解决 Asin/ASIN 大小写混用），中文字符保持原样。

## 模板修改规则

`template.html` 每次 HTTP 请求由 `export.py:_build_html()` 读取并用正则注入以下变量：
- `const RAW = __RAW_JSON__;` → 实际数据
- `const WEEKS` / `const WK_DATES` / `const WK_ISO_DATES` → 周次信息（WK_ISO_DATES 供 L3 日期范围计算）
- `let acosTargets = {...}` → ACoS 目标值
- `const REPORT_COUNTRY = '__REPORT_COUNTRY__'` → 当前国家代码（如 `'UK'`）

Market Monitor 的 CSS/JS 分别维护在 `frontend/static/market-monitor.css` 与
`frontend/static/market-monitor.js`。`template.html` 保留
`/* __MARKET_MONITOR_CSS__ */` / `/* __MARKET_MONITOR_JS__ */` 标记，
`export.py:_build_html()` 在服务端内联资源，确保导出的 HTML 可离线使用。不要把该模块
重新塞回内联源码，也不要改成运行时 `/static/market-monitor.*` 依赖。

修改模板后，`--reload` 模式下刷新页面即可看到效果（无需重启）。

## 服务启停（Windows）

```powershell
# 停止（杀掉所有 python 进程）
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force

# 启动（从项目根目录执行）
Set-Location "C:\Users\admin\Desktop\python\ads_dashboard"
Start-Process python -ArgumentList "-m uvicorn ads_funnel.api.main:app --host 0.0.0.0 --port 5001" -RedirectStandardError "nohup_err.out" -NoNewWindow
```

> **注意**：不要用 bash 的 `kill` 命令停进程，Windows PID 与 bash PID 不对应，必须用 PowerShell `Stop-Process`。

## 协作规则

1. **后端改动**（`api/` 目录）：必须先与用户明确需求、确认方案，再执行代码修改。
2. **前端改动**（`template.html` / `frontend/`）：改动较大时建议先出方案。
3. **禁止自动提交**：仅在收到明确指令时才执行 commit + push。

## 标准开发流程

严格遵循 superpowers skills 的定义顺序，不跳步、不简化：

**Step 1 — 需求澄清：`superpowers:brainstorming`**
探索代码现状 → 逐个提问（一次只问一个）→ 提出 2-3 个方案 → 展示设计稿逐段获得确认
→ 写 spec 到 `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` → 请用户 review spec
→ 用户确认后调用 `superpowers:writing-plans`

**Step 2 — 实施计划：`superpowers:writing-plans`**
每个 Task 必须包含：精确文件路径、完整代码、可运行的测试命令和预期输出，零占位符
→ 保存到 `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
→ 询问执行方式：Subagent-Driven（推荐）或 Inline

**Step 3 — 执行计划：`superpowers:subagent-driven-development`（推荐）**
每个 Task 依次：派发 implementer subagent → Spec 合规审查 → Code Quality 审查 → 标记完成
全部 Task 完成后跑 final review

**Step 4 — 验证：`superpowers:verification-before-completion`**
必须实际运行程序到达改动代码路径，拿到真实输出，才能声明完成
禁止用"跑测试"或"看起来对"替代运行时验证

**Step 5 — 提交（用户明确指令后执行）**
- "**提交到本地仓库**" → `git add + commit`，不 push
- "**提交到 GitHub**" → `git add + commit + git push origin HEAD`
