#!/usr/bin/env python3
"""回归测试：browser-profile 跨进程互斥锁。

事故（2026-11-12 15:19 实锤）：
  用户手动跑 `node scripts/login.js` 时，后台采集正用 Playwright 占着
  browser-profile。Chromium 的 SingletonLock 保证一个 user-data-dir 只能
  有一个实例，后启动的实例直接退出：
      [out] 正在现有的浏览器会话中打开。
  → Playwright 抛 "Target page, context or browser has been closed"，
    人工登录脚本和后台采集**双双崩溃**。

既有防护缺口：
  fetch.py 自己的 flock（data/.fetch.lock）只防采集任务之间重叠，
  挡不住人工在终端里直接跑 node 脚本。

本测试验证：
  1. 锁能互斥（第二个进程拿不到锁时等待）
  2. 锁能在进程退出后释放（含异常退出）
  3. 陈旧锁（pid 已死）能自动清理
  4. 人工登录标记能被 fetch.py 读到
  5. 所有起 Chromium 的脚本都接入了 profile_lock
"""
import json
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


def run_node(code, timeout=30):
    env = dict(os.environ, NODE_PATH=NODE_PATH)
    return subprocess.run([NODE, '-e', code], cwd=ROOT, env=env,
                          capture_output=True, timeout=timeout, text=True)


def test_lock_is_mutual():
    print('== 锁互斥 ==')
    # 进程 A 拿锁并持有 8 秒
    a_code = """
const { acquireProfileLock } = require('./scripts/profile_lock');
(async () => {
  await acquireProfileLock(5000, 'A');
  console.log('A_LOCKED');
  await new Promise(r => setTimeout(r, 8000));
})();
"""
    a = subprocess.Popen([NODE, '-e', a_code], cwd=ROOT,
                         env=dict(os.environ, NODE_PATH=NODE_PATH),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # 等 A 拿到锁
    locked = False
    for _ in range(20):
        if os.path.isdir(LOCK_DIR):
            locked = True
            break
        time.sleep(0.25)
    check('进程 A 已持有锁', locked)

    # 进程 B 尝试拿锁（5 秒超时）→ 应该超时失败
    b_code = """
const { acquireProfileLock } = require('./scripts/profile_lock');
(async () => {
  try {
    await acquireProfileLock(3000, 'B');
    console.log('B_LOCKED');
  } catch (e) {
    console.log('B_TIMEOUT');
  }
})();
"""
    b = run_node(b_code, timeout=30)
    check('进程 B 被挡住（B_TIMEOUT）', 'B_TIMEOUT' in b.stdout, b.stdout + b.stderr)

    a.terminate()
    try:
        a.wait(timeout=5)
    except Exception:
        a.kill()


def test_lock_released_on_exit():
    print('== 进程退出释放锁 ==')
    code = """
const { acquireProfileLock } = require('./scripts/profile_lock');
(async () => {
  await acquireProfileLock(5000, 'X');
  console.log('LOCKED');
  process.exit(0);
})();
"""
    r = run_node(code, timeout=20)
    check('脚本报告已拿锁', 'LOCKED' in r.stdout, r.stdout + r.stderr)
    time.sleep(0.5)
    # process.exit 会触发 'exit' handler → 释放锁
    check('退出后锁目录已清理', not os.path.isdir(LOCK_DIR),
          str(os.listdir(ROOT + '/data')))


def test_stale_lock_cleaned():
    print('== 陈旧锁自动清理 ==')
    os.makedirs(LOCK_DIR, exist_ok=True)
    with open(os.path.join(LOCK_DIR, 'pid'), 'w') as f:
        f.write('999999 1000000000000')       # 不存在的 pid + 很旧的时间戳
    code = """
const { acquireProfileLock } = require('./scripts/profile_lock');
(async () => {
  await acquireProfileLock(5000, 'S');
  console.log('ACQUIRED');
  process.exit(0);
})();
"""
    r = run_node(code, timeout=20)
    check('陈旧锁被清理并成功获取', 'ACQUIRED' in r.stdout, r.stdout + r.stderr)


def test_manual_flag_readable_by_fetch():
    print('== 人工登录标记 ==')
    sys.path.insert(0, ROOT)
    import importlib.util
    spec = importlib.util.spec_from_file_location('f', os.path.join(ROOT, 'fetch.py'))
    f = importlib.util.module_from_spec(spec)
    sys.argv = ['f']
    spec.loader.exec_module(f)

    check('默认没有人工登录标记', f.manual_login_active() is False)

    os.makedirs(LOCK_DIR, exist_ok=True)
    # 标记文件独立于 LOCK_DIR（锁目录关闭时会被整体删掉）
    manual = os.path.join(ROOT, 'data', '.profile.manual')
    with open(manual, 'w') as fp:
        fp.write(f'{os.getpid()} {int(time.time()*1000)}')
    check('标记写入后 fetch.manual_login_active() 为 True',
          f.manual_login_active() is True)

    os.remove(manual)
    check('标记删除后恢复 False', f.manual_login_active() is False)


def test_all_chromium_scripts_use_lock():
    print('== 脚本接入检查 ==')
    scripts_dir = os.path.join(ROOT, 'scripts')
    missing = []
    checked = []
    for name in sorted(os.listdir(scripts_dir)):
        if not name.endswith('.js'):
            continue
        path = os.path.join(scripts_dir, name)
        src = open(path).read()
        # 起 Chromium 的标志：直接调 launchPersistentContext 或走 launchLocked 包装
        uses_chromium = ('launchPersistentContext' in src) or ('launchLocked(' in src)
        if not uses_chromium:
            continue
        checked.append(name)
        if 'profile_lock' not in src or 'launchLocked' not in src:
            missing.append(name)
    check(f'{len(checked)} 个起 Chromium 的脚本全部接入 profile_lock',
          not missing, f'未接入: {missing}')
    print(f'     起 Chromium 的脚本: {checked}')


def test_profile_lock_module_exports():
    print('== 模块导出 ==')
    code = """
const m = require('./scripts/profile_lock');
const need = ['withProfileLock','acquireProfileLock','releaseProfileLock',
              'launchLocked','setManualLogin','isManualLogin'];
const missing = need.filter(k => typeof m[k] !== 'function');
console.log(missing.length ? 'MISSING:' + missing.join(',') : 'ALL_OK');
"""
    r = run_node(code, timeout=20)
    check('profile_lock 导出齐全', 'ALL_OK' in r.stdout, r.stdout + r.stderr)


if __name__ == '__main__':
    try:
        test_lock_is_mutual()
        test_lock_released_on_exit()
        test_stale_lock_cleaned()
        test_manual_flag_readable_by_fetch()
        test_all_chromium_scripts_use_lock()
        test_profile_lock_module_exports()
    finally:
        # 清理测试残留
        try:
            import shutil
            shutil.rmtree(LOCK_DIR, ignore_errors=True)
            m = os.path.join(ROOT, 'data', '.profile.manual')
            if os.path.exists(m):
                os.remove(m)
        except Exception:
            pass

    print()
    if FAILS:
        print(f'❌ {len(FAILS)} 项失败: {FAILS}')
        sys.exit(1)
    print('✅ 全部通过')
