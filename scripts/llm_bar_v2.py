#!/usr/bin/env python3
"""SwiftBar 插件 v2 的渲染引擎（streamable，支持切换动效）。

由 swiftbar/llm-usage-v2.10m.sh 调用。

为什么单独成文件：插件本体要用 #streamable 标记并常驻，
Python 侧负责：
  1. 采集/读取用量数据（复用 fetch.py 的输出）
  2. 渲染状态栏 logo + 迷你进度条
  3. 渲染下拉菜单（顶部 provider logo 切换区 + 当前模型详情 + 底部操作区）
  4. **切换动效**：检测到 provider 变化时逐帧输出动画帧，用 ~~~ 分帧

动效设计（缓动函数驱动的 12 帧，约 0.5 秒）：
  - logo 缩放：overshoot 回弹（1.0 → 1.35 → 0.9 → 1.08 → 1.0）
  - 进度条：从 0 填充到目标值
  - 新旧 provider 之间做交叉淡入淡出，避免突兀
"""
import base64
import json
import os
import struct
import subprocess
import sys
import time
import zlib
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
LOGO_DIR = os.path.join(ROOT, 'assets', 'logos')
SWITCH = os.path.join(ROOT, 'scripts', 'switch_model.sh')
STATE = os.path.join(DATA, 'selected.json')
LAST_RENDERED = os.path.join(DATA, '.last_rendered.json')
FETCH_PY = os.path.join(ROOT, 'fetch.py')
PY_BIN = sys.executable

# 空闲期轮询：每 0.5 秒检查一次选择变化（让点击切换的动效立刻出现），
# 每 IDLE_TICKS 次（≈60 秒）刷新一次数据。
IDLE_POLL_INTERVAL = 0.5
IDLE_TICKS = 120

# ===================== 模型定义 =====================
MODELS = [
    {'key': 'bigmodel', 'name': '智谱 GLM', 'logo': 'bigmodel.png', 'color': (0x7d, 0x96, 0xb5)},
    {'key': 'kimi',     'name': 'Kimi',     'logo': 'kimi.png',     'color': (0xc8, 0x9a, 0x9a)},
    {'key': 'minimax',  'name': 'MiniMax',  'logo': 'minimax.png',  'color': (0x8f, 0xb0, 0x8f)},
    {'key': 'deepseek', 'name': 'DeepSeek', 'logo': 'deepseek.png', 'color': (0x6b, 0x9a, 0xa8)},
    {'key': 'mimo',     'name': 'MiMo',     'logo': 'mimo.png',     'color': (0xc9, 0xa8, 0x8a)},
]
MODEL_BY_KEY = {m['key']: m for m in MODELS}
MODEL_ORDER = [m['key'] for m in MODELS]

CONSOLE = {
    'bigmodel': 'https://bigmodel.cn/coding-plan/personal/usage',
    'kimi':     'https://www.kimi.com/membership/subscription?tab=quota',
    'minimax':  'https://platform.minimax.cn/console/usage',
    'deepseek': 'https://platform.deepseek.com/usage',
    'mimo':     'https://platform.xiaomimimo.com/console/plan-manage?edition=PERSONAL',
}

WINDOW_NAMES = {'5h': '5小时', 'week': '周限额', 'billing': '账期', 'today': '当日'}

# ===================== 基础工具 =====================
def hexc(rgb):
    return '%02x%02x%02x' % rgb


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def health_color(pct, base):
    """已用百分比 → 颜色：低=站点本色，中=琥珀，高=橙红，满=红。"""
    if pct is None:
        return tuple(int(c + (255 - c) * 0.5) for c in base)
    if pct >= 95:
        return (0xc4, 0x66, 0x5c)
    if pct >= 85:
        return (0xd9, 0x93, 0x6e)
    if pct >= 70:
        return (0xd4, 0xa8, 0x6a)
    return base


def png_bytes(width, height, pixels):
    """pixels: bytes, W*H*4, RGBA, 自上而下。"""
    def chunk(typ, data):
        out = struct.pack('>I', len(data)) + typ + data
        crc = zlib.crc32(typ + data) & 0xffffffff
        return out + struct.pack('>I', crc)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    raw = b''.join(b'\x00' + pixels[y * width * 4:(y + 1) * width * 4]
                   for y in range(height))
    idat = zlib.compress(raw, 9)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


def b64(data):
    return base64.b64encode(data).decode()


_LOGO_CACHE = {}


