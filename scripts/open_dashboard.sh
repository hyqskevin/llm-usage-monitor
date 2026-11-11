#!/bin/bash
# 重新生成 dashboard.html 并用默认浏览器打开。
# 由 SwiftBar 插件菜单「打开仪表盘」调用（bash=... terminal=false）。
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/Users/hanamaki_mac_mini/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

"$PY" "$ROOT/dashboard.py" >/dev/null 2>&1
/usr/bin/open "$ROOT/data/dashboard.html"
