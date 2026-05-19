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
git pull origin "$BRANCH"

# 2. 停止旧进程
echo ""
echo "▶ 停止旧服务..."
OLD_PID=$(lsof -t -i:$PORT 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    kill -9 $OLD_PID
    echo "  已停止 PID $OLD_PID"
else
    echo "  无运行中的旧进程"
fi
sleep 1

# 3. 启动新进程
echo ""
echo "▶ 启动服务..."
cd "$APP_DIR"
nohup python3 "$APP" > "$LOG" 2>&1 &
sleep 2

# 4. 检查是否成功启动
NEW_PID=$(lsof -t -i:$PORT 2>/dev/null || true)
if [ -n "$NEW_PID" ]; then
    echo ""
    echo "✅ 服务启动成功！"
    echo "   PID: $NEW_PID"
    echo "   端口: $PORT"
    echo "   日志: tail -f $LOG"
else
    echo ""
    echo "❌ 服务启动失败，查看日志："
    tail -20 "$LOG"
    exit 1
fi

echo "========================================"
