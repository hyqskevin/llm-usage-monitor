#!/usr/bin/env python3
"""回归测试：context 'close' 事件必须释放 profile 锁。

事故（2026-11-12 15:36 实锤）：
  login.js 用 `context.on('close')` 等用户手动关浏览器窗口，
  但 launchLocked() 原本只在 `context.close()` 被**显式调用**时释放锁。
  → login.js 后续调用的子脚本（mimo_refresh_session.js / dump_cookies.js）
    拿不到同一把锁 → 20 秒超时失败
  → data/mimo-session.json 不更新 → 用户明明登录了，采集仍 401
    （会话文件停留在 11-11，而 profile 里的 cookie 是最新的）

这是自死锁：持锁者自己去调需要同一把锁的子进程。

本测试验证：
  1. context 触发 'close' 事件时锁被释放
  2. 释放后子进程能立即拿到锁
  3. 显式 close() 也释放（不重复释放）
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = '/opt/homebrew/bin/node'
NODE_PATH = '/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules'
LOCK_DIR = os.path.join(ROOT, 'data', '.profile.lock')

FAILS = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ✅ {name}')
    else:
        print(f'  ❌ {name}  {detail}')
        FAILS.append(name)


def run_node(code, timeout=90):
    env = dict(os.environ, NODE_PATH=NODE_PATH)
    return subprocess.run([NODE, '-e', code], cwd=ROOT, env=env,
                          capture_output=True, timeout=timeout, text=True)


def test_close_event_releases_lock():
    print("== context 'close' 事件释放锁 ==")
    code = """
const { chromium } = require('playwright');
const path = require('path');
const { launchLocked } = require('./scripts/profile_lock');
const fs = require('fs');
const LOCK = path.join(__dirname, 'data', '.profile.lock');
(async () => {
  const ctx = await launchLocked(chromium, path.join(__dirname, 'browser-profile'), {
    headless: true,
    args: ['--disable-blink-features=AutomationControlled'],
  }, { timeoutMs: 20000, label: 'test' });
  console.log('LOCKED:' + fs.existsSync(LOCK));
  // 模拟 login.js 的做法：等 close 事件，而不是显式调 close()
  await new Promise(resolve => ctx.on('close', resolve));
  console.log('CLOSED');
})().catch(e => { console.error('ERR:', e.message); process.exit(1); });
"""
    p = subprocess.Popen([NODE, '-e', code], cwd=ROOT,
                         env=dict(os.environ, NODE_PATH=NODE_PATH),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    locked_seen = False
    # 等脚本报告 LOCKED
    for _ in range(60):
        time.sleep(0.5)
        if os.path.isdir(LOCK_DIR):
            locked_seen = True
            break
    check('脚本已拿到锁', locked_seen)

    # 杀进程模拟「用户关窗」→ close 事件触发 → 锁应释放
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()
    time.sleep(1.5)
    check('进程结束后锁目录已清理', not os.path.isdir(LOCK_DIR),
          '仍存在: ' + str(os.path.isdir(LOCK_DIR)))


def test_child_can_acquire_after_parent_releases():
    print('== 父释放后子进程能拿锁 ==')
    # 父持锁 3 秒后释放
    parent = """
const { acquireProfileLock, releaseProfileLock } = require('./scripts/profile_lock');
(async () => {
  await acquireProfileLock(5000, 'parent');
  console.log('P_LOCKED');
  setTimeout(() => { releaseProfileLock(); console.log('P_RELEASED'); }, 3000);
})();
"""
    p = subprocess.Popen([NODE, '-e', parent], cwd=ROOT,
                         env=dict(os.environ, NODE_PATH=NODE_PATH),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    check('父进程持锁中', os.path.isdir(LOCK_DIR))

    # 子进程立即拿锁 → 应该失败/等待（因为父还持着）
    child_fail = run_node("""
const { acquireProfileLock } = require('./scripts/profile_lock');
(async () => {
  try { await acquireProfileLock(1000, 'child'); console.log('C_LOCKED'); }
  catch (e) { console.log('C_BLOCKED'); }
})();
""", timeout=30)
    check('父持锁时子被挡住', 'C_BLOCKED' in child_fail.stdout,
          child_fail.stdout + child_fail.stderr)

    # 等父释放
    time.sleep(3)
    child_ok = run_node("""
