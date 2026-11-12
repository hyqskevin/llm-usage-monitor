#!/usr/bin/env python3
"""国内站点直连（不走代理）的单元测试。

背景（2026-11-12）：macOS 环境里有常驻的 HTTP_PROXY/HTTPS_PROXY 环境变量
（代理工具写进 shell profile），而 urllib.request.urlopen 默认读这些变量
→ 所有抓取请求被塞进 127.0.0.1:7890。代理一挂，全部站点同时报
Connection refused，看起来像采集器坏了。

实际上这五个平台全在国内，直连更快也不该受代理存活影响。

覆盖：
  - is_direct_domain 对各站点域名的判定
  - 子域名匹配、大小写、非直连域名
  - http() 对直连域名使用无代理 opener（代理挂时仍能发出请求）

运行：
  python3 tests/test_proxy_bypass.py
"""
import importlib.util
import os
import sys
import urllib.request

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


print('== 域名判定 ==')
DIRECT = [
    'https://bigmodel.cn/api/x',
    'https://www.kimi.com/membership/subscription',
    'https://moonshot.cn/v1/chat',
    'https://platform.minimax.cn/console/usage',
    'https://www.minimaxi.com/api',
    'https://platform.deepseek.com/usage',
    'https://platform.xiaomimimo.com/console/plan-manage',
]
for u in DIRECT:
    check('%s 直连' % u.split('/')[2], f.is_direct_domain(u) is True)

print()
print('== 非直连域名 ==')
OTHER = [
    'https://github.com/hyqskevin',
    'https://api.anthropic.com/v1/messages',
    'https://www.kimi.com.evil.com/x',      # 后缀伪装
    'https://notkimi.com/x',
    'https://example.com',
]
for u in OTHER:
    check('%s 不直连' % u.split('/')[2], f.is_direct_domain(u) is False)

print()
print('== 域名解析健壮性 ==')
check('无 scheme 的 URL', f.is_direct_domain('bigmodel.cn/x') is False)
check('空字符串', f.is_direct_domain('') is False)
check('None', f.is_direct_domain(None) is False)

print()
print('== opener 构造 ==')
# build_opener(ProxyHandler({})) 会把默认的 ProxyHandler 整个替换掉
# （默认那个会读环境变量），因此直连 opener 里**不应再有** ProxyHandler。
# 这正是「禁用代理」的实现方式。
direct_opener = f._DIRECT_OPENER
proxy_handlers = [h for h in direct_opener.handlers
                  if isinstance(h, urllib.request.ProxyHandler)]
check('直连 opener 无 ProxyHandler（=不读环境代理）',
      len(proxy_handlers) == 0,
      str([type(h).__name__ for h in direct_opener.handlers]))

# 对照：默认 opener 一定带 ProxyHandler，且会读环境变量
default_opener = urllib.request.build_opener()
default_ph = [h for h in default_opener.handlers
              if isinstance(h, urllib.request.ProxyHandler)]
check('默认 opener 带 ProxyHandler', len(default_ph) == 1)

print()
print('== 代理不可用时直连域名仍可发出请求 ==')
# 用一个必定连不通的代理地址；直连域名应该照样能握手（收到 HTTP 响应即可，
# 401/404 都说明请求到达了服务器，不是连接失败）。
old = {k: os.environ.get(k) for k in
       ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')}
try:
    for k in old:
        os.environ[k] = 'http://127.0.0.1:1'
    # 重新构造模块级 opener（构造时读环境变量）
    f._DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        f.http('GET', 'https://bigmodel.cn/api/paas/v4/chat/completions')
        check('直连域名请求到达服务器', False, '不该 200')
    except urllib.error.HTTPError as e:
        check('直连域名请求到达服务器（HTTP %s，非连接失败）' % e.code,
              True)
    except Exception as e:
        check('直连域名请求到达服务器', False,
              '%s: %s' % (type(e).__name__, str(e)[:80]))
finally:
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

print()
if FAILED:
    print('FAILED: %s' % ', '.join(FAILED))
    sys.exit(1)
print('ALL PASS')
