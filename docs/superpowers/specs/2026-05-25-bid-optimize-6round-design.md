# 批量竞价优化 — 六轮分析设计文档

**日期**：2026-05-25  
**状态**：已确认，待实施

---

## 背景

现有「批量竞价优化」功能采用三轮分析（R1=1周 / R2=3周 / R3=6周），投票门槛 ≥2/3。  
三轮跨度较大，无法识别趋势方向（向好 vs 向差）。  
本次新增「批量竞价优化-6轮」tab，用于对比验证，由用户决定后续保留哪个版本。

---

## 目标

1. 新增六轮分析功能，每轮递增一周（R1=1w … R6=6w）
2. Consensus 加入趋势方向判断，向好的 targeting 自动排除
3. 原有三轮 tab 完全保留不动，两套并行运行供对比

---

## 轮次定义

| 轮次 | 时间窗口 | 起始点 |
|------|---------|--------|
| R1 | 最近 1 周 | `week_sundays[-1]` |
| R2 | 最近 2 周 | `week_sundays[-2]` |
| R3 | 最近 3 周 | `week_sundays[-3]` |
| R4 | 最近 4 周 | `week_sundays[-4]` |
| R5 | 最近 5 周 | `week_sundays[-5]` |
| R6 | 最近 6 周 | `week_sundays[0]` |

> 若实际数据不足 6 周（如 n=4），R5/R6 自动退化为与 R4 相同的起始点（`week_sundays[0]`），不报错。

---

## Consensus 规则

### 进入门槛
≥3/6 轮命中调价标签（`_ADJ_LABELS`）

### 排除规则（向好模式）
满足以下任一条件则**不进入** consensus：

| 命中数 | 排除条件 | 解读 |
|--------|---------|------|
| =3/6 | 仅 R4、R5、R6 命中，R1/R2/R3 均未命中 | 近 3 周干净，明显好转 |
| =4/6 | 仅 R3、R4、R5、R6 命中，R1/R2 均未命中 | 近 2 周干净，趋势向好 |

≥5/6 命中时无排除规则，无条件进入 consensus。

### 降幅策略
与现有三轮逻辑相同：取各命中轮 `adj_pct` 中绝对值最小（最保守）的值，不按命中数放大。

### 字段来源
- **指标数据（metrics）**：来自 R6（数据最全，等价于原三轮的 R3）
- **标签 label**：来自 R1（最新状态）
- **hit_rounds**：记录命中的轮次字符串，如 `R1,R2,R4`

---

## UI 设计

### 新增 Tab 位置
在现有「批量竞价优化」tab 旁边新增「批量竞价优化-6轮」tab，两者独立。

### 六轮 Tab 内部标签顺序
```
[✦ Consensus]  [R1·1周]  [R2·2周]  [R3·3周]  [R4·4周]  [R5·5周]  [R6·6周]
```
默认展示 Consensus tab。

### 原三轮 Tab 不变
```
[R1]  [R2]  [R3]  [✦ Consensus]
```

---

## 改动范围

| 文件 | 类型 | 内容 |
|------|------|------|
| `api/targeting_analysis.py` | 新增函数 | `run_multi_round_analysis_6r()` |
| `api/main.py` | 新增路由 | `POST /api/analysis/mode1/bid-optimize-6r` |
| `template.html` | 新增 tab | 「批量竞价优化-6轮」tab + 对应 JS/HTML |

**原有代码完全不动**：`run_multi_round_analysis()`、原路由、原 tab。

---

## 数据流

```
用户点击「执行分析」（6轮 tab）
  ↓ POST /api/analysis/mode1/bid-optimize-6r
  ↓ targeting_analysis.run_multi_round_analysis_6r()
      ↓ 跑 R1~R6 共 6 轮 run_analysis()
      ↓ 投票：≥3/6 命中 → 候选
      ↓ 排除向好模式（=3/6 仅R4-R6 / =4/6 仅R3-R6）
      ↓ 返回 {R1, R2, R3, R4, R5, R6, consensus}
  ↓ 前端渲染 7 个子标签，默认展示 Consensus
```

---

## 不在本次范围内

- 三轮 tab 的任何修改
- 命中数影响降幅（保留现有保守策略）
- 环境配置（dev/prod）相关改动
