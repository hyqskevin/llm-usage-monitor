#!/bin/bash
# <bitbar.title>LLM 用量</bitbar.title>
# <bitbar.version>v2.0</bitbar.version>
# <bitbar.author>kevin_w</bitbar.author>
# <bitbar.desc>状态栏展示 GLM / Kimi / Kimi Code / MiniMax / DeepSeek / MiMo 剩余额度（图片进度条）</bitbar.desc>
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
    'deepseek':  {'fg': (0x6b, 0x9a, 0xa8), 'fg_dark': (0x4d, 0x7a, 0x87)},
    'mimo':      {'fg': (0xc9, 0xa8, 0x8a), 'fg_dark': (0xa8, 0x86, 0x68)},
}
NAMES = {'bigmodel': 'GLM', 'kimi': 'Kimi', 'kimi-code': 'KimiCode', 'minimax': 'MM', 'deepseek': 'DS', 'mimo': 'MiMo'}
FULL  = {'bigmodel': '智谱 GLM', 'kimi': 'Kimi（订阅总额）',
         'kimi-code': 'Kimi Code', 'minimax': 'MiniMax', 'deepseek': 'DeepSeek', 'mimo': 'MiMo（套餐）'}
WNAMES = {'5h': '5小时', 'week': '周', 'billing': '账期', 'today': '当日'}
TITLE_WINDOW = {'bigmodel': '5h', 'kimi': 'billing', 'kimi-code': '5h', 'minimax': '5h', 'deepseek': 'today', 'mimo': 'billing'}
ROOT = sys.argv[2]

# 菜单栏头部 emoji 进度条字符
SITE_EMOJI = {
    'bigmodel':  ('🟦', '⬜'),   # 蓝灰
    'kimi':      ('🟥', '⬜'),   # 粉
    'kimi-code': ('🟪', '⬜'),   # 紫
    'minimax':   ('🟩', '⬜'),   # 绿
    'deepseek':  ('🟦', '⬜'),   # 蓝青
    'mimo':      ('🟧', '⬜'),   # 暖沙橙
}
def format_emoji_bar(pct):
    if pct is None:
        return '⬜⬜⬜'
    filled = round(max(0, min(100, pct)) / 100 * 4)
    return '▰' * filled + '▱' * (4 - filled)
def color_swatch(site):
    full, empty = SITE_EMOJI.get(site, ('⬛', '⬜'))
    return full

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
        'deepseek':  (0x6b, 0x9a, 0xa8),  # 蓝青
        'mimo':      (0xc9, 0xa8, 0x8a),  # 暖沙橙
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
    """items: list of (pct, site). 多站点拼接成一行。

    2026-11-10 起无调用方：菜单栏彩色进度条图标因 SwiftBar 2.x 的 header
    image= 渲染不稳定而移除（见提交 c1fe950）。保留此函数以备将来
    换用其它渲染方式时复用，避免重复实现 PNG 拼接逻辑。
    """
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

# 各 provider 的官方管理台地址（点击板块标题跳转）
CONSOLE = {
    'bigmodel':  'https://bigmodel.cn/coding-plan/personal/usage',
    'kimi':      'https://www.kimi.com/membership/subscription?tab=quota',
    'kimi-code': 'https://www.kimi.com/membership/subscription?tab=quota',
    'minimax':   'https://platform.minimax.cn/console/usage',
    'mimo':      'https://platform.xiaomimimo.com/console/plan-manage?edition=PERSONAL',
    'deepseek':  'https://platform.deepseek.com/usage',
}

def href(site):
    """生成 SwiftBar href 参数：点击该项用默认浏览器打开该 provider 的管理台。"""
    u = CONSOLE.get(site, '')
    return (' href=' + u) if u else ''

def pct_of(site, window):
    for r in rows:
        if r['site'] == site and r['window'] == window:
            return r.get('used_pct')
    return None

def remain(site, window):
    p = pct_of(site, window)
    return (100 - p) if p is not None else None

def deepseek_balance():
    """DeepSeek 账户余额（CNY）—— 用于头部菜单栏直显。"""
    for r in rows:
        if r['site'] == 'deepseek' and r.get('window') == 'billing':
            rem = r.get('remaining')
            if rem is not None:
                return rem
    return None

def header_pct(site):
    if site == 'deepseek':
        # DeepSeek 不走百分比，菜单栏直显账户余额（由 header_extra 处理）
        return None
    return pct_of(site, TITLE_WINDOW[site])

def header_extra(site):
    """菜单栏标题里站点名后的额外文字（DeepSeek 特殊：¥余额）。"""
    if site == 'deepseek':
        b = deepseek_balance()
        return '¥' + format(b, '.2f') if b is not None else '?'
    return None

