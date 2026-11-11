#!/usr/bin/env python3
"""条带点击命中逻辑的单元测试。

覆盖：
  - draw_logo_strip 的 geometry 与实际像素布局一致
  - handle_menu_click 的坐标映射（切换其他格 / 点击当前格 / 条带外 /
    Y 行外 / 菜单窗口外 / 无新点击）
  - 地面真值常量：MENU_IMG_INSET=14, MENU_BODY_TOP=8

运行：
  python3 tests/test_strip_hit.py
"""
import importlib.util
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


def geom_for(sel):
    m._sel_hint[0] = sel
    _png, geom = m.draw_logo_strip()
    return geom


def hit_of(geom, rel_x):
    for (x0, x1, key) in geom:
        if x0 <= rel_x < x1 + 2:
            return key
    return None


def test_geometry():
    print('test_geometry')
    geom = geom_for('bigmodel')
    check('5 个 provider', len(geom) == 5, str(geom))
    check('key 顺序', [g[2] for g in geom] ==
          ['bigmodel', 'kimi', 'minimax', 'deepseek', 'mimo'], str(geom))
    # 选中格 15px，其余 12px，gap 4
    check('选中格宽 15', geom[0][1] - geom[0][0] == 15, str(geom[0]))
    check('未选中格宽 12', geom[1][1] - geom[1][0] == 12, str(geom[1]))
    check('gap=4', geom[1][0] - geom[0][1] == 4, str(geom))
    check('首格起点=0', geom[0][0] == 0, str(geom[0]))
    # 条带图总宽 83
    png, _ = m.draw_logo_strip()
    # PNG 宽度写在 IHDR
    import struct
    w = struct.unpack('>I', png[16:20])[0]
    check('条带图宽 83', w == 83, str(w))


def test_hit_mapping():
    print('test_hit_mapping')
    geom = geom_for('bigmodel')
    check('rel 0 → bigmodel', hit_of(geom, 0) == 'bigmodel')
    check('rel 8 → bigmodel', hit_of(geom, 8) == 'bigmodel')
    check('rel 24 → kimi', hit_of(geom, 24) == 'kimi')
    check('rel 37 → minimax', hit_of(geom, 37) == 'minimax')
    check('rel 56 → deepseek', hit_of(geom, 56) == 'deepseek')
    check('rel 70 → mimo', hit_of(geom, 70) == 'mimo')
    check('gap 落在 none', hit_of(geom, 17) is None, 'gap区不应命中')
    check('超出右边界 none', hit_of(geom, 82) is None)


def test_constants():
    print('test_constants')
    # 地面真值（/tmp/striptest/final.swift AppKit 实测）
    check('MENU_IMG_INSET == 14', m.MENU_IMG_INSET == 14, str(m.MENU_IMG_INSET))
    check('MENU_BODY_TOP == 8', m.MENU_BODY_TOP == 8, str(m.MENU_BODY_TOP))
    check('STRIP_H == 19', m.STRIP_H == 19, str(m.STRIP_H))


class FakeWin:
    """替换 find_menu_windows / mouse_state 的桩。"""

    def __init__(self, wins, mouse):
        self.wins = wins
        self.mouse = mouse

    def __enter__(self):
        self._fw = m.find_menu_windows
        self._ms = m.mouse_state
        m.find_menu_windows = lambda: self.wins
        m.mouse_state = lambda: self.mouse
        return self

    def __exit__(self, *a):
        m.find_menu_windows = self._fw
        m.mouse_state = self._ms
        return False


def test_handle_menu_click():
    print('test_handle_menu_click')
    geom = geom_for('bigmodel')
    m._strip_geom[0] = geom

    WIN = [(1, 811, 25, 319, 469)]
    strip_left = 811 + m.MENU_IMG_INSET      # 825
    strip_top = 25 + m.MENU_BODY_TOP        # 33

    def click(mx, my, prev='bigmodel'):
        """模拟一次真实点击：先建立 up 计时基线，再让 up 归零表示新点击。

        handle_menu_click 用 up（距上次 leftMouseUp 的秒数）判定「新点击」：
        up 变小 = 计时器刚被重置 = 刚发生过一次点击。首轮调用只做基线化。
        """
        m._last_up_ts[0] = None
        with FakeWin(WIN, (mx, my, 5.0)):      # 基线轮
            m.handle_menu_click(prev)
        with FakeWin(WIN, (mx, my, 0.0)):      # 新点击
            return m.handle_menu_click(prev)

    # 1. 点击 minimax 格（rel 37 → 屏幕 862），应返回 minimax
    r = click(strip_left + 37, strip_top + 5)
    check('点 minimax → minimax', r == 'minimax', str(r))

    # 2. 点击当前选中格（bigmodel，rel 5）→ 不切换
    r = click(strip_left + 5, strip_top + 5)
    check('点当前格 → None', r is None, str(r))

    # 3. Y 落在条带行外（menu 其他行）→ None
    r = click(strip_left + 37, strip_top + 60)
    check('行外 Y → None', r is None, str(r))

    # 4. 菜单窗口外 → None
    r = click(100, 100)
    check('菜单外 → None', r is None, str(r))

    # 5. 无新点击（up 不变）→ None
    m._last_up_ts[0] = 0.5
    with FakeWin(WIN, (strip_left + 37, strip_top + 5, 0.5)):
        r = m.handle_menu_click('bigmodel')
    check('无新点击 → None', r is None, str(r))

    # 6. 真实点击日志回放：10:31 那次（my=41, mx=862）应命中 minimax
    r = click(862.0, 41.0)
    check('回放 10:31 点击 → minimax', r == 'minimax', str(r))

    # 7. 真实点击日志回放：10:20 那次（mx=849, my=39）应命中 kimi
    r = click(849.0, 39.0)
    check('回放 10:20 点击 → kimi', r == 'kimi', str(r))

    # 8. 真实点击日志回放：10:19 那次（mx=873, my=45）应命中 minimax
    r = click(873.0, 45.0)
    check('回放 10:19 点击 → minimax', r == 'minimax', str(r))


if __name__ == '__main__':
    test_geometry()
    test_hit_mapping()
    test_constants()
    test_handle_menu_click()
    print()
    if FAILED:
        print('FAILED: %d' % len(FAILED))
        for f in FAILED:
            print('  - %s' % f)
        sys.exit(1)
    print('ALL PASSED')
