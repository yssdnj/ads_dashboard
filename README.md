# 广告漏斗分析 — 项目说明

Amazon 广告数据可视化工具，支持从领星后台导出的 Excel 生成交互式分析报告。

## 目录结构

```
ads_dashboard/
├── legacy/          # v1.0 历史存档（只读）
├── ads_funnel/      # v2.0 当前活跃版本（FastAPI + SQLite）
├── data/            # 原始 Excel 数据（领星导出）
└── v2_architecture.svg
```

---

## v2.0 — 当前版本（推荐使用）

**本地启动：**

```bash
cd ads_funnel
python start.py
```

浏览器自动打开 → `http://localhost:5001`

---

## 云服务器部署

**1. 拉取代码（首次）**

```bash
git clone -b dev https://github.com/yssdnj/ads_dashboard.git
cd ads_dashboard/ads_funnel
```

**2. 安装依赖**

```bash
pip install fastapi uvicorn[standard] python-multipart pandas openpyxl numpy
```

**3. 启动服务（后台运行）**

```bash
nohup uvicorn api.main:app --host 0.0.0.0 --port 5001 > app.log 2>&1 &
```

访问地址：`http://<服务器IP>:5001`

**4. 验证服务正常**

```bash
curl http://localhost:5001/api/health
# 返回 {"status":"ok","report_count":0} 表示正常
```

**5. 查看日志 / 停止服务**

```bash
tail -f app.log                  # 查看日志
kill $(lsof -t -i:5001)          # 停止服务
```

**6. 更新代码后重启**

```bash
git pull origin dev
kill $(lsof -t -i:5001)
nohup uvicorn api.main:app --host 0.0.0.0 --port 5001 > app.log 2>&1 &
```

**功能：**
- 在线上传领星 Excel，自动解析并入库
- 多报告管理（历史记录、删除、切换）
- 在线查看交互报告（总览 / L1 / L2 / L3 / 分析建议）
- 导出为离线 HTML、JSON、CSV

**依赖：**
```bash
pip install fastapi uvicorn[standard] python-multipart pandas openpyxl numpy
```

---

## v1.0 — 历史存档（只读）

位于 `legacy/`，直接用浏览器打开 HTML 文件即可查看，无需运行任何脚本。

> 仅供查看和对比，不再修改。

---

## 数据来源（领星后台导出）

| 文件 | 导出位置 |
|------|----------|
| 全部每日明细.xlsx | 广告管理 → 广告活动 → 导出 → 每日明细 |
| 广告组合每日明细.xlsx | 广告管理 → 广告组合 → 导出 → 每日明细 |

原始文件存放于 `data/` 目录。
