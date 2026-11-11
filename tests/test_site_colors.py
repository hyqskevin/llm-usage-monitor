#!/usr/bin/env python3
"""Kimi / Kimi Code 配色区分的回归测试。

背景：v2 把 kimi-code 合并进 Kimi 分组后，标题和进度条都用了 Kimi 的粉色，
      两者在菜单里完全无法区分。v1（swiftbar/llm-usage.10m.sh）里
      Kimi=粉 0xc89a9a、Kimi Code=紫 0xa594b8，并有一条「粉+紫」分段总用量条。

覆盖：
  - kimi 与 kimi-code 的站点主色不同，且 kimi-code 是紫色系
  - draw_segmented_bar 的分段宽度符合 code_pct/kimi_pct 语义
  - 分段条里确实同时出现粉、紫两种像素
  - 非零段保底宽度（占比极小时颜色不消失）
  - render_dropdown 里 Kimi Code 小节标题用的是紫色而非粉色

运行：
  python3 tests/test_site_colors.py
"""
import base64
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    'llm_bar_v2', os.path.join(ROOT, 'scripts', 'llm_bar_v2.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FAILED = []


def check(name, cond, detail=''):
    if cond:
        print('  PASS  %s' % name)
    else:
        print('  FAIL  %s  %s' % (name, detail))
        FAILED.append(name)


def png_pixels(png_bytes):
    """解 PNG → [(r,g,b,a), ...]（只处理本脚本自产的 8bit RGBA 非隔行图）。"""
    import struct
    import zlib
    assert png_bytes[:8] == b'\x89PNG\r\n\x1a\n'
    pos = 8
    idat = b''
    w = h = 0
    while pos < len(png_bytes):
        ln = struct.unpack('>I', png_bytes[pos:pos + 4])[0]
        typ = png_bytes[pos + 4:pos + 8]
        data = png_bytes[pos + 8:pos + 8 + ln]
        if typ == b'IHDR':
            w, h = struct.unpack('>II', data[:8])
        elif typ == b'IDAT':
            idat += data
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4
    out = []
    prev = bytearray(stride)
    p = 0
    for _y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xff
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xff
        elif ft == 3:
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xff
        elif ft == 4:
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xff
        prev = line
        for x in range(w):
            out.append(tuple(line[x * 4:x * 4 + 4]))
    return w, h, out


def near(c, t, tol=24):
    return all(abs(c[i] - t[i]) <= tol for i in range(3))


def bar_segment_widths(png, width, height):
    """返回 (粉段宽, 紫段宽)：按中线行逐像素归类。"""
    _w, _h, px = png_pixels(png)
    y = height // 2
    row = px[y * width:(y + 1) * width]
    pink = sum(1 for c in row if near(c, m.SITE_COLORS['kimi']))
    purple = sum(1 for c in row if near(c, m.SITE_COLORS['kimi-code']))
    return pink, purple


print('== 站点主色 ==')
kimi_c = m.site_color('kimi')
code_c = m.site_color('kimi-code')
check('kimi 与 kimi-code 颜色不同', kimi_c != code_c, '%s vs %s' % (kimi_c, code_c))
check('kimi 是 v1 的粉色 c89a9a', kimi_c == (0xc8, 0x9a, 0x9a), str(kimi_c))
check('kimi-code 是 v1 的紫色 a594b8', code_c == (0xa5, 0x94, 0xb8), str(code_c))
check('kimi-code 偏紫（R>B）', code_c[2] > code_c[0], str(code_c))
check('kimi 偏粉（R>B）', kimi_c[0] > kimi_c[2], str(kimi_c))
for site in ('bigmodel', 'minimax', 'deepseek', 'mimo'):
    check('%s 颜色未被改动' % site,
          m.site_color(site) == m.MODEL_BY_KEY[site]['color'],
          '%s vs %s' % (m.site_color(site), m.MODEL_BY_KEY[site]['color']))

print('== 分段总用量条 ==')
W, H = 220, 18
png = m.draw_segmented_bar(50.0, 25.0, W, H)
pink, purple = bar_segment_widths(png, W, H)
# 已用 50% → 110px；Code 占已用的 50% → 55px 紫 / 55px 粉
check('粉段宽度 ≈ 55', abs(pink - 55) <= 2, 'pink=%d' % pink)
check('紫段宽度 ≈ 55', abs(purple - 55) <= 2, 'purple=%d' % purple)
check('两色同时出现', pink > 0 and purple > 0, 'pink=%d purple=%d' % (pink, purple))

print('== 极小占比保底 ==')
png = m.draw_segmented_bar(50.54, 50.37, W, H)
pink, purple = bar_segment_widths(png, W, H)
check('Kimi 只占 0.17% 时粉段仍可见(≥2px)', pink >= 2, 'pink=%d' % pink)
check('紫段仍占主体', purple > 100, 'purple=%d' % purple)

print('== 菜单渲染 ==')
data = json.load(open(os.path.join(ROOT, 'data', 'latest.json')))
out = m.render_dropdown(data, {}, 'kimi')
titles = [l for l in out if 'size=13 color=' in l]
code_title = [l for l in titles if 'Kimi Code' in l]
check('存在 Kimi Code 小节标题', len(code_title) == 1, str(titles))
if code_title:
    check('Kimi Code 标题用紫色 a594b8',
          'color=a594b8' in code_title[0], code_title[0][:80])
kimi_title = [l for l in titles if 'Kimi（订阅总额）' in l]
check('Kimi 标题用粉色 c89a9a',
      kimi_title and 'color=c89a9a' in kimi_title[0], str(kimi_title))
check('Kimi Code 标题不是粉色',
      not any('Kimi Code' in l and 'color=c89a9a' in l for l in titles),
      str(titles))
check('存在总使用量图例',
      any('🟥Kimi' in l and '🟪Code' in l for l in out),
      'no legend')

print('== 其它 provider 未受影响 ==')
for sel in ('bigmodel', 'minimax', 'deepseek', 'mimo'):
    o = m.render_dropdown(data, {}, sel)
    t = [l for l in o if 'size=13 color=' in l]
    check('%s 标题颜色=自身主色' % sel,
          t and ('color=' + m.hexc(m.site_color(sel))) in t[0], str(t[:1]))

print()
if FAILED:
    print('FAILED: %s' % ', '.join(FAILED))
    sys.exit(1)
print('ALL PASS')
