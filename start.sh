#!/bin/bash
# YouTube Comment Research — Local Web App Launcher
# 用法: ./start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 配置（按需修改）──
export YOUTUBE_API_KEY="${YOUTUBE_API_KEY:-AIzaSyADIpiZxbIQ1fBGuibL7Sqjh_7z8B0pF1g}"
export CAPTION_PROXY="${CAPTION_PROXY:-http://127.0.0.1:7897}"  # Clash 代理（字幕需要）
export PORT="${PORT:-5000}"

# ── 安装依赖 ──
pip3 install -q flask requests gunicorn openpyxl yt-dlp 2>/dev/null

# ── 启动 ──
echo ""
echo "  🚀 Comment Research · Local"
echo "  http://localhost:$PORT"
echo ""
python3 app.py
