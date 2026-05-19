#!/bin/bash
# deploy.sh — 拉取最新代码并重启 ads_funnel 服务
# 用法：bash deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/ads_funnel"
PORT=5001
LOG="$APP_DIR/app.log"

echo "=== [1/3] 拉取最新代码 ==="
cd "$SCRIPT_DIR"
git pull origin dev

echo "=== [2/3] 停止旧服务（端口 $PORT）==="
OLD_PID=$(lsof -t -i:$PORT 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
  kill "$OLD_PID"
  sleep 1
  echo "已停止 PID $OLD_PID"
else
  echo "端口 $PORT 无运行中的服务"
fi

echo "=== [3/3] 启动服务 ==="
cd "$APP_DIR"
nohup python3 start.py > "$LOG" 2>&1 &
NEW_PID=$!
echo "已启动 PID $NEW_PID，日志：$LOG"

sleep 3
if curl -s http://localhost:$PORT/api/health > /dev/null 2>&1; then
  echo "=== 部署成功 ✓ http://localhost:$PORT ==="
else
  echo "=== 警告：服务可能尚未就绪，请检查日志：tail -f $LOG ==="
fi
