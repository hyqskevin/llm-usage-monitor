#!/usr/bin/env python3
"""回归测试：主循环的采集绝不能阻塞 UI 响应。

2026-11-12 事故：
  llm_bar_v2 主循环里 `fetch_data()` 同步跑 `subprocess.run(fetch.py)`，
  而 fetch.py 内部要跑 Playwright（deepseek_discover.js 等），单次 30~120 秒。
  期间主循环完全卡死 → 点击探测不执行 → **所有 logo 都点不动**。
  用户观感是「某个 session 挂了之后别的 logo 也点不了」，实际与 kimi 无关。

本测试验证：
  1. FetchRunner.start() 立即返回（不等待子进程）
  2. 采集进行中时，poll() 不阻塞
  3. 主循环里没有同步 fetch_data() 调用
"""
import os
import sys
import time
import importlib.util
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAR = os.path.join(ROOT, 'scripts', 'llm_bar_v2.py')

FAILS = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ✅ {name}')
    else:
        print(f'  ❌ {name}  {detail}')
        FAILS.append(name)


def load_bar():
    spec = importlib.util.spec_from_file_location('bar', BAR)
    m = importlib.util.module_from_spec(spec)
    sys.argv = ['bar']
    spec.loader.exec_module(m)
    return m


def test_start_is_non_blocking():
    print('== FetchRunner.start 非阻塞 ==')
    m = load_bar()
    r = m.FetchRunner.__new__(m.FetchRunner)
    m.FetchRunner.__init__(r)
    # 假装 fetch 要跑很久
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(10)
        return {'rows': [], 'errors': {}}

    r._do_fetch = slow
    t0 = time.time()
    r.start()
    dt = time.time() - t0
    check('start() 返回耗时 < 0.2s', dt < 0.2, f'实际 {dt:.3f}s')
    check('工作线程已启动', started.wait(2))
    check('running 标记为 True', r.running)

    # poll 在采集进行中必须立即返回 None，不阻塞
    t0 = time.time()
    got = r.poll()
    dt = time.time() - t0
    check('采集中 poll() 返回 None', got is None, str(got))
    check('采集中 poll() 耗时 < 0.05s', dt < 0.05, f'实际 {dt:.3f}s')

    release.set()
    time.sleep(0.3)
    got = r.poll()
    check('采集完成后 poll() 拿到结果', got is not None and 'rows' in got[1])


def test_second_start_ignored():
    print('== 并发保护 ==')
    m = load_bar()
    r = m.FetchRunner.__new__(m.FetchRunner)
    m.FetchRunner.__init__(r)
    release = threading.Event()

    def slow():
        release.wait(5)
        return {'rows': [], 'errors': {}}

    r._do_fetch = slow
    check('第一次 start 返回 True', r.start() is True)
    check('采集中再 start 返回 False', r.start() is False)
    release.set()


def test_main_loop_has_no_sync_fetch():
    print('== 主循环无同步采集 ==')
    with open(BAR) as f:
        src = f.read()
    main_start = src.index('\ndef main():')
    body = src[main_start:]
    # 剥离注释（避免匹配到注释里提到的 fetch_data()）
    code = '\n'.join(l.split('#')[0] for l in body.split('\n'))
    check('main() 内无 fetch_data() 调用', 'fetch_data(' not in code,
          code[code.find('fetch_data('):code.find('fetch_data(') + 60]
          if 'fetch_data(' in code else '')
    check('main() 内使用 FETCHER.start()', 'FETCHER.start()' in code)
    check('main() 内使用 FETCHER.poll()', 'FETCHER.poll()' in code)
    check('import threading 已加入', 'import threading' in src)


def test_click_probe_not_blocked_by_fetch():
    print('== 采集进行中点击探测仍可执行 ==')
    m = load_bar()
    release = threading.Event()

    def slow():
        release.wait(6)
        return {'rows': [], 'errors': {}}

    m.FETCHER._do_fetch = slow
    m.FETCHER.start()
    time.sleep(0.1)
    # 模拟主循环每轮做的事：poll + 点击探测，都必须快
    t0 = time.time()
    for _ in range(20):
        m.FETCHER.poll()
        m.handle_menu_click('bigmodel')
    dt = time.time() - t0
    check('20 轮 poll+点击探测 < 1s', dt < 1.0, f'实际 {dt:.3f}s')
    release.set()


if __name__ == '__main__':
    test_start_is_non_blocking()
    test_second_start_ignored()
    test_main_loop_has_no_sync_fetch()
    test_click_probe_not_blocked_by_fetch()
    print()
    if FAILS:
        print(f'❌ {len(FAILS)} 项失败: {FAILS}')
        sys.exit(1)
    print('✅ 全部通过')