def load_logo(name, size):
    """读 logo 缩放到 size，返回 (bytes, size)。失败返回 (None, size)。"""
    ck = (name, size)
    if ck in _LOGO_CACHE:
        return _LOGO_CACHE[ck]
    try:
        from PIL import Image
        p = os.path.join(LOGO_DIR, name)
        if not os.path.exists(p):
            res = (None, size)
        else:
            im = Image.open(p).convert('RGBA').resize((size, size), Image.LANCZOS)
            res = (im.tobytes(), size)
    except Exception:
        res = (None, size)
    _LOGO_CACHE[ck] = res
    return res


def blit(pixels, total_w, dst_x, dst_y, buf, src_w, src_x=0, src_y=0,
         tint=None, alpha=1.0):
    """把 logo 的一像素混合到画布。

    源坐标 (src_x, src_y) 读自 buf；目标坐标 (dst_x, dst_y) 写入画布。
    两者分开，避免把带 padding 的目标坐标误当源坐标导致越界。
    """
    stride = src_w * 4
    si = src_y * stride + src_x * 4
    r, g, b, a = buf[si], buf[si + 1], buf[si + 2], buf[si + 3]
    if a == 0:
        return
    if tint is not None:
        r, g, b = tint
    a = int(a * alpha)
    if a == 0:
        return
    di = (dst_y * total_w + dst_x) * 4
    if a < 255:
        br, bg, bb = pixels[di], pixels[di + 1], pixels[di + 2]
        r = int(r * a / 255 + br * (255 - a) / 255)
        g = int(g * a / 255 + bg * (255 - a) / 255)
        b = int(b * a / 255 + bb * (255 - a) / 255)
    pixels[di:di + 4] = bytes((r, g, b, 255))


# ===================== 图形绘制 =====================
def draw_bar(pct, width, height=16, base=None):
    """下拉菜单里的额度进度条。"""
    bg = (0xec, 0xe8, 0xe1)
    pct_disp = 0 if pct is None else max(0, min(100, pct))
    fill_w = round(width * pct_disp / 100)
    fill = health_color(pct, base)
    fill_hi = lerp(fill, (255, 255, 255), 0.20)
    fill_lo = lerp(fill, (0, 0, 0), 0.15)
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            if x < fill_w:
                t = y / max(1, height - 1)
                c = lerp(fill_hi, fill_lo, t)
                pixels[i:i + 4] = bytes((c[0], c[1], c[2], 255))
            else:
                pixels[i:i + 4] = bytes((bg[0], bg[1], bg[2], 255))
    return png_bytes(width, height, bytes(pixels))


def draw_single_logo(size=28, model=None, selected=False):
    """下拉菜单里单个 provider 的 logo（带选中描边）。"""
    model = model or MODELS[0]
    pad = 3
    total = size + pad * 2
    pixels = bytearray(total * total * 4)
    if selected:
        hl = (0xd4, 0xa8, 0x6a)
        for y in range(total):
            for x in range(total):
                if x < pad or x >= total - pad or y < pad or y >= total - pad:
                    i = (y * total + x) * 4
                    pixels[i:i + 4] = bytes((hl[0], hl[1], hl[2], 255))
    buf, w = load_logo(model['logo'], size)
    if buf:
        for y in range(size):
            for x in range(size):
                blit(pixels, total, x + pad, y + pad, buf, w, src_x=x, src_y=y)
    else:
        c = model['color']
        for y in range(size):
            for x in range(size):
                i = ((y + pad) * total + x + pad) * 4
                pixels[i:i + 4] = bytes((c[0], c[1], c[2], 255))
    return png_bytes(total, total, bytes(pixels))


