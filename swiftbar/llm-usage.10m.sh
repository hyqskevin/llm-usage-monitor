#!/bin/bash
# <bitbar.title>LLM 用量</bitbar.title>
# <bitbar.version>v1.1</bitbar.version>
# <bitbar.author>kevin_w</bitbar.author>
# <bitbar.desc>状态栏展示 GLM / Kimi / Kimi Code / MiniMax 剩余额度</bitbar.desc>
# <bitbar.dependencies>python3</bitbar.dependencies>
#
# SwiftBar 插件：每 10 分钟自动执行一次（文件名 llm-usage.10m.sh）

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/usr/bin/python3"

# 采集（失败不阻塞菜单显示，用上次结果）
JSON=$($PY "$ROOT/fetch.py" --json 2>/dev/null)
[ -z "$JSON" ] && JSON=$(cat "$ROOT/data/latest.json" 2>/dev/null)

$PY - "$JSON" "$ROOT" <<'EOF'
import json, sys
from datetime import datetime

NAMES = {'bigmodel': 'GLM', 'kimi': 'Kimi', 'kimi-code': 'KimiCode', 'minimax': 'MM'}
FULL  = {'bigmodel': '智谱 GLM', 'kimi': 'Kimi（订阅总额）',
         'kimi-code': 'Kimi Code', 'minimax': 'MiniMax'}
WNAMES = {'5h': '5小时', 'week': '周', 'billing': '账期'}
# 菜单栏每个站点用哪个窗口代表剩余量
TITLE_WINDOW = {'bigmodel': '5h', 'kimi': 'billing', 'kimi-code': '5h', 'minimax': '5h'}
ROOT = sys.argv[2]

def bar(pct, width=10):
    """文字进度条：▓ 已用 ░ 剩余"""
    if pct is None:
        return '░' * width
    filled = round(min(100, max(0, pct)) / 100 * width)
    return '▓' * filled + '░' * (width - filled)

try:
    data = json.loads(sys.argv[1])
except Exception:
    print('LLM ⚠️')
    print('---')
    print('数据不可用 | color=red')
    sys.exit(0)

rows = data.get('rows', [])
errors = data.get('errors', {})

def remain(site, window):
    for r in rows:
        if r['site'] == site and r['window'] == window and r['used_pct'] is not None:
            return 100 - r['used_pct']
    return None

parts = []
for site in ('bigmodel', 'kimi', 'kimi-code', 'minimax'):
    p = remain(site, TITLE_WINDOW[site])
    parts.append(f"{NAMES[site]} {p:.0f}%" if p is not None else f"{NAMES[site]} ?")
print(' '.join(parts) + (' ⚠️' if errors else ''))

print('---')
ts = data.get('ts', '')
if ts:
    local = datetime.fromisoformat(ts).astimezone().strftime('%H:%M:%S')
    print(f"更新于 {local} | color=gray")
for site in ('bigmodel', 'kimi', 'kimi-code', 'minimax'):
    site_rows = [r for r in rows if r['site'] == site]
    if not site_rows:
        continue
    print(f"{FULL[site]} | size=13 color=gray")
    for r in site_rows:
        pct = r['used_pct']
        label = WNAMES.get(r['window'], r['window'])
        if pct is not None:
            color = '#c0665f' if pct >= 90 else ('#c9a86a' if pct >= 70 else '#8a8680')
            txt = f"{bar(pct)} {pct:5.1f}% {label}"
            if r.get('total'):
                txt += f"（{r['used']:.0f}/{r['total']:.0f}）"
            print(txt + f' | font=Menlo size=12 color={color}')
        elif r.get('used') and r['window'] == 'billing':
            print(f"{label}: 近30天 {r['used']/1e6:.0f}M tokens | font=Menlo size=12 color=#8a8680")
if errors:
    print('---')
    for site, err in errors.items():
        print(f"{NAMES.get(site, site)} 抓取失败: {err[:60]} | color=red size=11")
print('---')
print('刷新 | refresh=true')
print(f'打开仪表盘 | bash=/usr/bin/open param0={ROOT}/data/dashboard.html terminal=false')
print(f'重新登录（浏览器）| bash=/bin/zsh param0=-c param1="cd {ROOT} && NODE_PATH=/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules /Users/hanamaki_mac_mini/.workbuddy/binaries/node/versions/22.22.2-2/bin/node scripts/login.js" terminal=true')
EOF
