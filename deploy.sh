#!/bin/bash
# deploy.sh — 拉取最新代码并重启 ads_funnel 服务

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$PROJECT_DIR/ads_funnel"
APP="start.py"
LOG="$APP_DIR/app.log"
BRANCH="dev"
PORT=5001

echo "========================================"
echo "  Ads Dashboard 部署脚本"
echo "  目录: $PROJECT_DIR"
echo "========================================"

# 1. 拉取最新代码
echo ""
echo "▶ 拉取代码 (origin/$BRANCH)..."
cd "$PROJECT_DIR"
git checkout -- deploy.sh
git pull origin "$BRANCH"

# 2. 停止旧进程
echo ""
echo "▶ 停止旧服务..."
pkill -f "python3 $APP" 2>/dev/null && echo "  旧进程已停止" || echo "  无运行中的旧进程"
sleep 1

# 3. 启动新进程
echo ""
echo "▶ 启动服务..."
cd "$APP_DIR"
nohup python3 "$APP" > nohup.out 2>&1 &
sleep 2

# 4. 检查是否成功启动
if pgrep -f "python3 $APP" > /dev/null; then
    echo ""
    echo "✅ 服务启动成功！"
    echo "   PID: $(pgrep -f "python3 $APP")"
    echo "   端口: $PORT"
    echo "   日志: tail -f $LOG"
else
    echo ""
    echo "❌ 服务启动失败，查看日志："
    tail -20 "$LOG"
    exit 1
fi

echo "========================================"
