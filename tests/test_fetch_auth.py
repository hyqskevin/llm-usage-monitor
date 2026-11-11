#!/usr/bin/env python3
"""鉴权判定与凭证指纹的单元测试。

覆盖 2026-11-11 修的两个 bug：
  1. mimo 抛的 'mimo auth failed' 没被 is_auth_error 识别 → 被当成网络错误，
     走错重试分支，最终仍落到 errors['mimo']，菜单显示「抓取失败」。
     症状：点「重新登录」把每站都登一遍，别的站都好了，只有小米一直失败。
  2. token_fingerprint 只查 api-requests.json 的 authorization 头，但
     mimo / minimax 用的是 session cookie → 指纹恒为 None →
     refresh_auth_for 永远返回 False → 自动刷新路径形同虚设。

运行：
  python3 tests/test_fetch_auth.py
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('fetch', os.path.join(ROOT, 'fetch.py'))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)

FAILED = []


def check(name, cond, detail=''):
    if cond:
        print('  PASS  %s' % name)
    else:
        print('  FAIL  %s  %s' % (name, detail))
        FAILED.append(name)


def test_is_auth_error():
    print('test_is_auth_error')
    # mimo 的鉴权失败必须被识别为鉴权错误（bug #1）
    check('mimo auth failed → True',
          f.is_auth_error(Exception('mimo auth failed: {"code":401}')))
    check('mimo auth failed 大小写不敏感',
          f.is_auth_error(Exception('MIMO AUTH FAILED: xxx')))
    check('HTTP error 401 → True',
          f.is_auth_error(Exception('HTTP error 401')))
    check('HTTP error 403 → True',
          f.is_auth_error(Exception('HTTP error 403')))
    check('unauthorized → True',
          f.is_auth_error(Exception('unauthorized')))
    # 非鉴权错误不能误判
    check('缺字段 → False',
          not f.is_auth_error(Exception('mimo tokenPlan/usage 缺 plan_total_token')))
    check('文件不存在 → False',
          not f.is_auth_error(Exception('mimo-session.json 不存在')))
    check('网络超时 → False',
          not f.is_auth_error(Exception('timed out')))


def test_token_fingerprint():
    print('test_token_fingerprint')
    reqs = f.load_auth()[1]
    # 五站都必须有指纹（bug #2）
    for site in ('bigmodel', 'kimi', 'minimax', 'mimo', 'deepseek'):
        fp = f.token_fingerprint(reqs, site)
        check('%s 有指纹' % site, fp is not None, str(fp))
        if fp is not None:
            check('%s 指纹是 10 位短哈希' % site, len(fp) == 10, fp)

    # 指纹必须稳定：同样输入两次结果一致
    a = f.token_fingerprint(reqs, 'mimo')
    b = f.token_fingerprint(reqs, 'mimo')
    check('mimo 指纹稳定', a == b, '%s vs %s' % (a, b))

    # 未知站点返回 None（不是抛异常）
    check('未知站点 → None', f.token_fingerprint(reqs, 'nope') is None)

    # 指纹不能泄露原文凭证
    check('mimo 指纹不含原文',
          'serviceToken' not in (a or ''))


def test_refresh_auth_for():
    print('test_refresh_auth_for')
    # refresh_auth_for 对 mimo 不能因为「指纹为 None」而恒返回 False。
    # 这里不真跑刷新脚本（要起浏览器），只验证前置判定逻辑：
    # 既然 mimo 现在有指纹，before != None，判定才可能走到 after 比较。
    reqs = f.load_auth()[1]
    before = f.token_fingerprint(reqs, 'mimo')
    check('mimo 刷新前指纹可用（不再为 None）', before is not None, str(before))


if __name__ == '__main__':
    test_is_auth_error()
    test_token_fingerprint()
    test_refresh_auth_for()
    print()
    if FAILED:
        print('FAILED: %d' % len(FAILED))
        for x in FAILED:
            print('  - %s' % x)
        sys.exit(1)
    print('ALL PASSED')
