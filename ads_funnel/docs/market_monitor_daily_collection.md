# Slip Lead Leash 市场监控：关键词目录、每日采集与运行手册

## 1. 边界与数据源

市场 ID 为 `slip_lead_leash`，业务库为 `ads_funnel_market`。FastAPI 的查询路由不
直接调用 MCP 或供应商接口；Codex automation 负责调用数据源并将标准化 payload
交给 Python 入库。服务启动并非只读：lifespan 会创建数据库/表、执行幂等增量迁移，
并 upsert 默认市场与 seed ASIN 配置，因此运行账号需要相应 DDL/DML 权限。启动过程
不会写入关键词目录；June 2026 Excel 关键词只能通过下述显式导入命令同步。

来源优先级：

1. `xydc`：每日主链路；
2. `sif`、`sorftime`：缺口复核、异常诊断或周度辅助，不阻塞主链路入库。

当前关键词唯一来源为：

```text
ads_funnel/docs/US_Dog Slip Leads_关键词洞察列表_2026-06_高中低关键词.xlsx
```

- 工作表：`关键词列表`
- 来源：`xydc`
- 版本月：`2026-06`
- 有效目录：256 个关键词，即 `high=77`、`mid=40`、`low=139`
- `类目相关度` 原值写入 `relevance_weight`，不会用固定档位权重替代
- 每日表现不会自动改写关键词所属档位

### 校验与应用 Excel 目录

先做只读校验：

```powershell
python ads_funnel/scripts/market_monitor_import_keywords.py --dry-run
```

预期输出包含：

```json
{"active": 256, "high": 77, "mid": 40, "low": 139}
```

校验通过后再事务性应用：

```powershell
python ads_funnel/scripts/market_monitor_import_keywords.py
```

可用 `--workbook`、`--market-id`、`--source-version` 覆盖默认值。导入会 upsert
工作簿中的 256 行，并把不在新版工作簿中的旧配置标为 `inactive`；不会物理删除旧
配置，也不会改写历史快照。重复的规范化关键词、缺列、非法相关性或错误的 77/40/139
分布会让整次导入失败，不会部分替换有效目录。

## 2. 每日与周度采集计划

暂定每日北京时间 17:00 执行；如果供应商数据更晚更新，应同步调整 automation。

生成当日调用计划：

```powershell
python ads_funnel/scripts/market_monitor_collect_daily.py plan
```

常用覆盖项：

```powershell
python ads_funnel/scripts/market_monitor_collect_daily.py plan `
  --date 2026-07-21 `
  --target-data-date 2026-07-20 `
  --market-id slip_lead_leash
```

计划以数据库内 `active` 的 seed ASIN 和关键词为准，不应在操作手册中假定固定 ASIN
数量。默认核心 ASIN 是：

```text
B0D6G27DNH
B09T3KS313
B091TMYKHF
B095775SWX
B0BMQQ74H2
```

### 三档采集规则

- `get_keyword_info`：采集全部 256 个 `high`/`mid`/`low` 有效关键词。
- `get_asin_traffic`、`get_asin_info`：采集全部有效 seed ASIN；近七日流量与商品基础
  信息分别标准化到市场/ASIN 快照。
- `get_asin_keywords`：对默认或 `--core-asin` 指定的核心 ASIN 采集关键词流量来源。
- `get_keyword_asin_analysis`：只对 77 个 `high` 关键词采集 Top 竞争 ASIN；`mid`、
  `low` 不展开这项高成本调用。
- `sorftime` 的关键词排名趋势辅助调用也只取 `high` 关键词（当前计划取前五个）；
  `sif` 仅在流量或广告拆分需要复核时使用。

标准化的关键词、ASIN×关键词和关键词×ASIN快照只接受 `high`、`mid`、`low`。
数据库中的旧 `strong_*` 列仅为非破坏性兼容字段，写入值固定为 0；API、活动聚合和
页面均不使用 `strong`。

父子变体变化较慢，默认不每日全量采集。周度更新使用：

```powershell
python ads_funnel/scripts/market_monitor_collect_daily.py plan --include-weekly
```

这会为有效 seed ASIN 增加 `get_asin_variations`，更新
`listing_variation_map`。监控对象不是所有子体的平铺列表：每个父体显示自有子体和
显式 `is_representative_child=1` 的代表子体；若该父体没有显式代表，则比较各子体在
有效完整日及之前各自最新可用快照的流量，并回退到其中流量最高的一个子体。缺数时
这些候选快照可能来自不同日期，并非强制同一完整日。

### 商品图片

`get_asin_info`/其他来源如返回商品图片，应把合法原始 URL 标准化为
`asin_snapshots[].image_url`。`asin_snapshot_daily.image_url` 可为空，并随每日 ASIN
快照持久化；`/api/market-monitor/asins`、ASIN 趋势/快照接口会原样返回该字段。系统不
猜测第三方 CDN 地址；缺失或前端拒绝的 URL 使用本地
`/static/placeholder-product.svg`。

## 3. 入库接口与 payload 合同

将 automation 生成的标准化 JSON 写入 MySQL：

```powershell
python ads_funnel/scripts/market_monitor_collect_daily.py ingest `
  --payload .\normalized_payload.json
```

JSON 可以是一批，或使用 `{"batches": [...]}` 一次传多批。每批参数对应：

