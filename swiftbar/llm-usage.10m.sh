#!/bin/bash
# <bitbar.title>LLM 用量</bitbar.title>
# <bitbar.version>v1.2</bitbar.version>
# <bitbar.author>kevin_w</bitbar.author>
# <bitbar.desc>状态栏展示 GLM / Kimi / Kimi Code / MiniMax 剩余额度</bitbar.desc>
# <bitbar.dependencies>python3</bitbar.dependencies>
#
# SwiftBar 插件：每 10 分钟自动执行一次（文件名 llm-usage.10m.sh）

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/Users/hanamaki_mac_mini/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

JSON=$("$PY" "$ROOT/fetch.py" --json 2>/dev/null)
[ -z "$JSON" ] && JSON=$(cat "$ROOT/data/latest.json" 2>/dev/null)

"$PY" - "$JSON" "$ROOT" <<'PYEOF'
import json, sys
from datetime import datetime

NAMES = {'bigmodel': 'GLM', 'kimi': 'Kimi', 'kimi-code': 'KimiCode', 'minimax': 'MM'}
FULL  = {'bigmodel': '智谱 GLM', 'kimi': 'Kimi（订阅总额）',
         'kimi-code': 'Kimi Code', 'minimax': 'MiniMax'}
WNAMES = {'5h': '5小时', 'week': '周', 'billing': '账期'}
TITLE_WINDOW = {'bigmodel': '5h', 'kimi': 'billing', 'kimi-code': '5h', 'minimax': '5h'}
ROOT = sys.argv[2]

# 把 #rrggbb 转成 ANSI 24-bit truecolor (前景色)
def ansi_fg(hex_color):
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return '\x1b[38;2;' + str(r) + ';' + str(g) + ';' + str(b) + 'm'

RESET = '\x1b[0m'
DIM   = '\x1b[2m'

# 渐变色带：绿 → 琥珀 → 橙红 → 红
BAR_HEALTH = [
    (70,  '#7fae8a'),
    (85,  '#d4a86a'),
    (95,  '#d9936e'),
    (999, '#c4665c'),
]

def bar_color(pct):
    if pct is None:
        return '#6e6c66'
    for limit, c in BAR_HEALTH:
        if pct < limit:
            return c
    return BAR_HEALTH[-1][1]

def bar_segments(pct, width=14):
    """返回 (used_seg, rest_seg) 两段，SwiftBar 会显示成两行相邻。"""
    if pct is None:
        return ('▱' * width, '')
    pct = max(0, min(100, pct))
    half_filled = round(pct / 100 * (width * 2))
    full = half_filled // 2
    half = half_filled % 2
    used = '▰' * full + ('▰' if half else '')
    rest = '▱' * (width - len(used))
    return (used, rest)

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
    if p is not None:
        parts.append(NAMES[site] + ' ' + format(p, '.0f') + '%')
    else:
        parts.append(NAMES[site] + ' ?')
suffix = ' ⚠️' if errors else ''
print(' '.join(parts) + suffix)

print('---')
ts = data.get('ts', '')
if ts:
    local = datetime.fromisoformat(ts).astimezone().strftime('%H:%M:%S')
    print('更新于 ' + local + ' | color=gray')

for site in ('bigmodel', 'kimi', 'kimi-code', 'minimax'):
    site_rows = [r for r in rows if r['site'] == site]
    if not site_rows:
        continue
    print(FULL[site] + ' | size=13 color=gray')
    for r in site_rows:
        pct = r['used_pct']
        label = WNAMES.get(r['window'], r['window'])
        if pct is not None:
            used, rest = bar_segments(pct, width=14)
            color = bar_color(pct)
            # 单行：已用段用真彩色 + 剩余段用暗灰，整体后接百分比与标签
            line = ansi_fg(color) + used + RESET + ansi_fg('#6e6c66') + rest + RESET
            extra = '   ' + format(pct, '5.1f') + '%  '
            if r.get('total'):
                extra += format(r['used'], '.0f') + '/' + format(r['total'], '.0f') + '  '
            extra += label
            print(line + ' ' + extra + ' | font=Menlo size=14')
        elif r.get('used') and r['window'] == 'billing':
            buf = label + ': 近30天 ' + format(r['used']/1e6, '.0f') + 'M tokens'
            print(buf + ' | font=Menlo size=12 color=#8a8680')

if errors:
    print('---')
    for site, err in errors.items():
        name = NAMES.get(site, site)
        print(name + ' 抓取失败: ' + err[:60] + ' | color=red size=11')

print('---')
print('刷新 | refresh=true')
print('打开仪表盘 | bash=/usr/bin/open param0=' + ROOT + '/data/dashboard.html terminal=false')
NODE='/Users/hanamaki_mac_mini/.workbuddy/binaries/node/versions/22.22.2-2/bin/node'
NODEMOD='/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules'
print('重新登录（浏览器）| bash=/bin/zsh param0=-c param1="cd ' + ROOT + ' && NODE_PATH=' + NODEMOD + ' ' + NODE + ' scripts/login.js" terminal=true')
PYEOF
