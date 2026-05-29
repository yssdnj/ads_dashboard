---
name: amazon-targeting-structure-report
description: >-
  Builds Amazon Sponsored Products targeting structure analysis from Advertised
  Product Report and Targeting Report (Excel). Produces Markdown report, UTF-8
  CSV with per-targeting labels and bid suggestions, and a styled text summary.
  Use when the user provides Sponsored Products Advertised product report.xlsx,
  Sponsored Products Targeting report.xlsx, ASIN_LIST, TARGET_ACOS, or
  AVG_CLICKS_PER_ORDER, or asks for targeting structure / 投放结构 / 核心流量
  protection / 分层标签 reports.
---

# Amazon Targeting 结构分析报告

## 何时使用

用户给出（或拖拽）以下输入时执行本流程：

1. **Advertised Product Report**：Sponsored Products 广告商品报告（`.xlsx`）
2. **Targeting Report**：Sponsored Products 投放报告（`.xlsx`）
3. **ASIN_LIST**：待分析 ASIN，可为单个或多个
4. **TARGET_ACOS**：目标 ACoS，小数形式（如 15% → `0.15`）
5. **AVG_CLICKS_PER_ORDER**：平均出单点击数（总点击 ÷ 总订单）

## 脚本版本说明

| 版本 | 文件 | 说明 |
|------|------|------|
| **v2（默认）** | `run_targeting_structure_report_v2.py` | `--advertised-product` 同时支持摘要格式（Start Date/End Date，英文列名）和每日格式（日期列，中文列名），自动识别转换，其余逻辑不变 |
| v1 | `run_targeting_structure_report_v1.py` | 新增 `--product-target` 标识符；输出到带时间戳子目录；文件名用报告日期范围；健康度新增总点击/订单/花费/销售额/CVR |
| v0（原版） | `run_targeting_structure_report_v0.py` | 原始版本，文件名用生成日期，直接输出到 `--out-dir` |

## 必须执行的步骤

1. **确认路径**：两个 xlsx 在用户工作区中的绝对路径或相对路径；若用户只上传文件，先保存到可写目录并记下路径。

2. **默认运行 v2**：

   ```bash
   python "<skill_dir>/run_targeting_structure_report_v2.py" \
     --advertised-product "<AP_REPORT.xlsx>" \
     --targeting "<TARGETING_REPORT.xlsx>" \
     --product-target "<标识符，如 DL_ASIN>" \
     --asin <ASIN1> <ASIN2> ... \
     --target-acos <0.15> \
     --avg-clicks-per-order <7> \
     --out-dir "<输出根目录>"
   ```

   > **注意**：`--asin` 使用 `nargs="+"` 语法，多个 ASIN 在同一个 `--asin` 后空格分隔，无需重复 `--asin`。
   >
   > 输出目录会自动创建子目录 `{YYYYMMDDHHMMSS}_{product_target}/`。
   >
   > 文件命名：`report_{product_target}_{报告起始日}_{报告截止日}.md` 和 `targeting_labels_{product_target}_{起始日}_{截止日}.csv`
   >
   > `--advertised-product` 支持两种格式：每日明细（`sp_advertised_product_daily.xlsx`，中文列名）或摘要汇总（英文列名），自动识别无需手动区分。

3. **如需回退到 v1 / v0**（用户明确要求时）：

   ```bash
   python "<skill_dir>/scripts/run_targeting_structure_report_v0.py" \
     --advertised-product "<AP_REPORT.xlsx>" \
     --targeting "<TARGETING_REPORT.xlsx>" \
     --asin <ASIN1> \
     --asin <ASIN2> \
     --target-acos <0.15> \
     --avg-clicks-per-order <7> \
     --out-dir "<输出目录>"
   ```

4. **依赖**：需要 `pandas`、`numpy`、`openpyxl`。若缺失则 `pip install pandas numpy openpyxl`。

5. **交付物**（向用户说明生成位置）：
   - `report_{标识}_{起止日}.md`（v1）或 `report_{ASIN}_{今日}.md`（v0）：完整 Markdown 报告
   - `targeting_labels_{标识}_{起止日}.csv`：每行一个 Targeting，含标签与调价建议
   - 终端中的**样式文本报告**（脚本会打印）

6. **多 ASIN**：v1 中所有 ASIN 在 `--asin` 后空格分隔；报告主 ASIN 使用列表中第一个。数据层面合并所有列出 ASIN 对应的 Campaign/Ad Group。

## 可选参数（两版本通用）

| 参数 | 默认 | 含义 |
|------|------|------|
| `--analysis-days` | 14 | 分析窗口（天），以 Targeting 报告日期列最大日为结束日向前推 |
| `--core-sales-share` | 0.2 | 销售占比 ≥ 该值视为核心流量，高 ACoS 时建议保护 |
| `--no-styled-print` | false | 不打印样式文本报告（仅生成文件）|

## v2 / v1 新增参数

| 参数 | 必填 | 含义 |
|------|------|------|
| `--product-target` | 是 | 产品/组合标识符，如 `DL_ASIN`、`SL_KW`，用于输出目录与文件命名 |

## 报告内容概要（便于口头摘要）

- 账户健康度：整体 ACoS、与目标缺口、日均订单、订单波动 CV、不出单花费占比、总点击/订单/花费/销售额/CVR（v1）
- 流量集中度：Top 3/5/10/20 销售占比
- 投放分层：低/高 ACoS 出单、高/低点击不出单、无点击；分层花费与销售
- 核心流量保护名单：出单前 10 的 Targeting
- 高 ACoS 非保护 Top10 及建议调价幅度

## 若无法运行脚本

在用户明确要求且环境无 Python 时，再考虑用项目内 `targeting_structure_report.ipynb` 逻辑手写等价代码；否则**默认跑 v2 脚本**。

## 脚本维护

逻辑与 `Marketing Projects/Open Source - Time-Series Bidding System/targeting_structure_report.ipynb` 对齐；列名依赖亚马逊导出模板：`Advertised ASIN`、`Campaign Name`、`Ad Group Name`、`Date`、`Impressions`、`Clicks`、`7 Day Total Orders (#)`、`Spend`、`7 Day Total Sales`、`Targeting`、`Match Type`。
