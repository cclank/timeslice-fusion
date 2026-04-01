#!/bin/bash
# TimeSlice Fusion 管理脚本

case "$1" in
    start)
        echo "🚀 启动 TimeSlice Fusion 服务..."
        if pgrep -f "uv run server.py" > /dev/null; then
            echo "⚠️  服务已在运行"
        else
            PORT=${2:-8000}
            echo "🌐 在端口 $PORT 上启动服务..."
            uv run server.py &
            echo "✅ 服务已在后台启动，访问 http://localhost:$PORT"
        fi
        ;;
    stop)
        echo "🛑 正在停止 TimeSlice Fusion 服务..."
        pids=$(ps aux | grep "uv run server.py" | grep -v grep | awk '{print $2}')
        if [ -z "$pids" ]; then
            echo "❌ 未找到运行中的服务器进程"
        else
            echo "mPid(s) found: $pids"
            kill $pids
            echo "✅ 服务器已停止"
        fi
        ;;
    restart)
        $0 stop
        sleep 2
        $0 start ${2}
        ;;
    status)
        if pgrep -f "uv run server.py" > /dev/null; then
            echo "✅ TimeSlice Fusion 服务正在运行"
            ps aux | grep "uv run server.py" | grep -v grep
        else
            echo "🔴 TimeSlice Fusion 服务未运行"
        fi
        ;;
    *)
        echo "📖 TimeSlice Fusion 管理脚本"
        echo ""
        echo "用法: $0 {start|stop|restart|status} [port]"
        echo ""
        echo "命令:"
        echo "  start [port]  - 启动服务 (默认端口 8000)"
        echo "  stop          - 停止服务"
        echo "  restart [port]- 重启服务"
        echo "  status        - 查看服务状态"
        echo ""
        ;;
esac