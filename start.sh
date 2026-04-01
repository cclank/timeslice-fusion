#!/bin/bash
# TimeSlice Fusion 启动脚本

echo "🚀 启动 TimeSlice Fusion 服务..."
echo "📦 检查依赖..."

# 检查 uv 是否安装
if ! command -v uv &> /dev/null; then
    echo "❌ uv 未找到，请先安装 uv"
    echo "   macOS/Linux: pip install uv"
    exit 1
fi

# 检查端口参数
PORT=${1:-8000}

echo "🌐 服务器将在 http://localhost:$PORT 启动"
echo "📝 日志将显示在终端中"
echo ""
echo "💡 提示: 按 Ctrl+C 停止服务器"
echo ""

# 运行服务器
uv run server.py