#!/bin/bash
# <bitbar.title>LLM 用量</bitbar.title>
# <bitbar.version>v2.0</bitbar.version>
# <bitbar.author>kevin_w</bitbar.author>
# <bitbar.desc>状态栏展示 GLM / Kimi / Kimi Code / MiniMax 剩余额度（图片进度条）</bitbar.desc>
# <bitbar.dependencies>python3</bitbar.dependencies>
#
# SwiftBar 插件：每 10 分钟自动执行一次（文件名 llm-usage.10m.sh）

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/Users/hanamaki_mac_mini/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

JSON=$("$PY" "$ROOT/fetch.py" --json 2>/dev/null)
[ -z "$JSON" ] && JSON=$(cat "$ROOT/data/latest.json" 2>/dev/null)

"$PY" - "$JSON" "$ROOT" <<'PYEOF'
import json, sys, struct, zlib, base64
from datetime import datetime

# ============= 颜色表（莫兰迪低饱和） =============
SITE_COLORS = {
    'bigmodel':  {'fg': (0x7d, 0x96, 0xb5), 'fg_dark': (0x5d, 0x75, 0x92)},
    'kimi':      {'fg': (0xc8, 0x9a, 0x9a), 'fg_dark': (0xa8, 0x73, 0x73)},
    'kimi-code': {'fg': (0xa5, 0x94, 0xb8), 'fg_dark': (0x80, 0x6f, 0x96)},
    'minimax':   {'fg': (0x8f, 0xb0, 0x8f), 'fg_dark': (0x6a, 0x8a, 0x6a)},
}
NAMES = {'bigmodel': 'GLM', 'kimi': 'Kimi', 'kimi-code': 'KimiCode', 'minimax': 'MM'}
FULL  = {'bigmodel': '智谱 GLM', 'kimi': 'Kimi（订阅总额）',
         'kimi-code': 'Kimi Code', 'minimax': 'MiniMax'}
WNAMES = {'5h': '5小时', 'week': '周', 'billing': '账期'}
TITLE_WINDOW = {'bigmodel': '5h', 'kimi': 'billing', 'kimi-code': '5h', 'minimax': '5h'}
ROOT = sys.argv[2]

# ============= 零依赖 PNG 编码器（RGBA） =============
def png_bytes(width, height, pixels):
    """pixels: bytes of length W*H*4, RGBA, top-down."""
    def chunk(typ, data):
        out = struct.pack('>I', len(data)) + typ + data
        crc = zlib.crc32(typ + data) & 0xffffffff
        return out + struct.pack('>I', crc)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)  # 8bit RGBA
    # 每行加 1 字节 filter type 0（None）
    raw = b''.join(b'\x00' + pixels[y*width*4:(y+1)*width*4] for y in range(height))
    idat = zlib.compress(raw, 9)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')

def b64(data):
    return base64.b64encode(data).decode()

def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def health_color(pct, site=None):
    """<70 绿、<85 琥珀、<95 橙红、>=95 红（不传 site 时）；传了 site 则按站点主色为基色。"""
    site_colors = {
        'bigmodel':  (0x7d, 0x96, 0xb5),  # 蓝灰
        'kimi':      (0xc8, 0x9a, 0x9a),  # 粉
        'kimi-code': (0xa5, 0x94, 0xb8),  # 紫
        'minimax':   (0x8f, 0xb0, 0x8f),  # 绿
    }
    base = site_colors.get(site, (0x70, 0x70, 0x70))
    if pct is None:
        return lerp(base, (255, 255, 255), 0.5)
    if pct >= 95:   danger = (0xc4, 0x66, 0x5c)  # 红
    elif pct >= 85: danger = (0xd9, 0x93, 0x6e)  # 橙红
    elif pct >= 70: danger = (0xd4, 0xa8, 0x6a)  # 琥珀
    else:           danger = base                  # 健康：站点本色
    return danger

# ============= 画进度条 PNG =============
def draw_bar(pct, width, height=16, site=None, radius=0):
    """单个进度条 PNG（直角，无锯齿），按站点着色 + 用量健康度微调。"""
    bg = (0xec, 0xe8, 0xe1)
    if pct is None:
        pct_disp = 0
    else:
        pct_disp = max(0, min(100, pct))
    fill_w = round(width * pct_disp / 100)
    fill_color = health_color(pct, site=site)
    fill_hi = lerp(fill_color, (255, 255, 255), 0.20)
    fill_lo = lerp(fill_color, (0, 0, 0), 0.15)
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            # 直角（radius=0），全部实心像素
            if x < fill_w:
                t = y / max(1, height - 1)
                c = lerp(fill_hi, fill_lo, t)
                pixels[i:i+4] = bytes((c[0], c[1], c[2], 255))
            else:
                pixels[i:i+4] = bytes((bg[0], bg[1], bg[2], 255))
    return png_bytes(width, height, bytes(pixels))