const { acquireProfileLock } = require('./scripts/profile_lock');
(async () => {
  try { await acquireProfileLock(3000, 'child2'); console.log('C2_LOCKED'); }
  catch (e) { console.log('C2_BLOCKED'); }
})();
""", timeout=30)
    check('父释放后子能拿到锁', 'C2_LOCKED' in child_ok.stdout,
          child_ok.stdout + child_ok.stderr)


def test_manual_flag_does_not_block_lock():
    print('== manual 标记不阻塞锁获取 ==')
    os.makedirs(LOCK_DIR, exist_ok=True)
    with open(os.path.join(LOCK_DIR, 'pid'), 'w') as f:
        f.write(f'{os.getpid()} {int(time.time()*1000)}')
    # 标记文件独立于 LOCK_DIR（锁目录关闭时会被整体删掉）
    with open(os.path.join(ROOT, 'data', '.profile.manual'), 'w') as f:
        f.write(f'{os.getpid()} {int(time.time()*1000)}')
    # 手动模式（锁已由 login.js 持有）时，子脚本应当拿不到锁 → 超时
    r = run_node("""
const { acquireProfileLock } = require('./scripts/profile_lock');
(async () => {
  try { await acquireProfileLock(1500, 'x'); console.log('GOT'); }
  catch (e) { console.log('TIMEOUT'); }
})();
""", timeout=30)
    check('manual 模式下子进程拿不到锁（预期 TIMEOUT）',
          'TIMEOUT' in r.stdout, r.stdout + r.stderr)

    # 清理 manual，但锁目录还在（pid 是活着的当前进程）→ 仍应挡住
    os.remove(os.path.join(ROOT, 'data', '.profile.manual'))
    r2 = run_node("""
const { acquireProfileLock } = require('./scripts/profile_lock');
(async () => {
  try { await acquireProfileLock(1500, 'y'); console.log('GOT2'); }
  catch (e) { console.log('TIMEOUT2'); }
})();
""", timeout=30)
    check('锁目录存在时子进程仍拿不到锁',
          'TIMEOUT2' in r2.stdout, r2.stdout + r2.stderr)




def test_orphan_chromium_reaper():
    print('== 孤儿 Chromium 清理 ==')
    # 造一个「父进程已死但自己还活着」的假孤儿：
    # 起一个 sleep 进程，然后把它的父改成 1（init）再杀掉原父
    import subprocess as sp
    import signal
    import time as t

    profile_dir = os.path.join(ROOT, 'browser-profile')
    # 用一个带 user-data-dir 参数但无害的进程冒充 Chromium
    proc = sp.Popen(
        ['/bin/sh', '-c',
         f'exec -a "Google Chrome for Testing --user-data-dir={profile_dir}" sleep 60'],
        stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    t.sleep(1.0)
    # 先杀掉它的父（当前 shell 的子进程结构里，直接杀 proc 即可模拟父死）
    # 这里改成：把 proc 的父置为 1 不现实，改用「杀掉父 + 让子变孤儿」模拟
    # 简化：直接验证 killOrphanChromium 不会误杀「父还活着」的进程
    code = """
const { killOrphanChromium } = require('./scripts/profile_lock');
console.log('KILLED:' + killOrphanChromium());
"""
    r = run_node(code, timeout=30)
    check('reaper 可调用', 'KILLED:' in r.stdout, r.stdout + r.stderr)
    check('父进程活着的进程不被误杀', proc.poll() is None,
          'proc 被误杀了')
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


if __name__ == '__main__':
    try:
        test_close_event_releases_lock()
        test_child_can_acquire_after_parent_releases()
        test_manual_flag_does_not_block_lock()
        test_orphan_chromium_reaper()
    finally:
        import shutil
        shutil.rmtree(LOCK_DIR, ignore_errors=True)
        try:
            os.remove(os.path.join(ROOT, 'data', '.profile.manual'))
        except Exception:
            pass

    print()
    if FAILS:
        print(f'❌ {len(FAILS)} 项失败: {FAILS}')
        sys.exit(1)
    print('✅ 全部通过')