# ============= 菜单栏标题（header） =============
# 2026-11-10：菜单栏彩色进度条图标已移除。
#   原因：SwiftBar 2.x 在 header 位置对 `image=` 参数渲染不稳定（见提交 c1fe950），
#   原先生成的 icon_png 从未被使用，属死代码，纯属每轮白算一遍 PNG。
#   现只保留纯文字百分比；下拉菜单内的 PNG 进度条不受影响（那里 image= 稳定）。
#
# 2026-11-10 修：失败站点在标题里加 ❌ 标记，避免 GLM 这类单站失败
#   被其他站点的正常数字淹没（此前 bigmodel 连续失败 5 天都无人察觉）。
parts = []
failed_sites = set(errors.keys())
for site in ('bigmodel', 'kimi', 'kimi-code', 'minimax', 'mimo', 'deepseek'):
    extra = header_extra(site)
    label = NAMES[site]
    # kimi-code 的采集错误记在 'kimi' 站点上，这里做映射
    is_failed = site in failed_sites or (site == 'kimi-code' and 'kimi' in failed_sites)
    mark = '❌' if is_failed else ''
    if extra is not None:
        parts.append(label + extra + mark)
    else:
        p = header_pct(site)
        if p is not None:
            parts.append(label + format(100 - p, '.0f') + '%' + mark)
        else:
            parts.append(label + '?' + mark)
title_suffix = ' ⚠️' if errors else ''
# header 第一行：纯数值（GLM 100% Kimi 78% KimiCode 45% MM 68%）
print(' '.join(parts) + title_suffix + ' | size=12')

# ============= 下拉菜单 =============
print('---')
ts = data.get('ts', '')
if ts:
    local = datetime.fromisoformat(ts).astimezone().strftime('%H:%M:%S')
    print('更新于 ' + local + ' | color=gray')

# Kimi 合并视图：把 kimi/billing + kimi-code/billing 合并画到「Kimi」组顶部
def pct_of_rows(rs, window):
    for r in rs:
        if r['window'] == window and r['used_pct'] is not None:
            return r
    return None

kimi_rows = [r for r in rows if r['site'] == 'kimi']
kimi_code_rows = [r for r in rows if r['site'] == 'kimi-code']
bigmodel_rows = [r for r in rows if r['site'] == 'bigmodel']
minimax_rows = [r for r in rows if r['site'] == 'minimax']

def draw_segmented_bar(kimi_pct, code_pct, width=240, height=16):
    """Kimi 总额进度条（按页面截图样式）：
       已用部分按比例分成 Kimi(粉) + Code(蓝)，剩余 bg。
       amountUsedRatio=已用总量；kimiCodeUsedRatio=Code 在已用里占的比例（不是占总比例）。
    """
    bg = (0xec, 0xe8, 0xe1)
    pixels = bytearray(width * height * 4)
    kimi_pct = kimi_pct or 0
    code_pct = code_pct or 0
    total_used_w = round(width * min(100, kimi_pct) / 100)
    # Code 在已用部分中的占比 = code_pct / kimi_pct
    code_share = (code_pct / kimi_pct) if kimi_pct > 0 else 0
    code_w = round(total_used_w * code_share)
    code_w = min(code_w, total_used_w)
    kimi_seg = total_used_w - code_w
    code_color = (0x70, 0xa8, 0xe8)  # Code 蓝
    kimi_color = (0xc8, 0x9a, 0x9a)  # Kimi 粉
    code_hi = lerp(code_color, (255, 255, 255), 0.18)
    code_lo = lerp(code_color, (0, 0, 0), 0.15)
    kimi_hi = lerp(kimi_color, (255, 255, 255), 0.18)
    kimi_lo = lerp(kimi_color, (0, 0, 0), 0.15)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            if x < kimi_seg:
                t = y / max(1, height - 1)
                c = lerp(kimi_hi, kimi_lo, t)
                pixels[i:i+4] = bytes((c[0], c[1], c[2], 255))
            elif x < kimi_seg + code_w:
                t = y / max(1, height - 1)
                c = lerp(code_hi, code_lo, t)
                pixels[i:i+4] = bytes((c[0], c[1], c[2], 255))
            else:
                pixels[i:i+4] = bytes((bg[0], bg[1], bg[2], 255))
    return png_bytes(width, height, bytes(pixels))

def fmt_reset_inline(iso):
    if not iso: return '—'
    try:
        d = datetime.fromisoformat(iso)
        return d.strftime('%Y-%m-%d') + ' 后重置'
    except Exception:
        return '—'

def fmtBig(n):
    """K / M / B 自适应缩写"""
    if n is None: return '—'
    if n >= 1e9: return ('%.1f' % (n/1e9)) + 'B'
    if n >= 1e6: return ('%.1f' % (n/1e6)) + 'M'
    if n >= 1e3: return ('%.1f' % (n/1e3)) + 'K'
    return str(int(round(n)))