def draw_status_icon(logo_size=20, pct=None, model=None, scale=1.0,
                     bar_progress=None, prev_model=None, prev_alpha=0.0):
    """状态栏图标：provider logo + 底部迷你进度条。

    scale:      logo 缩放比例（做弹入动效）
    bar_progress: 进度条填充比例 0..1（覆盖 pct，做填充动效）
    prev_model / prev_alpha: 交叉淡入时的上一个 logo
    """
    model = model or MODELS[0]
    bar_h = 4
    box = int(round(logo_size * scale))
    total_w = max(logo_size, box)
    total_h = total_w + 2 + bar_h
    pixels = bytearray(total_w * total_h * 4)

    # 底部进度条
    if bar_progress is None:
        bar_progress = 0 if pct is None else max(0, min(100, pct)) / 100.0
    fill_w = round(total_w * bar_progress)
    fill = health_color((bar_progress * 100) if pct is None else pct, model['color'])
    bg = (0xd8, 0xd3, 0xca)
    by0 = total_w + 2
    for y in range(bar_h):
        for x in range(total_w):
            i = ((y + by0) * total_w + x) * 4
            if x < fill_w:
                pixels[i:i + 4] = bytes((fill[0], fill[1], fill[2], 255))
            else:
                pixels[i:i + 4] = bytes((bg[0], bg[1], bg[2], 255))

    # 旧 logo（淡出）
    if prev_model and prev_alpha > 0:
        buf, w = load_logo(prev_model['logo'], logo_size)
        if buf:
            for y in range(logo_size):
                for x in range(logo_size):
                    blit(pixels, total_w, x, y, buf, w,
                         src_x=x, src_y=y, alpha=prev_alpha)
        else:
            c = prev_model['color']
            for y in range(logo_size):
                for x in range(logo_size):
                    i = (y * total_w + x) * 4
                    pixels[i:i + 4] = bytes((c[0], c[1], c[2], 255))

    # 新 logo（缩放弹入）
    if box >= 1:
        buf, w = load_logo(model['logo'], box)
        ox = (total_w - box) // 2
        oy = (total_w - box) // 2
        if buf:
            for y in range(box):
                for x in range(box):
                    blit(pixels, total_w, ox + x, oy + y, buf, w,
                         src_x=x, src_y=y)
        else:
            c = model['color']
            for y in range(box):
                for x in range(box):
                    i = ((oy + y) * total_w + ox + x) * 4
                    pixels[i:i + 4] = bytes((c[0], c[1], c[2], 255))

    return png_bytes(total_w, total_h, bytes(pixels))


# ===================== 动效 =====================
# 回弹缓动：overshoot 参数越大回弹越明显
def ease_out_back(t, overshoot=1.9):
    c1 = overshoot
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    return 3 * t * t - 2 * t * t * t


# 切换动画关键帧
ANIM_FRAMES = 12                # 帧数
ANIM_INTERVAL = 0.045           # 秒/帧 → 12 帧 ≈ 0.54 秒
ANIM_STEP = 1.0 / ANIM_FRAMES   # 每帧推进的进度


def anim_state(t):
    """给定动画进度 t，返回 (scale, bar_progress, prev_alpha)。"""
    # 缩放：overshoot 回弹
    scale = ease_out_back(min(1.0, t * 1.25), 1.9)
    # 进度条：稍滞后开始填充
    bt = max(0.0, (t - 0.18) / 0.82)
    bar = ease_out_cubic(min(1.0, bt))
    # 旧 logo 淡出
    prev_alpha = max(0.0, 1.0 - t * 1.8)
    return scale, bar, prev_alpha


# ===================== 数据 =====================
def read_selected():
    try:
        with open(STATE) as f:
            m = json.load(f).get('model')
        if m in MODEL_BY_KEY:
            return m
    except Exception:
        pass
    return 'bigmodel'


def write_selected(model):
    os.makedirs(DATA, exist_ok=True)
    payload = {'model': model, 'updated_at': datetime.now().isoformat()}
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def read_last_rendered():
    """读上次渲染的 provider —— 用于插件重启后补播切换动效。"""
    try:
        with open(LAST_RENDERED) as f:
            m = json.load(f).get('model')
        if m in MODEL_BY_KEY:
            return m
    except Exception:
        pass
    return None


def write_last_rendered(model):
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = LAST_RENDERED + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'model': model}, f)
        os.replace(tmp, LAST_RENDERED)
    except Exception:
        pass


SNAPSHOT_MAX_AGE = 600      # 快照超过 10 分钟才重新采集


def fetch_data(force=False):
    """取用量数据。

    优先直接读 data/latest.json 快照（v1 插件也读这个文件），
    快照过期或缺失时才真正跑 fetch.py —— 避免每次刷新都起一轮
    Playwright，也避免与 v1 插件抢 flock 锁。
    """
    snap_path = os.path.join(DATA, 'latest.json')
    age = None
    try:
        age = time.time() - os.path.getmtime(snap_path)
    except OSError:
        pass

    if not force and age is not None and age < SNAPSHOT_MAX_AGE:
        try:
            with open(snap_path) as f:
                return json.load(f)
        except Exception:
            pass

    try:
        p = subprocess.run([PY_BIN, FETCH_PY, '--json'],
                           cwd=ROOT, capture_output=True, timeout=180)
        out = p.stdout.decode(errors='replace').strip()
        if out:
            return json.loads(out)
    except Exception:
        pass
    try:
        with open(snap_path) as f:
            return json.load(f)
    except Exception:
        return {'rows': [], 'errors': {}}


