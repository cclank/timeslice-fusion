#!/bin/bash
# TimeSlice Fusion 停止脚本

echo "🛑 停止 TimeSlice Fusion 服务..."

# 查找并终止服务器进程
pids=$(ps aux | grep "uv run server.py" | grep -v grep | awk '{print $2}')

if [ -z "$pids" ]; then
    echo "❌ 未找到运行中的服务器进程"
else
    echo "mPid(s) found: $pids"
    kill $pids
    echo "✅ 服务器已停止"
fi