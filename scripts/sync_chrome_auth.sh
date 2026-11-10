#!/bin/bash
# 从用户日常 Chrome 导入 bigmodel 鉴权材料（无需人工登录）。
#
# 由 SwiftBar 插件菜单「从 Chrome 同步登录态」调用（bash=... terminal=true）。
#
# 2026-11-10 新增：此前只能靠「重新登录（浏览器）」人工过拼图验证，
# 而该菜单项因 bash= 写法错误根本拉不起浏览器（见 llm-usage.10m.sh 注释）。
# 本脚本走 Chrome cookie 路径，可直接解决智谱的登录态问题。
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/Users/hanamaki_mac_mini/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

cd "$ROOT"
"$PY" scripts/chrome_cookie_import.py