```python
ads_funnel.api.market_monitor_collector.persist_daily_source_batch(...)
```

关键字段：

- 运行：`market_id`、`run_date`、`collect_time_bj`、`target_data_date`、
  `data_available_through`、`timezone_basis`
- 来源：`source_channel`（`mcp`/`api`/`manual`/`csv`）、`source_provider`、
  `source_tool`、`request_params`、`response_json`、`response_status`
- 标准化区块：`listing_variations`、`market_snapshot`、`asin_snapshots`、
  `keyword_snapshots`、`asin_keyword_snapshots`、`keyword_asin_competition`
- 可选报告：`report`

默认 `write_mode=upsert`，适合多个接口分批补齐同一天数据。只有调用方确认某个区块是
完整日全集时才使用 `replace_day`，否则会删除该表同市场、同日期的已有行后再写入。
原始请求、原始响应、标准化快照和 run log 会保留来源链路。供应商响应非 `success`
但可继续入库时，run 状态记为 `partial_success`；异常则记为 `failed` 并重新抛出。

## 4. 包职责与兼容入口

```text
ads_funnel/api/market_monitoring/
├── constants.py        市场、三档、工作簿元数据
├── schema.py           建库、建表、幂等增量迁移
├── repository.py       SQL 查询与关键词目录事务
├── keyword_catalog.py  Excel 解析、规范化、严格校验
├── collector.py        原始响应与标准化日快照持久化
├── analytics.py        基线、异常、置信度与行动优先级
├── service.py          Dashboard/Comparison DTO 组装
└── router.py           FastAPI 参数校验与路由
```

旧模块 `api/market_monitor_db.py`、`api/market_monitor_collector.py` 和
`api/market_monitor.py` 是兼容 facade。新业务逻辑应放入上面的职责模块，不应写回
facade、`main.py` 或前端。

## 5. Dashboard 与查询接口

服务启动后，Market Insights 使用以下只读接口：

- `GET /api/market-monitor/health`
- `GET /api/market-monitor/markets`
- `GET /api/market-monitor/dates`
- `GET /api/market-monitor/date-context`
- `GET /api/market-monitor/dashboard?market_id=...&date_from=...&date_to=...`
- `GET /api/market-monitor/asins?market_id=...`：父体分组的代表子体，含
  `image_url` 和最新完整日流量
- `GET /api/market-monitor/comparison?...`：一个自有 ASIN 加最多三个
  `competitor_asin`，场景为 `market_share`、`keyword_competition`、
  `ad_competition` 或 `growth_quality`
- `GET /api/market-monitor/keywords?relevance_level=high|mid|low`
- `GET /api/market-monitor/keyword-summary`
- `GET /api/market-monitor/market-trend`、`asin-trend`、`asin-snapshots`、
  `keyword-trend`、`asin-keywords`、`keyword-competition`
- `GET /api/market-monitor/reports`、`runs`

`date_from` 晚于 `date_to`、第四个竞争 ASIN、非法场景或
`relevance_level=strong` 均返回 HTTP 422。默认页面先读 `date-context`，再用同一日期
范围加载 Dashboard、Comparison 和关键词数据。

Market Monitor 的 CSS/JS 位于 `frontend/static/market-monitor.*`，但
`export._build_html()` 会将其内联到页面。因此 `/api/reports/{report_id}/export/html`
生成的 HTML 可脱离 FastAPI 单独打开，不应请求 `/static/market-monitor.*`。

## 6. 日常操作与排障

### 验证目录和服务

```powershell
python -c "from collections import Counter; from ads_funnel.api.market_monitoring import repository as r; rows=r.list_keywords(); print(len(rows), Counter(x['relevance_level'] for x in rows))"
curl.exe -sS "http://127.0.0.1:5001/api/market-monitor/keyword-summary"
curl.exe -sS "http://127.0.0.1:5001/api/market-monitor/runs?limit=10"
```

期望有效目录为 `256`，分布为 `low=139, high=77, mid=40`。

### Excel 无效

先运行 `--dry-run`，根据错误修复缺失工作表/列、空关键词、规范化后重复、非法
`相关性`/`类目相关度` 或三档数量。不要绕过校验，也不要手工部分更新线上目录。失败的
事务不会停用当前有效目录。

### 页面显示陈旧数据

检查 `date-context` 的 `latest_complete_date` 与 `latest_snapshot_date`，再查看 `runs`
中的 `target_data_date`、`data_available_through`、状态、采集时间和时区。缺少当日完整
run 时，页面会保留最后完整数据并显示 stale；不要把采集缺口解释为市场下滑。

### 部分供应商失败

查看 `runs` 与 Dashboard `data_quality` 的 provider coverage、missing data types 和
error 信息。`partial_success` 数据允许展示，但完整度和置信度会下降；先补采缺失区块，
用默认 `upsert` 合并，不要误用 `replace_day` 擦除已有数据。

### 图片缺失或监控对象不符合预期

先检查最新 `asin_snapshot_daily.image_url` 是否为空/非 HTTP(S)，以及周度
`listing_variation_map` 的父子关系和代表标记是否已更新。图片缺失显示本地占位图是
预期降级；父体没有显式代表时按“完整日及之前各子体最新可用快照”的流量回退也是
预期行为。
