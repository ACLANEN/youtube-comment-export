#!/bin/bash
cd "$(dirname "$0")"
export YOUTUBE_API_KEY=${YOUTUBE_API_KEY:-$(grep YOUTUBE_API_KEY .env 2>/dev/null | cut -d= -f2)}
export PORT=${PORT:-5000}
echo "🚀 Comment Research by Fynn"
echo "   http://localhost:$PORT"
python3 run.py
