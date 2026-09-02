#!/bin/bash
# <bitbar.title>LLM 用量</bitbar.title>
# <bitbar.version>v1.0</bitbar.version>
# <bitbar.author>kevin_w</bitbar.author>
# <bitbar.desc>状态栏展示 GLM / Kimi / MiniMax 剩余额度</bitbar.desc>
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

NAMES = {'bigmodel': 'GLM', 'kimi': 'Kimi', 'minimax': 'MM'}
WNAMES = {'5h': '5小时', 'week': '周', 'billing': '账期'}
ROOT = sys.argv[2]

try:
    data = json.loads(sys.argv[1])
except Exception:
    print('LLM ⚠️')
    print('---')
    print('数据不可用 | color=red')
    sys.exit(0)

rows = data.get('rows', [])
errors = data.get('errors', {})

# 菜单栏：各站点 5h 剩余百分比
def remain(site):
    for r in rows:
        if r['site'] == site and r['window'] == '5h' and r['used_pct'] is not None:
            return 100 - r['used_pct']
    return None

parts = []
for site in ('bigmodel', 'kimi', 'minimax'):
    p = remain(site)
    parts.append(f"{NAMES[site]} {p:.0f}%" if p is not None else f"{NAMES[site]} ?")
print(' '.join(parts) + (' ⚠️' if errors else ''))

print('---')
ts = data.get('ts', '')
if ts:
    local = datetime.fromisoformat(ts).astimezone().strftime('%H:%M:%S')
    print(f"更新于 {local} | color=gray")
for site in ('bigmodel', 'kimi', 'minimax'):
    print(f"{NAMES[site]} | size=13 color=gray")
    for r in rows:
        if r['site'] != site:
            continue
        pct = r['used_pct']
        txt = f"{WNAMES.get(r['window'], r['window'])}: "
        if pct is not None:
            txt += f"已用 {pct:.1f}% · 剩 {100-pct:.0f}%"
            if r.get('total'):
                txt += f"（{r['used']:.0f}/{r['total']:.0f}）"
        elif r.get('used') and r['window'] == 'billing':
            txt += f"近30天 {r['used']/1e6:.0f}M tokens"
        else:
            txt += '—'
        print(txt + ' | size=12')
if errors:
    print('---')
    for site, err in errors.items():
        print(f"{NAMES.get(site, site)} 抓取失败: {err[:60]} | color=red size=11")
print('---')
print('刷新 | refresh=true')
print(f'打开仪表盘 | bash=open param0="{ROOT}/data/dashboard.html" terminal=false')
print(f'重新登录（浏览器）| bash=/bin/zsh param0=-c param1="cd {ROOT} && NODE_PATH=/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules /Users/hanamaki_mac_mini/.workbuddy/binaries/node/versions/22.22.2-2/bin/node scripts/login.js" terminal=true')
EOF
