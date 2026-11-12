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
import ctypes
import json
import os
import select as select_mod
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
CLICK_LOG = os.path.join(DATA, '.click_log.jsonl')
# 菜单项图片左缘相对菜单窗口左缘的 inset（pt）。
# 地面真值（/tmp/striptest/final.swift 实测，AppKit 弹出真实 NSMenu）：
#   条带图 cgX - 窗口 cgX = 14   → MENU_IMG_INSET
#   条带图 cgTop - 窗口 cgTop = 8 → MENU_BODY_TOP
#   (8 = NSMenu popup 顶部 padding 5 + 图项内上边距 3)
MENU_IMG_INSET = 14
# 条带图顶部相对菜单窗口顶部的偏移（pt）。实测 = 8，不是推算值。
# 注意：条带是菜单的**第一个图项**，其上只有被 NSMenu 折叠掉的连续
# separator，因此这个偏移与 body 行数 / provider 无关，是常量。
MENU_BODY_TOP = 8
STRIP_H = 34
# Y 容差：条带行高 = 图高 + 6（NSMenu 图项上下各留 3），点击落在行内
# 任意位置都算命中。
STRIP_Y_TOLERANCE = 8
FETCH_PY = os.path.join(ROOT, 'fetch.py')
PY_BIN = sys.executable

# 空闲期轮询间隔（秒）。
#
# 2026-11-12 从 0.5 降到 0.1：菜单弹出到用户点 logo 通常在 0.3 秒内，
# 0.5 秒的轮询有相当概率整个错过「菜单窗口存在」的窗口期 →
# 表现为「有时能切有时不能」。0.1 秒下每次轮询的探测开销
# （find_menu_windows 0.9ms + mouse_state 0.0ms）可以忽略。
IDLE_POLL_INTERVAL = 0.1
# 数据刷新周期（次数）。0.1s × 600 = 60 秒，与原来的 60 秒一致。
IDLE_TICKS = 600
# 点击有效期（秒）。超过这个时间的 leftMouseUp 视为「与本次轮询无关」，
# 直接丢弃。取值要大于「用户点完 → 菜单窗口可枚举」的延迟。
# 2026-11-12 从 1.2 放宽到 3.0：1.2 秒时大量点击被静默丢弃
# （且丢在日志之前，排查时完全看不到）。
CLICK_STALE_SEC = 3.0

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

# 站点配色（沿用 v1 swiftbar/llm-usage.10m.sh 的 SITE_COLORS）：
#   Kimi = 粉，Kimi Code = 紫 —— 两者在同一分组下必须视觉可分。
SITE_COLORS = {
    'bigmodel':  (0x7d, 0x96, 0xb5),
    'kimi':      (0xc8, 0x9a, 0x9a),   # 粉
    'kimi-code': (0xa5, 0x94, 0xb8),   # 紫
    'minimax':   (0x8f, 0xb0, 0x8f),
    'deepseek':  (0x6b, 0x9a, 0xa8),
    'mimo':      (0xc9, 0xa8, 0x8a),
}


def site_color(site):
    """取站点主色；未知站点回落到所属 provider 的颜色。"""
    if site in SITE_COLORS:
        return SITE_COLORS[site]
    base = site.split('-')[0]
    return SITE_COLORS.get(base, (0x70, 0x70, 0x70))