# ===================== 渲染 =====================
def rows_of(rows, site):
    return [r for r in rows if r['site'] == site]


def pct_of(rows, site, window):
    for r in rows_of(rows, site):
        if r['window'] == window:
            return r.get('used_pct')
    return None


def main_pct(rows, site):
    for w in ('5h', 'billing', 'week'):
        p = pct_of(rows, site, w)
        if p is not None:
            return p
    return None


def fmt_big(n):
    if n is None:
        return '—'
    if n >= 1e9:
        return ('%.1f' % (n / 1e9)) + 'B'
    if n >= 1e6:
        return ('%.1f' % (n / 1e6)) + 'M'
    if n >= 1e3:
        return ('%.1f' % (n / 1e3)) + 'K'
    return str(int(round(n)))


def fmt_reset(iso):
    if not iso:
        return ''
    try:
        return datetime.fromisoformat(iso).astimezone().strftime('%m-%d') + ' 后重置'
    except Exception:
        return ''


def header_text(rows, sel, errors):
    if sel == 'deepseek':
        bal = None
        for r in rows_of(rows, 'deepseek'):
            if r['window'] == 'billing' and r.get('remaining') is not None:
                bal = r['remaining']
        txt = ('¥%.2f' % bal) if bal is not None else '¥?'
    else:
        p = main_pct(rows, sel)
        txt = ('剩余%.0f%%' % (100 - p)) if p is not None else '无数据'
    if sel in errors:
        txt += ' ⚠️'
    return txt


def render_dropdown(data, errors, sel):
    """渲染下拉菜单正文（不含状态栏部分）。"""
    model = MODEL_BY_KEY[sel]
    data_rows = data.get('rows', [])
    out = []
    out.append('---')
    # 顶部：各 provider 的 logo（可点击切换）
    for m in MODELS:
        lp = draw_single_logo(28, m, m['key'] == sel)
        out.append(' | image=' + b64(lp)
                   + ' bash=' + SWITCH
                   + ' params param0=' + m['key']
                   + ' terminal=false refresh=true'
                   + ' tooltip=' + m['name'])
    out.append('当前：' + model['name'] + ' · 点上方 logo 切换 | size=11 color=#8a8680')

    # 主体：当前 provider 的额度
    out.append('---')
    data_ts = data.get('ts', '')
    if data_ts:
        try:
            local = datetime.fromisoformat(data_ts).astimezone().strftime('%m-%d %H:%M')
            out.append('更新于 ' + local + ' | color=gray size=11')
        except Exception:
            pass

    if sel in errors:
        out.append('抓取失败: ' + errors[sel][:70] + ' | color=red size=11')
        if sel == 'bigmodel':
            out.append('可试试底部「从 Chrome 同步登录态」 | size=11 color=#8a8680')

    def show_site(site, title):
        rs = rows_of(data_rows, site)
        if not rs:
            out.append('---')
            out.append(title + ' | size=13 color=' + hexc(model['color'])
                       + ' href=' + CONSOLE.get(site, ''))
            out.append('   数据不可用 | size=12 color=#8a8680')
            return
        out.append('---')
        out.append(title + ' | size=13 color=' + hexc(model['color'])
                   + ' href=' + CONSOLE.get(site, ''))
        for r in rs:
            w = r['window']
            label = WINDOW_NAMES.get(w, w)
            pct = r.get('used_pct')
            if pct is not None:
                bar = draw_bar(pct, 220, 16, model['color'])
                out.append(' | image=' + b64(bar))
                extra = ''
                if r.get('total'):
                    extra = '  %s/%s' % (fmt_big(r.get('used')), fmt_big(r.get('total')))
                line = '   已用 %.1f%%  %s%s' % (pct, label, extra)
                if r.get('unlimited'):
                    line += '  ∞ 无限制'
                rst = fmt_reset(r.get('reset_at'))
                if rst:
                    line += '  · ' + rst
                out.append(line + ' | size=12 color=#d6d3cc')
            elif site == 'deepseek' and w == 'billing':
                out.append('   余额 ¥%.2f  ·  累计消费 ¥%.2f / 充值 ¥%.2f'
                           % (r.get('remaining') or 0, r.get('used') or 0, r.get('total') or 0)
                           + ' | size=12 color=#3f3e3b')
            elif site == 'deepseek' and w == 'today':
                meta = r.get('meta') or {}
                tok = meta.get('today_tokens')
                line = '   当日消费 ¥%.2f' % (r.get('used') or 0)
                if tok is not None:
                    line += '  ·  当日 token ' + fmt_big(tok)
                out.append(line + ' | size=12 color=#3f3e3b')
            elif site == 'mimo' and w == 'today':
                meta = r.get('meta') or {}
                tok = meta.get('today_tokens')
                reqn = meta.get('today_requests')
                line = '   当日 ' + (fmt_big(tok) if tok is not None else '—') + ' tokens'
                if reqn is not None:
                    line += '   ·   ' + str(reqn) + ' 次请求'
                out.append(line + ' | size=12 color=#3f3e3b')
            elif r.get('used') and w == 'billing':
                out.append('   %s: 近30天 %s tokens' % (label, fmt_big(r['used']))
                           + ' | size=12 color=#3f3e3b')

    if sel == 'kimi':
        show_site('kimi', 'Kimi（订阅总额）')
        if rows_of(data_rows, 'kimi-code'):
            show_site('kimi-code', 'Kimi Code（编程额度）')
    else:
        show_site(sel, model['name'])

    # 底部操作区（与 v1 一致）
    out.append('---')
    out.append('刷新 | refresh=true')
    out.append('打开仪表盘 | bash=' + ROOT + '/scripts/open_dashboard.sh terminal=false')
    out.append('重新登录（浏览器）| bash=' + ROOT + '/scripts/login.sh terminal=true')
    out.append('从 Chrome 同步登录态 | bash=' + ROOT + '/scripts/sync_chrome_auth.sh terminal=true')
    return out