def draw_strip(items, total_w, h=14, gap=4):
    """items: list of (pct, site). 多站点拼接成一行。"""
    n = len(items)
    bar_w = (total_w - gap * (n - 1)) // n
    bg = (0xec, 0xe8, 0xe1)
    pixels = bytearray(total_w * h * 4)
    for idx, (pct, site) in enumerate(items):
        x0 = idx * (bar_w + gap)
        if pct is None:
            pct_disp = 0
        else:
            pct_disp = max(0, min(100, pct))
        fill_w = round(bar_w * pct_disp / 100)
        fill_color = health_color(pct, site=site)
        fill_hi = lerp(fill_color, (255, 255, 255), 0.20)
        fill_lo = lerp(fill_color, (0, 0, 0), 0.15)
        for y in range(h):
            for x in range(bar_w):
                i = (y * total_w + x0 + x) * 4
                if x < fill_w:
                    t = y / max(1, h - 1)
                    c = lerp(fill_hi, fill_lo, t)
                    pixels[i:i+4] = bytes((c[0], c[1], c[2], 255))
                else:
                    pixels[i:i+4] = bytes((bg[0], bg[1], bg[2], 255))
    return png_bytes(total_w, h, bytes(pixels))

# ============= 数据加载 =============
try:
    data = json.loads(sys.argv[1])
except Exception:
    print('LLM ⚠️')
    print('---')
    print('数据不可用 | color=red')
    sys.exit(0)

rows = data.get('rows', [])
errors = data.get('errors', {})

def pct_of(site, window):
    for r in rows:
        if r['site'] == site and r['window'] == window:
            return r.get('used_pct')
    return None

def remain(site, window):
    p = pct_of(site, window)
    return (100 - p) if p is not None else None

# ============= 菜单栏图标（header） =============
# 4 站点一行小条，按站点主色 + 健康度调色
items = [(pct_of(site, TITLE_WINDOW[site]), site) for site in ('bigmodel', 'kimi', 'kimi-code', 'minimax')]
ICON_W = 200
ICON_H = 12
icon_png = draw_strip(items, ICON_W, h=ICON_H, gap=4)

# 菜单栏标题：「图 + 文字百分比」
parts = []
for site in ('bigmodel', 'kimi', 'kimi-code', 'minimax'):
    p = pct_of(site, TITLE_WINDOW[site])
    if p is not None:
        parts.append(NAMES[site] + format(100 - p, '.0f') + '%')
    else:
        parts.append(NAMES[site] + '?')
title_suffix = ' ⚠️' if errors else ''
# header 第一行：title 是个空格占位，| image=<base64> 是真实图片
print(' ' + ' | image=' + b64(icon_png) + ' size=14')
# header 第二行（轮播）：文字百分比
print(' '.join(parts) + title_suffix + ' | size=13 dropdown=false')

# ============= 下拉菜单 =============
print('---')
ts = data.get('ts', '')
if ts:
    local = datetime.fromisoformat(ts).astimezone().strftime('%H:%M:%S')
    print('更新于 ' + local + ' | color=gray')

# 每站点独立板块，每行一条带图片进度条
for site in ('bigmodel', 'kimi', 'kimi-code', 'minimax'):
    site_rows = [r for r in rows if r['site'] == site]
    if not site_rows:
        continue
    print(FULL[site] + ' | size=13 color=gray')
    for r in site_rows:
        pct = r['used_pct']
        label = WNAMES.get(r['window'], r['window'])
        if pct is not None:
            # 240x16 大进度条图（菜单中放大显示更清晰），按站点着色
            bar_png = draw_bar(pct, 240, height=16, site=site, radius=0)
            detail = ''
            if r.get('total'):
                detail = '  ' + format(r['used'], '.0f') + '/' + format(r['total'], '.0f')
            # 图片行：title 占位 + | image=<base64>
            print(' ' + ' | image=' + b64(bar_png))
            print('   ' + format(pct, '5.1f') + '%  ' + label + detail + ' | size=12 color=#d6d3cc')
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