# 1) 智谱板块
# 2026-11-10 修：采集失败时补上分隔线与「数据不可用」提示。
#   原实现在失败分支只打了板块标题、漏了 '---'，导致 GLM 板块与上方
#   Kimi 板块黏在一起；同时没有任何失败提示，与其它站点板块表现不一致。
if bigmodel_rows:
    print('---')
    print(FULL['bigmodel'] + ' | size=13 color=gray' + href('bigmodel'))
    for r in bigmodel_rows:
        pct = r['used_pct']
        label = WNAMES.get(r['window'], r['window'])
        if pct is not None:
            bar_png = draw_bar(pct, 240, height=16, site='bigmodel', radius=0)
            detail = ''
            if r.get('total'):
                detail = '  ' + format(r['used'], '.0f') + '/' + format(r['total'], '.0f')
            print(' ' + ' | image=' + b64(bar_png))
            print('   ' + format(pct, '5.1f') + '%  ' + label + detail + ' | size=12 color=#d6d3cc')
else:
    print('---')
    print(FULL['bigmodel'] + ' | size=13 color=gray' + href('bigmodel'))
    err = errors.get('bigmodel', '')
    if err:
        print('   抓取失败: ' + err[:60] + ' | color=red size=11')
        print('   需人工重登 → 菜单「重新登录（浏览器）」 | size=11 color=#8a8680')
    else:
        print('   数据不可用 | size=12 color=#8a8680')

# 2) Kimi 合并板块：顶部 = 总用量（Kimi + Code 分段进度条），底部 = 各窗口细分
if kimi_rows or kimi_code_rows:
    print('---')
    print('Kimi（订阅总额） | size=14 color=#a87373' + href('kimi'))
    kimi_billing = pct_of_rows(kimi_rows, 'billing')
    code_billing = pct_of_rows(kimi_code_rows, 'billing')
    if kimi_billing and code_billing:
        total_pct = kimi_billing['used_pct']
        code_pct = code_billing['used_pct']
        seg_png = draw_segmented_bar(total_pct, code_pct, width=240, height=18)
        print(' ' + ' | image=' + b64(seg_png))
        print('   总使用量  ' + format(total_pct, '.1f') + '%     ' + fmt_reset_inline(kimi_billing.get('reset_at')) + ' | size=13')
        # 图例
        print('   🟥 Kimi   🟦 Code' + ' | size=11 color=#8a8680')
        # 细分行
        kimi_only = max(0, total_pct - code_pct)
        print('   Kimi  ' + format(kimi_only, '.1f') + '%   |   Code  ' + format(code_pct, '.1f') + '%' + ' | size=11 color=#a8a4a0')
else:
    print('---')
    print('Kimi（订阅总额） | size=14 color=#a87373' + href('kimi'))
    print('   数据不可用 | size=12 color=#8a8680')

# 3) Kimi Code 限流细分（5h/周）
if kimi_code_rows:
    for window_key, window_name in (('5h', 'Code 5小时'), ('week', 'Code 周限额')):
        r = pct_of_rows(kimi_code_rows, window_key)
        if r is None: continue
        seg_png = draw_segmented_bar(r['used_pct'], r['used_pct'], width=240, height=16)
        print(' ' + ' | image=' + b64(seg_png))
        print('   ' + window_name + ': ' + format(r['used_pct'], '.1f') + '%' + ' | size=12 color=#d6d3cc')

# 4) MiniMax
if minimax_rows:
    print('---')
    print(FULL['minimax'] + ' | size=13 color=gray' + href('minimax'))
    for r in minimax_rows:
        pct = r['used_pct']
        label = WNAMES.get(r['window'], r['window'])
        if pct is not None:
            if r.get('unlimited'):
                bar_png = draw_bar(pct, 240, height=16, site='minimax', radius=0)
                print(' ' + ' | image=' + b64(bar_png))
                print('   ' + format(pct, '5.1f') + '%  ' + label + '  ∞ 无限制' + ' | size=12 color=#d6d3cc')
            else:
                bar_png = draw_bar(pct, 240, height=16, site='minimax', radius=0)
                detail = ''
                if r.get('total'):
                    detail = '  ' + format(r['used'], '.0f') + '/' + format(r['total'], '.0f')
                print(' ' + ' | image=' + b64(bar_png))
                print('   ' + format(pct, '5.1f') + '%  ' + label + detail + ' | size=12 color=#d6d3cc')
        elif r.get('used') and r['window'] == 'billing':
            buf = label + ': 近30天 ' + format(r['used']/1e6, '.0f') + 'M tokens'
            print(buf + ' | font=Menlo size=12 color=#8a8680')