def main():
    global data_rows
    data = fetch_data()
    data_rows = data.get('rows', [])
    errors = data.get('errors', {})

    # 上次渲染的 provider 记到独立文件里：
    # 插件进程被 SwiftBar 重启后，仍能知道「换过 provider」并补播动效。
    prev_sel = read_last_rendered() or read_selected()
    cur_sel = read_selected()
    anim_t = 1.0
    prev_model = None
    if cur_sel != prev_sel:
        # 重启前刚好发生过切换 → 补播动效
        prev_model = MODEL_BY_KEY[prev_sel]
        prev_sel = cur_sel
        anim_t = 0.0
    write_last_rendered(prev_sel)

    last_data_refresh = time.time()
    idle_tick = 0

    while True:
        # ---- 检测 provider 变化（点击 logo → switch_model.sh 改状态文件）----
        cur = read_selected()
        if cur != prev_sel:
            prev_model = MODEL_BY_KEY[prev_sel]
            prev_sel = cur
            anim_t = 0.0

        model = MODEL_BY_KEY[prev_sel]
        pct = main_pct(data_rows, prev_sel)
        scale, bar, prev_alpha = anim_state(anim_t)

        icon = draw_status_icon(20, pct, model, scale=scale,
                                bar_progress=(bar if anim_t < 1.0 else None),
                                prev_model=prev_model, prev_alpha=prev_alpha)
        header = header_text(data_rows, prev_sel, errors)

        lines = [' | image=' + b64(icon),
                 header + ' | size=12 color=' + hexc(model['color'])]
        lines.extend(render_dropdown(data, errors, prev_sel))

        print('\n'.join(lines))
        sys.stdout.flush()
        print('~~~')
        sys.stdout.flush()

        if anim_t < 1.0:
            # 动画帧：快速推进
            anim_t = min(1.0, anim_t + ANIM_STEP)
            write_last_rendered(prev_sel)
            time.sleep(ANIM_INTERVAL)
            continue

        # 动画结束，落下最后状态
        prev_model = None
        write_last_rendered(prev_sel)

        # ---- 空闲期：快速轮询选择变化 + 定期刷新数据 ----
        idle_tick += 1
        if idle_tick % IDLE_TICKS == 0:
            # 刷新数据（fetch_data 内部按快照年龄判断是否真采集）
            data = fetch_data()
            data_rows = data.get('rows', [])
            errors = data.get('errors', {})
            last_data_refresh = time.time()
        time.sleep(IDLE_POLL_INTERVAL)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
