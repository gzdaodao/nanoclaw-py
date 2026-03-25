#!/bin/bash
#
echo "Starting HTTP server in background..."
#
# 进入包含文件的目录
cd /root/cache/
#
# 使用 Python 启动 HTTP 服务器（后台运行）
python3 -m http.server 8080 &
#
# 等待服务器启动
sleep 2
echo "HTTP server started on port 8080 with PID: $SERVER_PID"

pip install playwright
export PLAYWRIGHT_DOWNLOAD_HOST=http://localhost:8080/

# 安装 Playwright 依赖
playwright install chromium 
export PLAYWRIGHT_DOWNLOAD_HOST=

playwright install-deps