CONSOLE = {
    'bigmodel': 'https://bigmodel.cn/coding-plan/personal/usage',
    'kimi':     'https://www.kimi.com/membership/subscription?tab=quota',
    'kimi-code': 'https://www.kimi.com/membership/subscription?tab=quota',
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


def draw_segmented_bar(kimi_pct, code_pct, width=220, height=16):
    """Kimi 订阅总额进度条：已用部分按比例切成 Kimi(粉) + Code(紫)，剩余为底色。

    语义与 v1 一致：
      kimi_pct = 已用总量（kimi/billing 的 amountUsedRatio）
      code_pct = Code 已用（kimi-code/billing）
      Code 在**已用部分**中的占比 = code_pct / kimi_pct
    """
    bg = (0xec, 0xe8, 0xe1)
    kimi_pct = kimi_pct or 0
    code_pct = code_pct or 0
    total_used_w = round(width * min(100, kimi_pct) / 100)
    code_share = (code_pct / kimi_pct) if kimi_pct > 0 else 0
    code_w = min(round(total_used_w * code_share), total_used_w)
    kimi_seg = total_used_w - code_w
    # 非零段保底 2px，避免某一方占比极小时该颜色完全不可见
    # （实测 Kimi 只占 0.2% 时粉段宽 0，条子看起来像纯紫）
    if kimi_seg == 0 and total_used_w > 0 and (kimi_pct - code_pct) > 0:
        kimi_seg = min(2, total_used_w)
        code_w = total_used_w - kimi_seg
    elif code_w == 0 and total_used_w > 0 and code_pct > 0:
        code_w = min(2, total_used_w)
        kimi_seg = total_used_w - code_w
    kimi_color = SITE_COLORS['kimi']
    code_color = SITE_COLORS['kimi-code']
    kimi_hi = lerp(kimi_color, (255, 255, 255), 0.18)
    kimi_lo = lerp(kimi_color, (0, 0, 0), 0.15)
    code_hi = lerp(code_color, (255, 255, 255), 0.18)
    code_lo = lerp(code_color, (0, 0, 0), 0.15)
    pixels = bytearray(width * height * 4)
    for y in range(height):
        t = y / max(1, height - 1)
        for x in range(width):
            i = (y * width + x) * 4
            if x < kimi_seg:
                c = lerp(kimi_hi, kimi_lo, t)
            elif x < kimi_seg + code_w:
                c = lerp(code_hi, code_lo, t)
            else:
                c = bg
            pixels[i:i + 4] = bytes((c[0], c[1], c[2], 255))
    return png_bytes(width, height, bytes(pixels))


def draw_logo_strip(active=28, inactive=24, gap=6, pad=3):
    """下拉菜单顶部的横向 logo 条带：5 个 provider logo 拼成**一张图**。

    为什么拼一张图：NSMenu 的每一行只能放一个菜单项（一个 image=），
    要「横向排列」只能把所有 logo 画进同一张 PNG。

    交互（2026-11-11 v2 改造，去掉 fold 下拉）：
      条带是**无 action 的纯图片项**——实测（/tmp/testmenu/gray）disabled 项
      的 image 不被灰化、保持原色，且点击 disabled 项不会触发任何
      performItemAction → 菜单不会 dismiss。
      点击由常驻进程自己探测：CGEventSource 检测真实 leftMouseUp 后，
      枚举 layer=101 的 SwiftBar 菜单窗口 → 纯几何定位条带（条带是菜单
      第一个图项，相对窗口左缘/顶缘的偏移是实测常量 dx=14 / dyTop=8）
      → 按鼠标 x 映射到具体 provider（几何来自本函数返回的 geometry）。

    返回 (png_bytes, geometry)；geometry: list of (x0, x1, key)。
    """
    n = len(MODELS)
    heights = [active if m['key'] == _sel_hint[0] else inactive for m in MODELS]
    row_h = max(heights)
    total_w = sum(heights) + gap * (n - 1) + pad * 2
    total_h = row_h + pad * 2
    pixels = bytearray(total_w * total_h * 4)

    x = pad
    geometry = []
    for m, side in zip(MODELS, heights):
        buf, w = load_logo(m['logo'], side)
        y0 = pad + (row_h - side) // 2
        if buf:
            for yy in range(side):
                for xx in range(side):
                    blit(pixels, total_w, x + xx, y0 + yy, buf, w,
                         src_x=xx, src_y=yy)
        else:
            c = m['color']
            for yy in range(side):
                for xx in range(side):
                    i = ((y0 + yy) * total_w + (x + xx)) * 4
                    pixels[i:i + 4] = bytes((c[0], c[1], c[2], 255))
        if m['key'] == _sel_hint[0]:
            # 选中格：实心琥珀描边（整圈 2px 粗边，连四角）。
            # 琥珀色也用于人眼识别当前选中项（不再用于截图定位）。
            hl = (0xd4, 0xa8, 0x6a)
            bw = 2
            for yy in range(side):
                for xx in range(side):
                    edge = (xx < bw or xx >= side - bw
                            or yy < bw or yy >= side - bw)
                    if edge:
                        i = ((y0 + yy) * total_w + (x + xx)) * 4
                        pixels[i:i + 4] = bytes((hl[0], hl[1], hl[2], 255))
        geometry.append((x - pad, x - pad + side, m['key']))
        x += side + gap
    return png_bytes(total_w, total_h, bytes(pixels)), geometry


# draw_logo_strip 需要「当前选中」做描边，用模块级提示变量传入（避免改签名）
_sel_hint = ['bigmodel']
# 条带几何（draw_logo_strip 返回的 [(x0,x1,key),...]），给点击探测器用
_strip_geom = [None]
# 条带图总宽（px），用于多窗口时判定哪个窗口含条带
_strip_w = [154]
# 点击探测状态：上次见到的 leftMouseUp 时间戳（秒）；None=尚未基线化
_last_up_ts = [None]


def _probe_init():
    """初始化 CoreGraphics ctypes 绑定。失败返回 False（禁用点击探测）。"""
    if _probe_init._done:
        return _probe_init._ok
    _probe_init._done = True
    try:
        cg = _probe_init.cg = ctypes.CDLL(
            '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cf = _probe_init.cf = ctypes.CDLL(
            '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')

        class CGPoint(ctypes.Structure):
            _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]
        _probe_init.CGPoint = CGPoint

        for fn, args, res in (
            ('CGEventCreate', [ctypes.c_void_p], ctypes.c_void_p),
            ('CGEventGetLocation', [ctypes.c_void_p], CGPoint),
            ('CGEventSourceSecondsSinceLastEventType',
             [ctypes.c_uint32, ctypes.c_uint32], ctypes.c_double),
            ('CGWindowListCopyWindowInfo',
             [ctypes.c_uint32, ctypes.c_uint32], ctypes.c_void_p),
        ):
            f = getattr(cg, fn)
            f.argtypes = args
            f.restype = res
        for fn, args, res in (
            ('CFArrayGetCount', [ctypes.c_void_p], ctypes.c_long),
            ('CFArrayGetValueAtIndex',
             [ctypes.c_void_p, ctypes.c_long], ctypes.c_void_p),
            ('CFDictionaryGetValue',
             [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p),
            ('CFStringCreateWithCString',
             [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_void_p),
            ('CFNumberGetValue',
             [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p], ctypes.c_bool),
        ):
            f = getattr(cf, fn)
            f.argtypes = args
            f.restype = res
        cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        _probe_init._ok = True
    except Exception:
        _probe_init._ok = False
    return _probe_init._ok


_probe_init._done = False
_probe_init._ok = False


def _key(s):
    cf = _probe_init.cf
    return cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)


def _cstr(ref):
    if not ref:
        return ''
    cf = _probe_init.cf
    buf = ctypes.create_string_buffer(256)
    return (buf.value.decode()
            if cf.CFStringGetCString(ref, buf, 256, 0x08000100) else '')


def _cnum(ref):
    if not ref:
        return None
    cf = _probe_init.cf
    out = ctypes.c_double(0)
    cf.CFNumberGetValue(ref, 6, ctypes.byref(out))  # kCFNumberFloat64Type
    return out.value


def find_menu_windows():
    """枚举 on-screen 窗口，返回 SwiftBar 菜单弹出窗口列表 [(id,x,y,w,h)]。

    SwiftBar 的 NSMenu 弹出层是 layer=101（kCGPopUpMenuWindowLevel）。
    """
    if not _probe_init():
        return []
    cg, cf = _probe_init.cg, _probe_init.cf
    try:
        arr = cg.CGWindowListCopyWindowInfo(1 | 16, 0)  # onScreen|excludeDesktop
        if not arr:
            return []
        out = []
        for i in range(cf.CFArrayGetCount(arr)):
            d = cf.CFArrayGetValueAtIndex(arr, i)
            if _cstr(cf.CFDictionaryGetValue(d, _key('kCGWindowOwnerName'))) != 'SwiftBar':
                continue
            if _cnum(cf.CFDictionaryGetValue(d, _key('kCGWindowLayer'))) != 101:
                continue
            b = cf.CFDictionaryGetValue(d, _key('kCGWindowBounds'))
            if not b:
                continue
            x = _cnum(cf.CFDictionaryGetValue(b, _key('X')))
            y = _cnum(cf.CFDictionaryGetValue(b, _key('Y')))
            w = _cnum(cf.CFDictionaryGetValue(b, _key('Width')))
            h = _cnum(cf.CFDictionaryGetValue(b, _key('Height')))
            num = _cnum(cf.CFDictionaryGetValue(d, _key('kCGWindowNumber')))
            if None not in (x, y, w, h, num):
                out.append((int(num), x, y, w, h))
        return out
    except Exception:
        return []


def pick_strip_window(wins, mx, my):
    """从多个 SwiftBar 菜单窗口里挑出「含 logo 条带」的那一个。

    背景（2026-11-12 实测）：v1 与 v2 两个插件并存，SwiftBar 会在同一个
    状态栏区域生成多个 NSMenu 弹窗（各自的窗口宽度不同）。旧的取法是
    「第一个包含鼠标的窗口」，会选到另一个插件的菜单 → 条带起始 X 算错
    → 命中错位或落空。

    判定：条带图宽 154pt，条带左缘 = 窗口 x + MENU_IMG_INSET(14)，
    因此条带覆盖 [x+14, x+14+strip_w]。取「鼠标到条带左缘的偏移」
    落在 [0, strip_w] 的窗口。
    """
    strip_w = _strip_w[0] or 154
    cands = [(wx, wy, ww, wh) for (_n, wx, wy, ww, wh) in wins
             if wx <= mx < wx + ww and wy <= my < wy + wh]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    # 多窗口：优先选能让鼠标落在条带内的那个
    best = None
    for (wx, wy, ww, wh) in cands:
        rel = mx - (wx + MENU_IMG_INSET)
        if 0 <= rel <= strip_w:
            best = (wx, wy, ww, wh)
            break
    return best or cands[0]


def mouse_state():
    """返回 (mx, my, up_elapsed)。up_elapsed = 距上次真实 leftMouseUp 的秒数。

    只统计真实硬件事件（合成事件不计入）—— 免辅助功能权限。
    """
    if not _probe_init():
        return (None, None, None)
    cg = _probe_init.cg
    try:
        ev = cg.CGEventCreate(None)
        loc = cg.CGEventGetLocation(ev)
        up = cg.CGEventSourceSecondsSinceLastEventType(1, 2)  # 1=hid, 2=leftMouseUp
        return (loc.x, loc.y, up)
    except Exception:
        return (None, None, None)


def handle_menu_click(prev_sel):
    """点击探测主入口。返回应切换到的 provider key，无切换返回 None。

    判定链：
      1. 出现「新的」真实 leftMouseUp（时间戳比上次记录的新）
      2. 此刻鼠标坐标落在某个 layer=101 SwiftBar 菜单窗口内
      3. 定位条带：纯几何。条带是菜单第一个图项，其位置相对菜单窗口
         左缘/顶缘的偏移是常量（实测 dx=14 / dyTop=8），与 body 行数、
         provider、菜单总高都无关。
      4. 鼠标 x 相对条带左缘的偏移命中 geometry 的哪个格子
      5. 该格子不是当前选中 → 返回目标 key

    每次新点击都会写一条 CLICK_LOG 日志（点击坐标/窗口/定位结果/
    命中格子），用于真实环境核对。
    """
    mx, my, up = mouse_state()
    if mx is None:
        return None
    if _last_up_ts[0] is None:
        _last_up_ts[0] = up          # 首轮基线化，不触发
        return None
    if up >= _last_up_ts[0] - 1e-9:
        _last_up_ts[0] = up
        return None
    # 出现了新的 leftMouseUp（up 变小 = 计时器刚被重置）
    _last_up_ts[0] = up
    if up > CLICK_STALE_SEC:
        # 太久前的点击，与本次轮询无关。
        # 门槛要放宽：SwiftBar 重绘菜单需要时间，用户点完 logo 后
        # 菜单可能到 1 秒后才被检测到窗口。门槛太窄会「静默丢弃」，
        # 表现为「有时能切有时不能」且日志里什么都没有。
        _log_click({'mx': mx, 'my': my, 'method': 'none', 'hit': None,
                    'prev': prev_sel, 'up': round(up, 3),
                    'reason': 'stale_click'})
        return None
    # 鼠标必须在某个 SwiftBar 菜单窗口内
    wins = find_menu_windows()
    win = pick_strip_window(wins, mx, my)
    if win is None:
        # 没有窗口 → 说明菜单已收起/还没弹出，记一条便于排查
        _log_click({'mx': mx, 'my': my, 'method': 'none',
                    'hit': None, 'prev': prev_sel,
                    'reason': 'no_menu_window',
                    'wins': [(int(x), int(y), int(w), int(h))
                             for (_n, x, y, w, h) in wins]})
        return None
    geom = _strip_geom[0]
    if not geom:
        return None

    # ---- 定位条带左缘：纯几何（实测常量）----
    # 条带是菜单第一个图项，相对菜单窗口的偏移是常量：
    #   左缘 = 窗口 x + MENU_IMG_INSET(14)
    #   顶缘 = 窗口 y + MENU_BODY_TOP(8)
    # 这两个值由 AppKit 实测得出（/tmp/striptest/final.swift），
    # 与 body 行数 / provider / 菜单总高无关。
    wx, wy, ww, wh = win
    strip_left = wx + MENU_IMG_INSET
    strip_top = wy + MENU_BODY_TOP
    method = 'geometry'

    # Y 约束：鼠标必须落在条带行内（否则点到菜单其它行会被误判为切换）
    if not (strip_top - STRIP_Y_TOLERANCE <= my
            <= strip_top + STRIP_H + STRIP_Y_TOLERANCE):
        _log_click({'mx': mx, 'my': my, 'method': method,
                    'strip_left': strip_left, 'strip_top': strip_top,
                    'rel_x': mx - strip_left, 'hit': None, 'prev': prev_sel,
                    'reason': 'y_out_of_strip_row',
                    'wins': [(int(x), int(y), int(w), int(h))
                             for (_n, x, y, w, h) in wins]})
        return None

    rel_x = mx - strip_left
    hit = None
    for (x0, x1, key) in geom:
        if x0 <= rel_x < x1 + 2:         # +2: 选中格 2px 描边的外扩
            hit = key
            break
    _log_click({'mx': mx, 'my': my, 'method': method, 'strip_left': strip_left,
                'strip_top': strip_top, 'rel_x': rel_x, 'hit': hit,
                'prev': prev_sel, 'up': round(up, 3),
                'wins': [(int(x), int(y), int(w), int(h))
                         for (_n, x, y, w, h) in wins]})
    if hit and hit != prev_sel:
        return hit
    return None


def _log_click(info):
    """点击标定日志：真实环境核对 fallback 常数用。"""
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(CLICK_LOG, 'a') as f:
            f.write(json.dumps({'at': datetime.now().isoformat(),
                                'pid': os.getpid(), **info},
                               ensure_ascii=False) + '\n')
    except Exception:
        pass


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

    输出固定为 **正方形** logo_size × logo_size（SwiftBar 菜单栏状态项
    按正方形图标渲染，非等比 PNG 会被缩放变形，且可能被判定为无效
    状态项 → 菜单栏不显示）。进度条做成底部 3px 横条，占用 logo 高度内的
    底部区域，因此整体仍是正方形。

    scale:      logo 缩放比例（做弹入动效）
    bar_progress: 进度条填充比例 0..1（覆盖 pct，做填充动效）
    prev_model / prev_alpha: 交叉淡入时的上一个 logo
    """
    model = model or MODELS[0]
    total = logo_size
    bar_h = 3
    pixels = bytearray(total * total * 4)

    # 底部进度条（画在正方形内部底部）
    if bar_progress is None:
        bar_progress = 0 if pct is None else max(0, min(100, pct)) / 100.0
    fill_w = round(total * bar_progress)
    fill = health_color((bar_progress * 100) if pct is None else pct, model['color'])
    bg = (0xd8, 0xd3, 0xca)
    by0 = total - bar_h
    for y in range(bar_h):
        for x in range(total):
            i = ((y + by0) * total + x) * 4
            if x < fill_w:
                pixels[i:i + 4] = bytes((fill[0], fill[1], fill[2], 255))
            else:
                pixels[i:i + 4] = bytes((bg[0], bg[1], bg[2], 255))

    # logo 可用高度 = 总高 - 进度条高度
    logo_area = total - bar_h
    box = int(round(logo_area * scale))
    if box > logo_area:
        box = logo_area

    # 旧 logo（淡出）
    if prev_model and prev_alpha > 0:
        buf, w = load_logo(prev_model['logo'], logo_area)
        if buf:
            for y in range(logo_area):
                for x in range(logo_area):
                    blit(pixels, total, x, y, buf, w,
                         src_x=x, src_y=y, alpha=prev_alpha)
        else:
            c = prev_model['color']
            for y in range(logo_area):
                for x in range(logo_area):
                    i = (y * total + x) * 4
                    pixels[i:i + 4] = bytes((c[0], c[1], c[2], 255))

    # 新 logo（缩放弹入），在 logo 区域内居中
    if box >= 1:
        buf, w = load_logo(model['logo'], box)
        ox = (total - box) // 2
        oy = (logo_area - box) // 2
        if buf:
            for y in range(box):
                for x in range(box):
                    blit(pixels, total, ox + x, oy + y, buf, w,
                         src_x=x, src_y=y)
        else:
            c = model['color']
            for y in range(box):
                for x in range(box):
                    i = ((oy + y) * total + ox + x) * 4
                    pixels[i:i + 4] = bytes((c[0], c[1], c[2], 255))

    return png_bytes(total, total, bytes(pixels))


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


# 帧计数落盘（可观测性）：用于排查「菜单栏闪烁」这类问题 ——
#   直接读这个文件就知道插件实际向 SwiftBar 提交了多少帧、最后一帧何时提交。
#   空闲期（内容不变）不应再提交帧，若 frames 持续快速增长即为刷屏。
FRAME_STAT = os.path.join(DATA, '.frame_stat.json')


def write_frame_stat(frames, last_at, sel):
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = FRAME_STAT + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({
                'frames': frames,
                'last_frame_at': last_at,
                'model': sel,
                'pid': os.getpid(),
            }, f)
        os.replace(tmp, FRAME_STAT)
    except Exception:
        pass


# stdin 回传日志（可观测性）：验证 SwiftBar 点击是否真的把文本回传到常驻进程。
#   macOS 上菜单栏是独立合成层，screencapture 抓不到，
#   所以实机验证「点击是否生效」只能靠这个日志。
STDIN_LOG = os.path.join(DATA, '.stdin_log.jsonl')


def log_stdin(cmd):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(STDIN_LOG, 'a') as f:
            f.write(json.dumps({
                'at': datetime.now().isoformat(),
                'cmd': cmd,
                'pid': os.getpid(),
            }, ensure_ascii=False) + '\n')
    except Exception:
        pass


SNAPSHOT_MAX_AGE = 60       # 快照超过 60 秒才重新采集（用户要求每分钟取一次）


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

    # 顶部：横向 logo 条带（5 个 provider 拼一张图，选中者实心琥珀描边）。
    #   2026-11-11 v2 改造：**去掉 fold 下拉**，条带本身直接承载点击切换。
    #   实现：条带是**无 action 的纯图片项**——
    #   - 实测（/tmp/testmenu/gray 离屏渲染）：disabled 项的 image 不被
    #     灰化，保持原始饱和色（RGB 230,38,38 == 源色）；
    #   - SwiftBar buildMenuItem：无 action 且无 color 的项 item.action=nil
    #     → 点击不触发 performItemAction → 菜单不会 dismiss；
    #   - 点击探测由常驻进程自己做（见主循环 CLICK_DETECT 段）：
    #     CGEventSource 检测真实 leftMouseUp（免辅助功能权限，合成事件
    #     不计入）→ 枚举 layer=101 SwiftBar 菜单窗口 → 纯几何定位条带
    #     （条带是菜单第一个图项，相对窗口的偏移是实测常量 dx=14/dyTop=8，
    #     与 body 行数/provider/菜单总高无关）→ 鼠标 x 相对条带左缘的
    #     偏移命中 geometry 里的哪个 provider → 进程内直接切换+动效。
    #   - 点击落在菜单内但条带外 / 点在菜单外：静默忽略。
    _sel_hint[0] = sel
    strip_png, strip_geom = draw_logo_strip()
    # 条带几何存给点击探测器（x0/x1 相对条带图左缘）
    _strip_geom[0] = strip_geom
    _strip_w[0] = struct.unpack('>I', strip_png[16:20])[0]
    out.append(' | image=' + b64(strip_png)
               + ' tooltip=点击 logo 切换 provider（无需展开列表）')
    out.append('当前：' + model['name'] + ' | size=11 color=#8a8680')

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

    def show_site(site, title, seg=None):
        rs = rows_of(data_rows, site)
        col = site_color(site)
        if not rs:
            out.append('---')
            out.append(title + ' | size=13 color=' + hexc(col)
                       + ' href=' + CONSOLE.get(site, ''))
            out.append('   数据不可用 | size=12 color=#8a8680')
            return
        out.append('---')
        out.append(title + ' | size=13 color=' + hexc(col)
                   + ' href=' + CONSOLE.get(site, ''))
        if seg:
            # v1 的 Kimi 合并视图：顶部一条分段总用量条（粉=Kimi / 紫=Code）
            k_pct, c_pct = seg
            out.append(' | image=' + b64(draw_segmented_bar(k_pct, c_pct, 220, 18)))
            out.append('   总使用量  %.1f%%     🟥Kimi %.1f%%  🟪Code %.1f%%'
                       % (k_pct, max(0.0, k_pct - c_pct), c_pct)
                       + ' | size=12 color=#8a8680')
        for r in rs:
            w = r['window']
            label = WINDOW_NAMES.get(w, w)
            pct = r.get('used_pct')
            if pct is not None:
                if seg and site == 'kimi' and w == 'billing':
                    # 已用总量已由上方分段条表达，这里不重复画单色条
                    continue
                bar = draw_bar(pct, 220, 16, col)
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
        k_bill = pct_of(data_rows, 'kimi', 'billing')
        c_bill = pct_of(data_rows, 'kimi-code', 'billing')
        if k_bill is not None and c_bill is not None:
            show_site('kimi', 'Kimi（订阅总额）', seg=(k_bill, c_bill))
        else:
            show_site('kimi', 'Kimi（订阅总额）')
        if rows_of(data_rows, 'kimi-code'):
            show_site('kimi-code', 'Kimi Code（编程额度）')
    else:
        show_site(sel, model['name'])

    # 底部操作区（与 v1 一致）
    out.append('---')
    # 刷新走 stdin= 协议（由常驻进程自己重新采集）：
    #   refresh=true 会让 SwiftBar 重跑插件脚本 → 重启 streamable 进程
    #   → 进程一死菜单栏短暂空白、重新取数要等十几秒。
    out.append('刷新 | stdin=refresh tooltip=重新采集所有 provider 用量')
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
    last_frame = None          # 上一次输出的完整帧内容（去重用）
    stdin_buf = ''             # SwiftBar stdin= 协议：点击回传的命令缓冲
    frame_count = 0            # 已提交帧计数（写 .frame_stat.json 供观测）

    def handle_stdin_cmd(cmd):
        """处理一条 stdin 命令。用 nonlocal 改写主循环的状态变量。"""
        nonlocal prev_sel, prev_model, anim_t, data, errors
        global data_rows
        if not cmd:
            return
        log_stdin(cmd)
        if cmd.startswith('switch:'):
            target = cmd[len('switch:'):].strip()
            if target in MODEL_BY_KEY and target != prev_sel:
                write_selected(target)
                prev_model = MODEL_BY_KEY[prev_sel]
                prev_sel = target
                anim_t = 0.0
        elif cmd == 'refresh':
            # 手动刷新：立即重新采集（不重启 streamable 进程，
            # 否则进程一死菜单栏会短暂变空白 / ! ）。
            try:
                data = fetch_data(force=True)
                data_rows = data.get('rows', [])
                errors = data.get('errors', {})
            except Exception:
                pass

    while True:
        try:
            # ---- stdin= 协议：读 SwiftBar 点击回传（不重启进程、不收菜单重绘）----
            # SwiftBar 对 streamable 插件点击带 stdin= 的菜单项时，
            # 会把 stdin= 的文本写到插件进程的 stdin 管道（stdinPipe）。
            # 源码 StreamablePlugin.writeStdin 写的是「原文 + \n」，
            # 这里按行解析，同时兼容不带换行的写入。
            # 用 os.read 而非 sys.stdin.read(1)：后者在 TTY 模式下行缓冲，
            # 管道模式下逐字节读；os.read 在两种场景下都立即可用。
            try:
                while True:
                    chunk = select_mod.select([sys.stdin], [], [], 0)[0]
                    if not chunk:
                        break
                    piece = os.read(sys.stdin.fileno(), 256)
                    if not piece:
                        break
                    stdin_buf += piece.decode('utf-8', errors='replace')
                    if len(stdin_buf) > 512:      # 防御：缓冲异常增长
                        stdin_buf = stdin_buf[-128:]
            except (OSError, ValueError, TypeError):
                pass

            # 处理缓冲区里的所有完整命令行
            while '\n' in stdin_buf:
                line, stdin_buf = stdin_buf.split('\n', 1)
                handle_stdin_cmd(line.strip())
            # 没有换行结尾时也尝试按已知命令直接匹配（兼容不带换行的写入）
            tail = stdin_buf.strip()
            if tail and (tail == 'refresh' or
                         (tail.startswith('switch:') and
                          tail[len('switch:'):] in MODEL_BY_KEY)):
                handle_stdin_cmd(tail)
                stdin_buf = ''

            # ---- 条带点击探测（CLICK_DETECT）----
            # 条带是无 action 的图片项（点击不触发任何 SwiftBar 动作、
            # 菜单不 dismiss），由本进程自己识别「用户点了哪个 logo」：
            #   新的真实 leftMouseUp + 鼠标在 SwiftBar 菜单窗口内
            #   → 纯几何定位条带（实测常量）→ 按 x 命中 geometry 格子。
            # 开销控制：空闲期每 0.5s 一次 mouse_state（纯 ctypes 读，~0ms），
            # 全程无截图、无辅助功能权限依赖。
            # 菜单还开着时 SwiftBar 走 incrementalUpdateMenu 原地刷新，
            # 菜单不会收起，动效连续。
            try:
                hit = handle_menu_click(prev_sel)
            except Exception:
                hit = None
            if hit:
                write_selected(hit)
                prev_model = MODEL_BY_KEY[prev_sel]
                prev_sel = hit
                anim_t = 0.0

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

            # header 首行：logo 图与文字必须同行，且必须带文字。
            #   SwiftBar 对「header 只有图片、没有文字」的渲染不稳：
            #   纯 image 行会被判成空内容 → content state: empty →
            #   状态项 menu item requested visible: no → 菜单栏根本不显示。
            #   （v1 当天也因此移除了 header image，见提交 c1fe950。）
            # 注意：不要在这里多加 '---'，render_dropdown 开头自带一个，
            #   连续分隔符会造出空分组，SwiftBar 同样视为异常。
            lines = [header + ' | size=12 color=' + hexc(model['color'])
                     + ' image=' + b64(icon)]
            lines.extend(render_dropdown(data, errors, prev_sel))

            # ---- streamable 帧协议：`~~~` 必须写在内容「之前」----
            # SwiftBar 的 `~~~` 是「清空」标记，不是「提交」标记。
            # 官方 README：「SwiftBar will show 'Test 1' ... then nothing for 5
            # seconds, and then 'Test 2' indefinitely」——`~~~` 之后的静默期
            # 菜单栏是空的，之后到达的内容才是当前帧。
            #
            # 若把 `~~~` 写在内容之后（本插件初版就是如此），SwiftBar 会在每轮
            # 末尾清空菜单栏，而下一帧要等 0.5s 才补上 —— 采样正好落在空窗期，
            # 结果：content state: empty → menu item requested visible: no
            # → 状态栏什么都不显示（用户看到的是「插件没展示」）。
            #
            # 正确顺序：先 `~~~` 清空 → 再吐本帧内容 → 内容常驻到下一轮。
            #
            # ---- 帧去重（闪烁修复 2026-11-10）----
            # SwiftBar 每收到 `~~~` 就重绘菜单栏。空闲期内容完全不变时
            # 也每 0.5s 重绘一次 11840 字符的大帧 → 菜单栏肉眼可见地闪。
            # 只有「动画进行中」或「帧内容有变化」才输出。
            frame = '\n'.join(lines)
            if anim_t < 1.0 or frame != last_frame:
                print('~~~')
                sys.stdout.flush()
                print(frame)
                sys.stdout.flush()
                last_frame = frame
                frame_count += 1
                write_frame_stat(frame_count, datetime.now().isoformat(), prev_sel)

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
        except Exception:
            # 常驻流插件：任何一帧渲染抛异常都不能让进程死掉，
            # 否则 SwiftBar 会判定插件失败 → 状态栏变 ⚠️/!。
            # 这里吐一帧最小安全内容（纯文字 + ~~~）并继续轮询。
            try:
                sys.stdout.write(
                    'LLM 额度 | size=12 color=gray\n---\n'
                    '渲染异常，稍后自动恢复 | color=red size=11\n~~~\n')
                sys.stdout.flush()
            except Exception:
                pass
            time.sleep(2)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
