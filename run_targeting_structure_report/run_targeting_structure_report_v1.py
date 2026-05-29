#!/usr/bin/env python3
"""
Amazon Sponsored Products — Targeting 结构分析报告
与 targeting_structure_report.ipynb 逻辑对齐。
依赖: pandas, numpy, openpyxl
"""
from __future__ import annotations

import argparse
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

TARGETING_KEY = ["Campaign Name", "Ad Group Name", "Targeting", "Match Type"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Targeting 结构分析：生成 MD + CSV + 样式文本")
    p.add_argument("--advertised-product", required=True, type=Path, help="Advertised product report .xlsx")
    p.add_argument("--targeting", required=True, type=Path, help="Targeting report .xlsx")
    p.add_argument("--product-target", type=str, required=True, help="产品标识，如 DL_ASIN / DL_KW")
    p.add_argument("--asin", nargs="+", dest="asins", required=True, help="ASIN（可多次指定）")
    p.add_argument("--target-acos", type=float, required=True, help="目标 ACoS，如 0.15 表示 15%%")
    p.add_argument("--avg-clicks-per-order", type=float, required=True, help="平均出单点击数")
    p.add_argument("--out-dir", type=Path, default=Path("/home/admin/.openclaw/workspace-amz/.agents/skills/amazon-targeting-structure-report/report/"), help="输出目录")
    p.add_argument("--analysis-days", type=int, default=14, help="分析窗口天数")
    p.add_argument("--core-sales-share", type=float, default=0.20, help="销售占比≥此值视为核心流量")
    p.add_argument("--no-styled-print", action="store_true", help="不打印样式文本报告")
    return p.parse_args()


def load_frames(ap_path: Path, tar_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    ap_raw = pd.read_excel(ap_path)
    tar_raw = pd.read_excel(tar_path)
    ap_raw.columns = [c.strip() for c in ap_raw.columns]
    tar_raw.columns = [c.strip() for c in tar_raw.columns]
    tar_raw["Date"] = pd.to_datetime(tar_raw["Date"])
    ap_raw["Start Date"] = pd.to_datetime(ap_raw["Start Date"])
    ap_raw["End Date"] = pd.to_datetime(ap_raw["End Date"])
    return ap_raw, tar_raw


def get_label(row, target_acos: float, avg_clicks_per_order: float) -> str:
    if row["orders"] == 0:
        if row["clicks"] >= avg_clicks_per_order:
            return "🔴 高点击不出单"
        if row["clicks"] > 0:
            return "⚪ 低点击不出单"
        return "— 无点击"
    if pd.notna(row["acos"]) and row["acos"] > target_acos:
        return "⚠️ 高ACoS出单"
    return "✅ 低ACoS出单"


def get_action(row, target_acos: float, core_sales_share: float, core_keys: set) -> tuple[str, str, str, str]:
    label = row["label"]
    key = (
        row["Campaign Name"],
        row["Ad Group Name"],
        row["Targeting"],
        row["Match Type"],
    )
    is_core = key in core_keys or row["sales_share"] >= core_sales_share

    if "无点击" in label:
        return "观察", "0%", "$0.00", "无流量，检查出价是否过低"

    if "低ACoS出单" in label:
        if is_core:
            return "🔒 保护", "0%", "$0.00", "核心流量，维持出价"
        return "↗ 维持/提价", "+0~5%", "$0.00", "表现良好，可小幅提价测试"

    if "高ACoS出单" in label:
        if is_core:
            return "🔒 保护", "0%", "$0.00", "核心流量，谨慎调整"
        ratio = row["acos"] / target_acos if pd.notna(row["acos"]) else 1
        if ratio <= 1.2:
            pct, reason = -5, f"ACoS超出目标{(ratio - 1) * 100:.0f}%，小幅降价"
        elif ratio <= 1.5:
            pct, reason = -10, f"ACoS超出目标{(ratio - 1) * 100:.0f}%，中幅降价"
        else:
            pct, reason = -15, f"ACoS严重偏高（{(ratio - 1) * 100:.0f}%），大幅降价"
        cpc = row["cpc"] if pd.notna(row["cpc"]) else 0
        adj_dollar = max(abs(cpc * pct / 100), 0.01)
        return "↘ 降价", f"{pct}%", f"${adj_dollar:.2f}", reason

    if "高点击不出单" in label:
        cpc = row["cpc"] if pd.notna(row["cpc"]) else 0
        adj_dollar = max(cpc * 0.20, 0.01)
        return (
            "⛔ 大幅降价/暂停",
            "-20%",
            f"${adj_dollar:.2f}",
            f'已积累{int(row["clicks"])}次点击仍无出单，转化严重不足',
        )

    if "低点击不出单" in label:
        cpc = row["cpc"] if pd.notna(row["cpc"]) else 0
        adj_dollar = max(cpc * 0.10, 0.01)
        return "↘ 降价", "-10%", f"${adj_dollar:.2f}", "无出单，降低无效花费"

    return "观察", "0%", "$0.00", ""


def layer_summary(agg: pd.DataFrame, label_kw: str) -> tuple:
    sub = agg[agg["label"].str.contains(label_kw)]
    return len(sub), sub["spend"].sum(), sub["sales"].sum(), sub["orders"].sum()


def print_styled_report(
    *,
    W: int,
    demo_asin: str,
    analysis_days: int,
    data_start,
    data_end,
    today_str: str,
    target_acos: float,
    overall_acos: float,
    acos_gap: float,
    acos_status,
    avg_daily_orders: float,
    std_daily_orders: float,
    cv: float,
    cv_status: str,
    n_effective: int,
    n_total: int,
    zero_order_pct: float,
    z0_status: str,
    agg_sorted: pd.DataFrame,
    top3_share: float,
    top5_share: float,
    top10_share: float,
    top20_share: float,
    total_sales: float,
    total_spend: float,
    total_clicks: int,
    total_orders: int,
    avg_cvr:float,
    l_low,
    l_high,
    l_hcno,
    l_lcno,
    l_noclk,
    zero_order_spend: float,
    protection_list: pd.DataFrame,
    n_zero_order: int,
    n_high_acos: int,
    high_acos_adj: pd.DataFrame,
    label_counts: pd.Series,
) -> None:
    def _line(ch: str = "─", w: int = W) -> None:
        print(ch * w)

    def _title(s: str) -> None:
        print(f"\n▌ {s}")
        _line("─")

    def _kv(label: str, value: str, extra: str = "") -> None:
        if extra:
            print(f"  {label:<20} {str(value):<24}  {extra}")
        else:
            print(f"  {label:<20} {value}")

    def _table(headers: list[str], rows: list[tuple], widths: list[int]) -> None:
        def fmt_row(cells: tuple) -> str:
            parts = []
            for c, w in zip(cells, widths):
                s = str(c) if c is not None else ""
                parts.append(s[: w - 1].ljust(w))
            return "│ " + " │ ".join(parts) + " │"

        top = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
        mid = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
        bot = "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"
        print(top)
        print(fmt_row(tuple(headers)))
        print(mid)
        for r in rows:
            print(fmt_row(r))
        print(bot)

    _line("═")
    print(f"  Targeting 结构分析报告".center(W))
    print(
        f"  ASIN {demo_asin}  ·  {analysis_days}天窗口  ·  {data_start.date()} → {data_end.date()}  ·  目标ACoS {target_acos*100:.0f}%".center(
            W
        )
    )
    print(f"  生成时间 {today_str}".center(W))
    _line("═")

    _title("一、账户健康度")
    _kv(f"{analysis_days}天整体 ACoS", f"{overall_acos*100:.1f}%", acos_status)
    _kv("ACoS 缺口", f"{acos_gap*100:+.1f}%", "")
    _kv("日均订单量", f"{avg_daily_orders:.1f} 单", "")
    _kv("日订单标准差", f"{std_daily_orders:.1f} 单", "")
    _kv("变异系数 CV", f"{cv:.2f}", cv_status)
    _kv("有效投放", f"{n_effective} / {n_total}", "")
    _kv("不出单花费占比", f"{zero_order_pct:.1%}", z0_status)
    # <editor-fold desc="Larry 2026年4月22日">
    _kv("总点击", f"{total_clicks} 次", "")
    _kv("总订单", f"{total_orders} 单", f"CVR={avg_cvr*100:.2f}%")
    _kv("总花费", f"$ {total_spend:.2f} ", "")
    _kv("总销售额", f"$ {total_sales:.2f}", "")
    # </editor-fold>

    _title("二、流量集中度")
    _table(
        ["区间", "销售额($)", "销售占比"],
        [
            ("Top 3", f'{agg_sorted.head(3)["sales"].sum():,.0f}', f"{top3_share:.1%}"),
            ("Top 5", f'{agg_sorted.head(5)["sales"].sum():,.0f}', f"{top5_share:.1%}"),
            ("Top 10", f'{agg_sorted.head(10)["sales"].sum():,.0f}', f"{top10_share:.1%}"),
            ("Top 20", f'{agg_sorted.head(20)["sales"].sum():,.0f}', f"{top20_share:.1%}"),
            (f"全部 {n_total} 个", f"{total_sales:,.0f}", "100%"),
        ],
        [14, 18, 14],
    )

    _title("三、投放结构分层")
    _table(
        ["层级", "投放数", "花费($)", "花费%", "销售($)", "销售%", "订单"],
        [
            ("✅ 低ACoS出单", l_low[0], f"{l_low[1]:,.0f}", f"{l_low[1]/total_spend:.1%}", f"{l_low[2]:,.0f}", f"{l_low[2]/total_sales:.1%}", int(l_low[3])),
            ("⚠️ 高ACoS出单", l_high[0], f"{l_high[1]:,.0f}", f"{l_high[1]/total_spend:.1%}", f"{l_high[2]:,.0f}", f"{l_high[2]/total_sales:.1%}", int(l_high[3])),
            ("🔴 高点击不出单", l_hcno[0], f"{l_hcno[1]:,.0f}", f"{l_hcno[1]/total_spend:.1%}", "0", "0%", 0),
            ("⚪ 低点击不出单", l_lcno[0], f"{l_lcno[1]:,.0f}", f"{l_lcno[1]/total_spend:.1%}", "0", "0%", 0),
            ("— 无点击", l_noclk[0], "0", "0%", "0", "0%", 0),
        ],
        [18, 8, 12, 8, 12, 8, 6],
    )
    print(f"  不出单总花费: ${zero_order_spend:,.2f}（占比 {zero_order_pct:.1%}） {z0_status}")

    _title("四、核心流量保护名单（出单前10）")
    prot_rows = []
    for _, r in protection_list.iterrows():
        tgt = str(r["Targeting"])[:26] + ("…" if len(str(r["Targeting"])) > 26 else "")
        prot_rows.append(
            (
                int(r["rank"]),
                tgt,
                str(r["Match Type"])[:10],
                int(r["orders"]),
                f'{r["sales"]:,.0f}',
                f'{r["sales_share_pct"]}%',
                f'{r["cum_sales_share"]}%',
                "PROTECT",
            )
        )
    _table(
        ["#", "Targeting", "Match", "订单", "销售$", "销售%", "累计%", "建议"],
        prot_rows,
        [3, 28, 10, 6, 10, 8, 8, 9],
    )

    _title("五、优化建议摘要")
    print(
        "  1. 清理不出单花费 — "
        f"{n_zero_order} 个投放，花费 ${zero_order_spend:,.2f}，建议降价 10–20%"
    )
    print(
        "  2. 压缩高ACoS出单 — "
        f"{n_high_acos} 个投放（已排除核心），花费 ${l_high[1]:,.2f}，按超出幅度降价 5–15%"
    )
    print("  3. 保护核心流量 — " f"前10 出单投放贡献 {top10_share:.1%} 销售额，谨慎动价")

    _title("高ACoS出单 Top 10（非保护）")
    ha_rows = []
    for _, r in high_acos_adj.iterrows():
        tgt = str(r["Targeting"])[:24] + ("…" if len(str(r["Targeting"])) > 24 else "")
        ha_rows.append(
            (
                tgt,
                str(r["Match Type"])[:8],
                f'{r["acos"]*100:.1f}%',
                f'{r["sales_share"]*100:.1f}%',
                str(r["action"])[:10],
                f'{r["adj_pct"]} ({r["adj_dollar"]})',
            )
        )
    if ha_rows:
        _table(
            ["Targeting", "Match", "ACoS", "销售%", "建议", "调价"],
            ha_rows,
            [26, 10, 8, 8, 12, 16],
        )
    else:
        print("  （无非保护的高ACoS出单需展示）")

    _title("标签分布")
    for lbl, cnt in label_counts.items():
        print(f"  {lbl:<18} {cnt:>5} 个")

    _line("═")
    print("  以上为样式文本报告；完整 Markdown 已写入上述 .md 文件".center(W))
    _line("═")


def main() -> None:
    args = parse_args()
    asin_list = args.asins
    target_acos = args.target_acos
    avg_clicks = args.avg_clicks_per_order
    analysis_days = args.analysis_days
    core_sales_share = args.core_sales_share
    demo_asin = asin_list[0]
    product_target = args.product_target
    out_dir = args.out_dir
    ts_str = datetime.now().strftime("%Y%m%d%H%M%S")
    out_dir = out_dir / f"{ts_str}_{product_target}"
    out_dir.mkdir(parents=True, exist_ok=True)

    ap_raw, tar_raw = load_frames(args.advertised_product, args.targeting)

    report_start = tar_raw["Date"].min()
    report_end = tar_raw["Date"].max()
    ap_filtered = ap_raw[ap_raw["Advertised ASIN"].isin(asin_list)]
    cag_list = ap_filtered[["Campaign Name", "Ad Group Name"]].drop_duplicates().reset_index(drop=True)
    tar_filtered = tar_raw.merge(cag_list, on=["Campaign Name", "Ad Group Name"], how="inner")

    data_end = tar_filtered["Date"].max()
    data_start = data_end - pd.Timedelta(days=analysis_days - 1)
    tar_window = tar_filtered[tar_filtered["Date"] >= data_start]

    agg = (
        tar_window.groupby(TARGETING_KEY)
        .agg(
            impressions=("Impressions", "sum"),
            clicks=("Clicks", "sum"),
            orders=("7 Day Total Orders (#)", "sum"),
            spend=("Spend", "sum"),
            sales=("7 Day Total Sales", "sum"),
        )
        .reset_index()
    )

    agg["acos"] = np.where(agg["sales"] > 0, agg["spend"] / agg["sales"], np.nan)
    agg["cvr"] = np.where(agg["clicks"] > 0, agg["orders"] / agg["clicks"], np.nan)
    agg["cpc"] = np.where(agg["clicks"] > 0, agg["spend"] / agg["clicks"], np.nan)

    total_spend = agg["spend"].sum()
    total_sales = agg["sales"].sum()
    total_orders = agg["orders"].sum()
    # <editor-fold desc="# by Larry 2026年4月22日">
    total_clicks = agg["clicks"].sum()
    avg_cvr = total_orders / total_clicks if total_clicks > 0 else 0.0
    # </editor-fold>

    if total_sales <= 0:
        raise SystemExit("窗口内总销售为 0，无法计算 ACoS/销售占比，请检查 ASIN 与报告是否匹配。")

    agg["sales_share"] = agg["sales"] / total_sales
    agg["spend_share"] = agg["spend"] / total_spend if total_spend > 0 else 0

    daily_orders = (
        tar_window.groupby("Date")["7 Day Total Orders (#)"]
        .sum()
        .reindex(pd.date_range(data_start, data_end, freq="D"), fill_value=0)
    )
    avg_daily_orders = float(daily_orders.mean())
    std_daily_orders = float(daily_orders.std())
    cv = std_daily_orders / avg_daily_orders if avg_daily_orders > 0 else 0.0
    overall_acos = total_spend / total_sales
    acos_gap = overall_acos - target_acos
    n_effective = int((agg["orders"] > 0).sum())
    n_total = len(agg)

    agg_sorted = agg.sort_values("sales", ascending=False).reset_index(drop=True)
    agg_sorted["cumulative_sales"] = agg_sorted["sales"].cumsum()
    agg_sorted["cumulative_sales_share"] = agg_sorted["cumulative_sales"] / total_sales

    def top_n_share(n: int) -> float:
        return float(agg_sorted.head(n)["sales"].sum() / total_sales) if total_sales > 0 else 0.0

    top3_share = top_n_share(3)
    top5_share = top_n_share(5)
    top10_share = top_n_share(10)
    top20_share = top_n_share(20)

    orders_rank = agg.sort_values("orders", ascending=False).reset_index(drop=True)
    core_keys = set(
        zip(
            orders_rank.head(10)["Campaign Name"],
            orders_rank.head(10)["Ad Group Name"],
            orders_rank.head(10)["Targeting"],
            orders_rank.head(10)["Match Type"],
        )
    )

    agg["label"] = agg.apply(
        lambda r: get_label(r, target_acos, avg_clicks),
        axis=1,
    )
    actions = agg.apply(
        lambda r: pd.Series(
            get_action(r, target_acos, core_sales_share, core_keys),
            index=["action", "adj_pct", "adj_dollar", "reason"],
        ),
        axis=1,
    )
    agg = pd.concat([agg, actions], axis=1)
    label_counts = agg["label"].value_counts()

    today_str = datetime.now().strftime("%Y-%m-%d")

    protection_list = orders_rank.head(10).copy()
    protection_list["rank"] = range(1, len(protection_list) + 1)
    protection_list["sales_share_pct"] = (protection_list["sales_share"] * 100).round(1)
    protection_list["cum_sales_share"] = (protection_list["sales"].cumsum() / total_sales * 100).round(1)

    l_low = layer_summary(agg, "低ACoS出单")
    l_high = layer_summary(agg, "高ACoS出单")
    l_hcno = layer_summary(agg, "高点击不出单")
    l_lcno = layer_summary(agg, "低点击不出单")
    l_noclk = layer_summary(agg, "无点击")

    zero_order_spend = l_hcno[1] + l_lcno[1] + l_noclk[1]
    zero_order_pct = zero_order_spend / total_spend if total_spend > 0 else 0.0

    n_high_acos = l_high[0]
    n_zero_order = l_hcno[0] + l_lcno[0]

    acos_status = "🔴 偏高" if overall_acos > target_acos else "✅ 达标"
    cv_status = "✅ 稳定" if cv < 0.3 else ("⚠️ 一般" if cv < 0.5 else "🔴 波动大")
    z0_status = "✅ 正常" if zero_order_pct < 0.10 else ("⚠️ 偏高" if zero_order_pct < 0.15 else "🔴 超标")

    md_lines: list[str] = []
    md_lines += [
        f"# 广告结构分析报告\n",
        f"**ASIN**: `{demo_asin}` | **分析周期**: {analysis_days}天（{data_start.date()} → {data_end.date()}）",
        f" | **目标ACoS**: {target_acos*100:.0f}% | **生成时间**: {today_str}\n",
        "---\n",
        "## 一、账户健康度\n",
        "| 指标 | 数值 | 状态 |",
        "|---|---|---|",
        f"| {analysis_days}天整体 ACoS | {overall_acos*100:.1f}% | {acos_status} |",
        f"| ACoS 缺口 | {acos_gap*100:+.1f}% | — |",
        f"| 日均订单量 | {avg_daily_orders:.1f} 单 | — |",
        f"| 日订单标准差 | {std_daily_orders:.1f} 单 | — |",
        f"| 变异系数 CV | {cv:.2f} | {cv_status} |",
        f"| 有效投放数 | {n_effective} / {n_total} | — |",
        # <editor-fold desc="Larry 2026年4月22日">
        f"| 总点击 | {total_clicks} 次 | — |",
        f"| 总订单 | {total_orders} 单 | CVR={avg_cvr*100:.2f}% |",
        f"| 总花费 | $ {total_spend:.2f} | — |",
        f"| 总销售额 | $ {total_sales:.2f} | — |",
        # </editor-fold>

        "",
        "---\n",
        "## 二、流量集中度\n",
        "| 排名区间 | 销售额 | 占比 |",
        "|---|---|---|",
        f'| Top 3  | ${agg_sorted.head(3)["sales"].sum():,.0f} | {top3_share:.1%} |',
        f'| Top 5  | ${agg_sorted.head(5)["sales"].sum():,.0f} | {top5_share:.1%} |',
        f'| Top 10 | ${agg_sorted.head(10)["sales"].sum():,.0f} | {top10_share:.1%} |',
        f'| Top 20 | ${agg_sorted.head(20)["sales"].sum():,.0f} | {top20_share:.1%} |',
        f"| 全部 {n_total} 个 | ${total_sales:,.0f} | 100% |",
        "",
        "---\n",
        "## 三、投放结构分层\n",
        "| 层级 | 投放数 | 花费 | 花费占比 | 销售 | 销售占比 | 天订单 |",
        "|---|---|---|---|---|---|---|",
        f"| ✅ 低ACoS出单 | {l_low[0]} | ${l_low[1]:,.0f} | {l_low[1]/total_spend:.1%} | ${l_low[2]:,.0f} | {l_low[2]/total_sales:.1%} | {int(l_low[3])} |",
        f"| ⚠️ 高ACoS出单 | {l_high[0]} | ${l_high[1]:,.0f} | {l_high[1]/total_spend:.1%} | ${l_high[2]:,.0f} | {l_high[2]/total_sales:.1%} | {int(l_high[3])} |",
        f"| 🔴 高点击不出单 | {l_hcno[0]} | ${l_hcno[1]:,.0f} | {l_hcno[1]/total_spend:.1%} | $0 | 0% | 0 |",
        f"| ⚪ 低点击不出单 | {l_lcno[0]} | ${l_lcno[1]:,.0f} | {l_lcno[1]/total_spend:.1%} | $0 | 0% | 0 |",
        f"| — 无点击 | {l_noclk[0]} | $0 | 0% | $0 | 0% | 0 |",
        "",
        f"> **不出单总花费**: ${zero_order_spend:,.2f}（占比 {zero_order_pct:.1%}，目标 < 15%） {z0_status}",
        "",
        "---\n",
        "## 四、核心流量保护名单（出单前10）\n",
        "| 排名 | Targeting | Match Type | 订单 | 销售 | 销售占比 | 累计占比 | 建议 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, r in protection_list.iterrows():
        md_lines.append(
            f'| {int(r["rank"])} | {str(r["Targeting"])[:30]} | {r["Match Type"]} '
            f'| {int(r["orders"])} | ${r["sales"]:,.0f} '
            f'| {r["sales_share_pct"]}% | {r["cum_sales_share"]}% | 🔒 PROTECT |'
        )

    md_lines += [
        "",
        "---\n",
        "## 五、优化建议摘要\n",
        "### 优先行动（按影响程度排序）\n",
        f"1. **清理不出单花费** — 共 {n_zero_order} 个投放，"
        f"花费 ${zero_order_spend:,.2f}，建议降价 10-20%",
        f"2. **压缩高ACoS出单** — 共 {n_high_acos} 个投放（已排除核心流量），"
        f"花费 ${l_high[1]:,.2f}，按超出幅度降价 5-15%",
        f"3. **保护核心流量** — 前10出单投放贡献 {top10_share:.1%} 销售额，不做调整\n",
        "### 高ACoS出单 Top 10（需处理，已排除核心保护）\n",
        "| Targeting | Match | ACoS | 销售占比 | 建议 | 调价幅度 |",
        "|---|---|---|---|---|---|",
    ]
    high_acos_adj = (
        agg[(agg["label"].str.contains("高ACoS出单")) & (agg["action"] != "🔒 保护")]
        .sort_values("spend", ascending=False)
        .head(10)
    )
    for _, r in high_acos_adj.iterrows():
        md_lines.append(
            f'| {str(r["Targeting"])[:28]} | {r["Match Type"]} '
            f'| {r["acos"]*100:.1f}% | {r["sales_share"]*100:.1f}% '
            f'| {r["action"]} | {r["adj_pct"]} ({r["adj_dollar"]}) |'
        )

    md_content = "\n".join(md_lines)
    md_path = out_dir / f"report_{product_target}_{report_start.strftime('%Y-%m-%d')}_{report_end.strftime('%Y-%m-%d')}.md"
    md_path.write_text(md_content, encoding="utf-8")
    print(f"✓ MD 报告已保存: {md_path}")

    out_cols = TARGETING_KEY + [
        "impressions",
        "clicks",
        "orders",
        "spend",
        "sales",
        "acos",
        "cvr",
        "cpc",
        "sales_share",
        "spend_share",
        "label",
        "action",
        "adj_pct",
        "adj_dollar",
        "reason",
    ]
    csv_df = agg[out_cols].copy()
    csv_df["acos"] = (csv_df["acos"] * 100).round(1)
    csv_df["cvr"] = (csv_df["cvr"] * 100).round(2)
    csv_df["cpc"] = csv_df["cpc"].round(2)
    csv_df["sales_share"] = (csv_df["sales_share"] * 100).round(2)
    csv_df["spend_share"] = (csv_df["spend_share"] * 100).round(2)
    csv_df = csv_df.rename(
        columns={
            "acos": "ACoS(%)",
            "cvr": "CVR(%)",
            "cpc": "CPC($)",
            "sales_share": "销售占比(%)",
            "spend_share": "花费占比(%)",
        }
    )
    csv_df = csv_df.sort_values("orders", ascending=False)
    csv_path = out_dir / f"targeting_labels_{product_target}_{report_start.strftime('%Y-%m-%d')}_{report_end.strftime('%Y-%m-%d')}.csv"
    csv_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"✓ 标签表已保存: {csv_path}")
    print(f"  共 {len(csv_df)} 个 Targeting")

    if not args.no_styled_print:
        print_styled_report(
            W=78,
            demo_asin=demo_asin,
            analysis_days=analysis_days,
            data_start=data_start,
            data_end=data_end,
            today_str=today_str,
            target_acos=target_acos,
            overall_acos=overall_acos,
            acos_gap=acos_gap,
            acos_status=acos_status,
            avg_daily_orders=avg_daily_orders,
            std_daily_orders=std_daily_orders,
            cv=cv,
            cv_status=cv_status,
            n_effective=n_effective,
            n_total=n_total,
            zero_order_pct=zero_order_pct,
            z0_status=z0_status,
            agg_sorted=agg_sorted,
            top3_share=top3_share,
            top5_share=top5_share,
            top10_share=top10_share,
            top20_share=top20_share,
            total_sales=total_sales,
            total_spend=total_spend,
            total_clicks=total_clicks,
            total_orders=total_orders,
            avg_cvr=avg_cvr,
            l_low=l_low,
            l_high=l_high,
            l_hcno=l_hcno,
            l_lcno=l_lcno,
            l_noclk=l_noclk,
            zero_order_spend=zero_order_spend,
            protection_list=protection_list,
            n_zero_order=n_zero_order,
            n_high_acos=n_high_acos,
            high_acos_adj=high_acos_adj,
            label_counts=label_counts,
        )

if __name__ == "__main__":
    main()