else:
    # 采集失败时保留板块标题与控制台入口，避免该站点在菜单里彻底消失
    print('---')
    print(FULL['minimax'] + ' | size=13 color=gray' + href('minimax'))
    print('   数据不可用 | size=12 color=#8a8680')

# 5) MiMo（套餐 Credits：账期进度条 + 当日 tokens/请求）
mimo_rows = [r for r in rows if r['site'] == 'mimo']
if mimo_rows:
    print('---')
    print(FULL['mimo'] + ' | size=13 color=gray' + href('mimo'))
    m_billing = next((r for r in mimo_rows if r['window'] == 'billing'), None)
    m_today = next((r for r in mimo_rows if r['window'] == 'today'), None)
    if m_billing and m_billing.get('used_pct') is not None:
        bar_png = draw_bar(m_billing['used_pct'], 240, height=16, site='mimo', radius=0)
        print(' ' + ' | image=' + b64(bar_png))
        detail = ''
        if m_billing.get('total'):
            detail = '  ' + fmtBig(m_billing['used']) + '/' + fmtBig(m_billing['total']) + ' credits'
        print('   ' + format(m_billing['used_pct'], '5.1f') + '%  账期' + detail + ' | size=12 color=#d6d3cc')
        if m_billing.get('reset_at'):
            print('   ' + fmt_reset_inline(m_billing['reset_at']) + ' | size=11 color=#8a8680')
    if m_today:
        m_meta = m_today.get('meta') or {}
        tok = m_meta.get('today_tokens')
        reqn = m_meta.get('today_requests')
        line = '   当日 ' + (fmtBig(tok) if tok is not None else '—') + ' tokens'
        if reqn is not None:
            line += '   ·   ' + str(reqn) + ' 次请求'
        print(line + ' | size=12 color=#3f3e3b')
else:
    print('---')
    print(FULL['mimo'] + ' | size=13 color=gray' + href('mimo'))
    print('   数据不可用 | size=12 color=#8a8680')

# 6) DeepSeek（金额型：余额 / 当日消费 / 当日 Token，无进度条）
deepseek_rows_all = [r for r in rows if r['site'] == 'deepseek']
if deepseek_rows_all:
    print('---')
    print(FULL['deepseek'] + ' | size=13 color=gray' + href('deepseek'))
    billing = next((r for r in deepseek_rows_all if r['window'] == 'billing'), None)
    today = next((r for r in deepseek_rows_all if r['window'] == 'today'), None)
    today_meta = (today or {}).get('meta') or {}
    today_tokens = today_meta.get('today_tokens')

    def ds_yuan(n):
        return '¥' + format(n, '.2f') if n is not None else '—'

    # 余额行（直接展示账户余额，无预算逻辑）
    if billing:
        print('   账户余额  ' + ds_yuan(billing.get('remaining'))
              + '   ·   累计消费 ' + ds_yuan(billing.get('used'))
              + ' / 充值 ' + ds_yuan(billing.get('total'))
              + ' | size=12 color=#3f3e3b')
    # 当日消费
    if today:
        print('   当日消费  ' + ds_yuan(today.get('used'))
              + ('   ·   当日 token ' + (fmtBig(today_tokens) if today_tokens is not None else '—') if today else '')
              + ' | size=12 color=#3f3e3b')
else:
    print('---')
    print(FULL['deepseek'] + ' | size=13 color=gray' + href('deepseek'))
    print('   数据不可用 | size=12 color=#8a8680')

# 2026-11-10：GLM 板块内已单独显示失败详情与处置指引，
#   此处不再重复列出 bigmodel，避免同一错误在菜单里出现两遍。
if errors:
    print('---')
    other_errors = {k: v for k, v in errors.items() if k != 'bigmodel'}
    if other_errors:
        for site, err in other_errors.items():
            name = NAMES.get(site, site)
            print(name + ' 抓取失败: ' + err[:60] + ' | color=red size=11')
    elif 'bigmodel' in errors:
        print('部分站点抓取失败，详见上方各站点板块 | color=red size=11')

print('---')
print('刷新 | refresh=true')
print('打开仪表盘 | bash=' + ROOT + '/scripts/open_dashboard.sh terminal=false')
NODE='/opt/homebrew/bin/node'
NODEMOD='/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules'
# 重新登录：一次性打开全部站点登录页，登录后关闭浏览器即自动保存登录态到 browser-profile/
print('重新登录（浏览器）| bash=/bin/zsh param0=-c param1="cd ' + ROOT + ' && NODE_PATH=' + NODEMOD + ' ' + NODE + ' scripts/login.js" terminal=true')
PYEOF
