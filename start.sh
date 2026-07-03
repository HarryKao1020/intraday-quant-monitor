#!/usr/bin/env bash
# 盤中量化監看儀表板 一鍵啟動
# 用法：./start.sh   （首次請先 chmod +x start.sh）
# 直接用 finlab 環境的 python，不需先 conda activate。
set -e

cd "$(dirname "$0")"
PY=/opt/homebrew/Caskroom/miniforge/base/envs/finlab/bin/python

echo "啟動盤中量化監看儀表板…（讀部位 + 抓歷史約 1 分鐘）"
echo "完成後開 http://127.0.0.1:8050  ，按 Ctrl-C 結束"
PYTHONPATH=. exec "$PY" -m output.dash_app
