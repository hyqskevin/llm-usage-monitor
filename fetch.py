#!/usr/bin/env python3
"""LLM 用量采集器：直接 HTTP 调三站点 API（无浏览器），写入 SQLite。

依赖文件（由 scripts/dump_cookies.js 与 scripts/discover_api.js 生成）：
  data/cookies.json        —— minimax 用 cookie 鉴权
  data/api-requests.json   —— bigmodel / kimi 的 authorization 等请求头

用法:
  python3 fetch.py            # 采集一次并打印摘要
  python3 fetch.py --json     # 只输出最新一行 JSON（供 SwiftBar）
"""
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'data', 'usage.db')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')

WINDOWS = ('5h', 'week', 'billing')

# 国内站点直连，不走系统代理。
#
# 背景：macOS 环境里常驻 HTTP_PROXY/HTTPS_PROXY 环境变量（Stash 等代理工具
# 会写入 shell profile），而 urllib.request.urlopen 默认读取这些环境变量
# → 所有抓取请求都被塞进 127.0.0.1:7890。代理一挂，全部站点同时报
# "Connection refused"，看起来像采集器坏了。
#
# 实际上这些平台全部在国内，直连更快也不该受代理存活影响。
# 做法：自建 opener。国内域名用 ProxyHandler({}) 明确禁用代理；
# 其余域名仍走环境代理（登录/刷新脚本需要访问境外资源时不影响）。
DIRECT_DOMAINS = (
    'bigmodel.cn',
    'kimi.com',
    'moonshot.cn',
    'minimax.cn',
    'minimaxi.com',
    'deepseek.com',
    'xiaomimimo.com',
)

# 不带代理的 opener（ProxyHandler({}) = 明确不走任何代理）
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_direct_domain(url):
    """该 URL 是否属于强制直连的国内站点。"""
    try:
        host = urllib.parse.urlsplit(url).hostname or ''
    except ValueError:
        return False
    host = host.lower()
    return any(host == d or host.endswith('.' + d) for d in DIRECT_DOMAINS)


def http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'User-Agent': UA, **(headers or {})})
    opener = _DIRECT_OPENER if is_direct_domain(url) else urllib.request.build_opener()
    with opener.open(req, timeout=25) as r:
        return json.loads(r.read().decode())


def load_auth():
    cookies = json.load(open(os.path.join(ROOT, 'data', 'cookies.json')))['cookies']
    reqs = json.load(open(os.path.join(ROOT, 'data', 'api-requests.json')))
    return cookies, reqs


def refresh_auth():
    """鉴权过期时用无头浏览器从持久化 profile 重新导出 cookies 与请求头。

    2026-09-24 修：原先子进程输出被丢弃、returncode 不检查，导致「浏览器二进制缺失」
    与「token 过期」报同一个错，排查时无法区分。现在失败会打印 stderr 尾部。
    返回失败脚本名列表（空列表 = 全部成功）。
    """
    import subprocess
    env = dict(os.environ,
               NODE_PATH='/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules')
    node = '/opt/homebrew/bin/node'
    failed = []
    # 2026-11-12：人工登录进行中 → 不要抢 browser-profile。
    # 所有 Playwright 脚本共用同一个 user-data-dir，第二个实例会被 Chrome
    # 判定为「重复启动」并立即退出（"正在现有的浏览器会话中打开"），
    # 导致人工登录脚本和后台采集**双双崩溃**。人工登录最长 15 分钟，
    # 这期间直接跳过刷新，用现有凭证采集。
    if manual_login_active():
        print('  ⏸ 人工登录进行中，跳过 Playwright 刷新（用现有凭证采集）',
              file=sys.stderr)
        return failed
    # minimax_refresh_auth.js 是非交互的（复用已登录 profile 刷新会话），
    # 若 profile 登录态已失效它会 exit 2 并提示需人工登录。
    for s in ('dump_cookies.js', 'discover_api.js', 'mimo_refresh_session.js',
              'minimax_refresh_auth.js'):
        try:
            p = subprocess.run([node, os.path.join(ROOT, 'scripts', s)],
                               cwd=ROOT, env=env, capture_output=True, timeout=120)
            if p.returncode != 0:
                tail = p.stderr.decode(errors='replace').strip()[-400:]
                print(f'  ⚠️ refresh_auth: {s} 退出码 {p.returncode}', file=sys.stderr)
                if tail:
                    print(f'     {tail}', file=sys.stderr)
                # 浏览器二进制缺失/版本错配是最常见的根因，给出可执行的修复命令
                if "Executable doesn't exist" in tail or 'playwright install' in tail:
                    print('     ↳ Playwright 浏览器版本与库不匹配，修复命令：', file=sys.stderr)
                    print('       cd ~/.workbuddy/binaries/node/workspace && '
                          'node node_modules/playwright-core/cli.js install chromium', file=sys.stderr)
                failed.append(s)
        except Exception as e:
            print(f'  ⚠️ refresh_auth: {s} 执行异常 {e}', file=sys.stderr)
            failed.append(s)
    return failed


def manual_login_active():
    """人工登录是否进行中（scripts/login.js 打的标记）。

    2026-11-12：所有 Playwright 脚本共用同一个 browser-profile 目录，
    Chromium 用 SingletonLock 保证「一个 user-data-dir 同时只能有一个实例」。
    人工登录（login.js，最长 15 分钟）与后台采集（discover_api.js 等）
    同时启动时，后启动的实例会被 Chrome 判定为重复启动：
        [out] 正在现有的浏览器会话中打开。
    然后进程立刻退出，Playwright 抛「browser has been closed」。
    → 人工登录和后台采集**双双崩溃**。

    login.js 会写 data/.profile.manual 标记，这里读到就跳过
    所有 Playwright 步骤，只用现有凭证采集。
    """
    try:
        return os.path.exists(os.path.join(ROOT, 'data', '.profile.manual'))
    except Exception:
        return False


def cookie_header(cookies, domain):
    return '; '.join(f"{c['name']}={c['value']}" for c in cookies.get(domain, []))

def pick_headers(req, keys):
    return {k: v for k, v in req['headers'].items() if k in keys}


# ---------- 鉴权预检 ----------

# 各站点「鉴权失效」的判定依据：接口返回 HTTP 200 但 body 里带 code/msg 错误，
# 或直接 401/403。命中即视为 token 失效，不再重试刷新（刷新对已失效会话无效）。
#
# 2026-11-11 修：mimo 之前漏判。fetch_mimo 抛的是
#   RuntimeError(f"mimo auth failed: {resp}")
# 而 'mimo auth failed' 不在白名单里 → is_auth_error 返回 False →
# mimo 被当成「网络/解析错误」走 refresh_auth_for 重试分支，而
# token_fingerprint 对 mimo 恒为 None（mimo 不用 authorization 头，
# 用的是 session cookie），于是 refresh_auth_for 永远返回 False，
# 最终仍落到 errors['mimo']，菜单显示「抓取失败」。
# 症状：点「重新登录」把每站都登了一遍，别的站都好了，只有小米一直失败。
AUTH_ERROR_CODES = ('登录状态已过期', 'unauthorized', 'token expired', '登录已失效',
                    'mimo auth failed')


def is_auth_error(exc):
    """判断异常是否属于「鉴权失效」而非「网络/解析异常」。"""
    msg = str(exc).lower()
    if 'http error 401' in msg or 'http error 403' in msg:
        return True
    return any(k.lower() in msg for k in AUTH_ERROR_CODES)


def token_fingerprint(reqs, site):
    """取某站点当前凭证的指纹，用于判断「刷新是否真的换了新 token」。

    返回短哈希而非原文，避免把凭证写进日志。

    2026-11-11 修：原先只读 api-requests.json 的 authorization 头，
    但不同站点的鉴权来源不同：
      - bigmodel / kimi → authorization 头（api-requests.json）
      - deepseek → 会话 cookie（data/cookies.json 的 deepseek.com）
      - minimax → data/minimax-auth.json 的 _token cookie
      - mimo → data/mimo-session.json 的 session cookie
        （api-platform_serviceToken / api-platform_ph）
    mimo 走 authorization 路线恒得 None → refresh_auth_for 永远返回 False
    → mimo 的自动刷新路径形同虚设。这里按站点取对应凭证。
    """
    import hashlib
    if site == 'mimo':
        return _mimo_fingerprint()
    if site == 'minimax':
        return _minimax_fingerprint()
    if site == 'deepseek':
        return _deepseek_fingerprint()
    try:
        auth = reqs[site][0]['headers'].get('authorization', '')
    except (KeyError, IndexError, TypeError):
        return None
    if not auth:
        return None
    return hashlib.sha1(auth.encode()).hexdigest()[:10]


def _mimo_fingerprint():
    """mimo 的凭证指纹：session cookie（serviceToken + ph）。"""
    import hashlib
    sess_path = os.path.join(ROOT, 'data', 'mimo-session.json')
    try:
        sess = json.load(open(sess_path))
        cookies = sess.get('cookies', {}).get('xiaomimimo.com', [])
    except (OSError, ValueError, KeyError):
        return None
    pairs = []
    for c in cookies:
        if c.get('name') in ('api-platform_serviceToken', 'api-platform_ph'):
            pairs.append('%s=%s' % (c['name'], c['value']))
    if not pairs:
        return None
    return hashlib.sha1('|'.join(sorted(pairs)).encode()).hexdigest()[:10]


def _minimax_fingerprint():
    """minimax 的凭证指纹：会话 cookie（_token）。

    minimax 同样不用 authorization 头（实测 minimax-auth.json 的
    auth_headers 为空、requests[].headers 里也没有 authorization），
    真正起作用的是 cookie 里的 _token。
    """
    import hashlib
    path = os.path.join(ROOT, 'data', 'minimax-auth.json')
    try:
        d = json.load(open(path))
        cookies = d.get('cookies', [])
    except (OSError, ValueError, AttributeError):
        return None
    pairs = ['%s=%s' % (c.get('name'), c.get('value'))
             for c in cookies if c.get('name') == '_token']
    if not pairs:
        return None
    return hashlib.sha1('|'.join(sorted(pairs)).encode()).hexdigest()[:10]



def _deepseek_fingerprint():
    """deepseek 的凭证指纹：会话 cookie。

    deepseek 的内部 API 用 cookie 鉴权，api-requests.json 里的
    deepseek 请求头**没有** authorization（实测只有 x-client-* 等），
    所以走 authorization 路线恒得 None。
    """
    import hashlib
    try:
        d = json.load(open(os.path.join(ROOT, 'data', 'cookies.json')))
        cookies = d.get('cookies', {}).get('deepseek.com', [])
    except (OSError, ValueError, KeyError, AttributeError):
        return None
    pairs = []
    for c in cookies:
        name = c.get('name')
        if name and c.get('value'):
            pairs.append('%s=%s' % (name, c['value']))
    if not pairs:
        return None
    return hashlib.sha1('|'.join(sorted(pairs)).encode()).hexdigest()[:10]


def refresh_auth_for(site, reqs):
    """对单个站点尝试刷新鉴权。

    2026-11-10 修：原实现对所有站点统一跑一遍 refresh_auth()（4 个 Playwright 脚本），
    但每个站点的鉴权来源不同——bigmodel/kimi 靠 api-requests.json 的 authorization，
    minimax 靠 minimax-auth.json，mimo 靠 mimo-session.json。
    全量刷新既慢（每次起 4 个浏览器）又可能用「同样失效的旧 token」覆盖新值。

    这里改为按站点最小化刷新，并校验刷新前后的 token 指纹：
      - 指纹变化 → 刷新成功
      - 指纹不变 → 说明刷新生效但拿到同一个（仍失效的）token，不算成功
    """
    import subprocess
    env = dict(os.environ,
               NODE_PATH='/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules')
    node = '/opt/homebrew/bin/node'
    # 2026-11-12：人工登录进行中 → 不能抢 browser-profile，跳过刷新。
    if manual_login_active():
        print(f'  ⏸ 人工登录进行中，跳过 {site} 的鉴权刷新', file=sys.stderr)
        return False
    # 站点 → 负责刷新其鉴权的脚本
    SCRIPTS = {
        'bigmodel': ['discover_api.js'],      # 抓 bigmodel 的 authorization
        'kimi': ['discover_api.js'],           # 抓 kimi 的 authorization（同脚本含两站）
        'minimax': ['minimax_refresh_auth.js'],
        'mimo': ['mimo_refresh_session.js'],
        'deepseek': ['deepseek_discover.js'],
    }
    before = token_fingerprint(reqs, site)
    for s in SCRIPTS.get(site, []):
        try:
            p = subprocess.run([node, os.path.join(ROOT, 'scripts', s)],
                               cwd=ROOT, env=env, capture_output=True, timeout=120)
            if p.returncode != 0:
                tail = p.stderr.decode(errors='replace').strip()[-200:]
                print(f'  ⚠️ 刷新 {site}: {s} 退出码 {p.returncode}'
                      + (f' | {tail}' if tail else ''), file=sys.stderr)
        except Exception as e:
            print(f'  ⚠️ 刷新 {site}: {s} 执行异常 {e}', file=sys.stderr)
    after = token_fingerprint(load_auth()[1], site)
    if after is None:
        return False
    if before is not None and after == before:
        # 刷新没换出新 token —— 多半是会话本身已失效，必须人工重登
        print(f'  ⚠️ {site} 刷新后 token 未变化（指纹 {after}），会话可能已彻底失效',
              file=sys.stderr)
        return False
    return True


# ---------- 各站点解析 ----------

def fetch_bigmodel(reqs):
    r = reqs['bigmodel'][0]
    h = pick_headers(r, ('authorization', 'bigmodel-organization', 'bigmodel-project'))
    resp = http('GET', r['url'], h)
    if resp.get('code') != 200:
        # 2026-11-10：尝试从用户日常 Chrome 导入的鉴权材料兜底。
        # 智谱是双因子鉴权——authorization(JWT) + 605ea18895037d16 会话 cookie，
        # 缺任一都 401。只靠脚本专用 browser-profile 抓包时，两者是割裂的，
        # 而用户日常 Chrome 往往一直是登录态。
        chrome = _bigmodel_from_chrome(r)
        if chrome is not None:
            return chrome
        raise RuntimeError(f"bigmodel auth failed: {resp.get('msg')}")
    return _parse_bigmodel_limits(resp)


def _parse_bigmodel_limits(resp):
    out = []
    for lim in resp['data']['limits']:
        # unit=3 按小时(number=5→5h), unit=6 按周(number=1→week)
        if lim['unit'] == 3:
            window, reset = '5h', None
        elif lim['unit'] == 6:
            window = 'week'
            reset = lim.get('nextResetTime')
        else:
            continue
        out.append({
            'window': window,
            'used_pct': float(lim['percentage']),
            'used': float(lim['currentValue']),
            'total': float(lim['usage']),
            'remaining': float(lim['remaining']),
            'reset_at': datetime.fromtimestamp(reset / 1000, tz=timezone.utc).isoformat() if reset else None,
        })
    return out


def _bigmodel_from_chrome(req):
    """用 Chrome 导入的鉴权材料抓 bigmodel 用量；不可用时返回 None。

    数据来源：data/chrome_auth.json（由 scripts/chrome_cookie_import.py 生成）。
    若该文件不存在或内容已过期，尝试现场重新导入一次。
    """
    import subprocess
    path = os.path.join(ROOT, 'data', 'chrome_auth.json')
    payload = None
    for attempt in range(2):
        try:
            with open(path) as f:
                payload = json.load(f)
            break
        except Exception:
            payload = None
            if attempt == 0:
                # 现场从 Chrome 导入一次（会读 Keychain，可能弹授权框）
                try:
                    subprocess.run(
                        [sys.executable, os.path.join(ROOT, 'scripts', 'chrome_cookie_import.py')],
                        cwd=ROOT, capture_output=True, timeout=120)
                except Exception:
                    pass
    if not payload:
        return None
    cookies = payload.get('cookies') or {}
    token = cookies.get('bigmodel_token_production')
    if not token:
        return None

    headers = pick_headers(req, ('bigmodel-organization', 'bigmodel-project',
                                 'referer', 'accept', 'accept-language'))
    headers['authorization'] = token
    headers['cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
    try:
        resp = http('GET', req['url'], headers)
    except Exception as e:
        print(f'  ⚠️ bigmodel Chrome 兜底失败: {e}', file=sys.stderr)
        return None
    if resp.get('code') != 200:
        print(f"  ⚠️ bigmodel Chrome 兜底也被拒: {resp.get('msg')}", file=sys.stderr)
        return None
    print('  ✅ bigmodel 已用 Chrome 登录态兜底成功', file=sys.stderr)
    return _parse_bigmodel_limits(resp)


def fetch_kimi(reqs):
    """返回 (kimi_rows, kimi_code_rows)：kimi=整体订阅额度，kimi-code=编程额度"""
    r = reqs['kimi'][0]
    h = pick_headers(r, ('authorization', 'x-msh-session-id', 'x-msh-device-id',
                         'x-msh-platform', 'x-msh-version', 'connect-protocol-version',
                         'content-type', 'x-language', 'r-timezone'))
    resp = http('POST', r['url'], h, {})
    if 'ratelimitCode5h' not in resp:
        raise RuntimeError(f'kimi auth failed: {str(resp)[:120]}')
    kimi, code = [], []
    rl5, rl7 = resp['ratelimitCode5h'], resp['ratelimitCode7d']
    # 用量为 0（窗口刚重置）时 Kimi 会省略 ratio 字段，缺失按 0 处理
    code.append({'window': '5h', 'used_pct': rl5.get('ratio', 0.0) * 100,
                 'used': None, 'total': None, 'remaining': None,
                 'reset_at': rl5.get('resetTime')})
    code.append({'window': 'week', 'used_pct': rl7.get('ratio', 0.0) * 100,
                 'used': None, 'total': None, 'remaining': None,
                 'reset_at': rl7.get('resetTime')})
    bal = resp.get('subscriptionBalance') or {}
    if 'amountUsedRatio' in bal:
        kimi.append({'window': 'billing', 'used_pct': bal['amountUsedRatio'] * 100,
                     'used': None, 'total': None, 'remaining': None,
                     'reset_at': bal.get('expireTime')})
    if 'kimiCodeUsedRatio' in bal:
        code.append({'window': 'billing', 'used_pct': bal['kimiCodeUsedRatio'] * 100,
                     'used': None, 'total': None, 'remaining': None,
                     'reset_at': bal.get('expireTime')})
    return kimi, code


def fetch_minimax(cookies):
    """MiniMax Token Plan 用量。

    鉴权是「登录会话 cookie + 额外 API key 头」两层，缺任一层都会失败，
    但两层的错误码不同（2026-10-23 实测）：
      1004 cookie is missing, log in again  → 缺登录会话 cookie
      1016 invalid api key                    → 缺额外 API key 头

    cookie 优先取 data/minimax-auth.json（scripts/minimax_login.js 登录后落盘，
    含完整 cookie 与鉴权头），缺失时回退到 data/cookies.json 的域分组。
    """
    ck, extra_headers = _minimax_auth(cookies)
    h = {'Cookie': ck, 'Accept': 'application/json',
         'Referer': 'https://platform.minimax.cn/console/usage'}
    h.update(extra_headers)

    resp = http('GET', 'https://www.minimax.cn/backend/account/token_plan/remains_percent', h)
    code = resp.get('base_resp', {}).get('status_code')
    if code != 0:
        msg = resp.get('base_resp', {}).get('status_msg', '')
        if code == 1004:
            raise RuntimeError('minimax 未登录（cookie 缺失），需重新登录: node scripts/minimax_login.js')
        if code == 1016:
            raise RuntimeError('minimax 缺少 API key 头，需重新登录抓取鉴权: node scripts/minimax_login.js')
        raise RuntimeError(f"minimax auth failed: {str(resp)[:120]}")

    out = []
    models = resp.get('model_remains') or []
    if models:
        m = models[0]  # general
        pct5 = m.get('current_interval_used_percent', '').rstrip('%')
        out.append({
            'window': '5h',
            'used_pct': float(pct5) if pct5 else None,
            'used': None, 'total': None, 'remaining': None,
            'reset_at': datetime.fromtimestamp(m['end_time'] / 1000, tz=timezone.utc).isoformat()
                        if m.get('end_time') else None,
        })
        wpct = m.get('current_weekly_used_percent', '').rstrip('%')
        # MiniMax 高级订阅周限额 = -1 表示无限制
        weekly_unlimited = m.get('current_weekly_total_count') == -1
        if wpct:
            out.append({
                'window': 'week',
                'used_pct': float(wpct),
                'used': None, 'total': None, 'remaining': None,
                'unlimited': weekly_unlimited,
                'reset_at': datetime.fromtimestamp(m['weekly_end_time'] / 1000, tz=timezone.utc).isoformat()
                            if m.get('weekly_end_time') else None,
            })
    # 月度：用 usage_summary 的 daily_token_usage 聚合当月消耗
    try:
        s = http('GET', 'https://www.minimax.cn/backend/account/token_plan/usage_summary', h)
        daily = s.get('daily_token_usage') or []
        if daily:
            month_tokens = sum(daily[-30:])
            out.append({'window': 'billing', 'used_pct': None, 'used': float(month_tokens),
                        'total': None, 'remaining': None, 'reset_at': None})
    except Exception:
        pass
    return out


def _minimax_auth(cookies):
    """返回 (cookie 串, 额外鉴权头)。

    优先 data/minimax-auth.json（登录脚本落盘，含鉴权头），
    回退 data/cookies.json 的 minimax.cn / minimaxi.com 域分组。
    """
    auth_path = os.path.join(ROOT, 'data', 'minimax-auth.json')
    if os.path.exists(auth_path):
        try:
            auth = json.load(open(auth_path))
            cs = auth.get('cookies') or []
            if cs:
                ck = '; '.join(f"{c['name']}={c['value']}" for c in cs)
                return ck, (auth.get('auth_headers') or {})
        except Exception:
            pass
    ck = (cookie_header(cookies, 'minimax.cn') or cookie_header(cookies, 'minimaxi.com'))
    return ck, {}


# load_budget 已废弃：DeepSeek 区块去掉预算逻辑，账户余额直接展示。
# 旧函数保留注释避免后续误删：
# def load_budget(): ... (2026-09-15 21:15 移除)


def fetch_deepseek(reqs):
    """DeepSeek 用量：充值余额 / 当日消费金额 / 当日 token。

    实现方式：调子进程跑 scripts/deepseek_discover.js —— Playwright 复用已登录 profile
    (含 localStorage isAuthenticated:true)，从 platform.deepseek.com 三个内部 API 抓数据：
      - /api/v0/users/get_user_summary        → 充值余额 + 累计消费
      - /api/v0/usage/by_api_key/amount       → 当日 token
      - /api/v0/usage/by_api_key/cost         → 当日消费金额
    解析后写到 data/deepseek-rows.json，fetch.py 读这个文件。

    返回 rows：
      [
        {'site': 'deepseek', 'window': 'billing', 'used': 累计消费, 'total': 充值总额,
         'remaining': 余额, 'used_pct': None, 'reset_at': None, 'unlimited': False,
         'meta': {'monthly_budget_cny': 50.0}},
        {'site': 'deepseek', 'window': 'today',   'used': 当日消费, 'total': None,
         'remaining': None, 'used_pct': None, 'reset_at': None, 'unlimited': False,
         'meta': {'today_tokens': N, 'monthly_budget_cny': 50.0}},
      ]
    """
    import subprocess
    rows_path = os.path.join(ROOT, 'data', 'deepseek-rows.json')

    # 注意：不依赖 reqs['deepseek'] —— discover_api.js 在鉴权失败被自动重跑时会覆盖 deepseek 数组为 []，
    # 但 deepseek 的真实抓包由 deepseek_discover.js 自己负责，子进程通过 Playwright persistent context
    # 复用登录态（localStorage isAuthenticated:true），不依赖 api-requests.json 里的 deepseek 数组。

    # 2026-11-12 加缓存：deepseek_discover.js 每次要起 Playwright（30~120 秒），
    # 而 deepseek 的数据是「余额 + 当日消费」，分钟级精度没意义。
    # 原实现每轮采集都跑一遍 → 采集周期恒定 30 秒以上，还把浏览器资源占满。
    # 这里改成：结果文件在 DEEPSEEK_CACHE_TTL 内就直接复用。
    DEEPSEEK_CACHE_TTL = 600      # 10 分钟
    try:
        age = time.time() - os.path.getmtime(rows_path)
        if age < DEEPSEEK_CACHE_TTL:
            payload = json.load(open(rows_path))
            cached_rows = [{'site': 'deepseek', **r} for r in payload.get('rows', [])]
            if cached_rows:
                return cached_rows
    except Exception:
        pass

    env = dict(os.environ,
               NODE_PATH='/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules')
    node = '/opt/homebrew/bin/node'
    # 2026-11-12：人工登录进行中 → 不能抢 browser-profile。
    # 缓存已过期也没关系，人工登录结束后下一轮会重新采集。
    if manual_login_active():
        raise RuntimeError('人工登录进行中，跳过 deepseek 采集（避免抢 browser-profile）')
    proc = subprocess.run(
        [node, os.path.join(ROOT, 'scripts', 'deepseek_discover.js')],
        cwd=ROOT, env=env, capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f'deepseek_discover.js 退出码 {proc.returncode}: '
                           f'{proc.stderr.decode(errors="replace")[-500:]}')
    if not os.path.exists(rows_path):
        raise RuntimeError('deepseek_discover.js 未生成 deepseek-rows.json')

    payload = json.load(open(rows_path))
    out_rows = []
    for r in payload.get('rows', []):
        out_rows.append({'site': 'deepseek', **r})
    if payload.get('errors'):
        import warnings
        warnings.warn('deepseek 部分失败: ' + '; '.join(payload['errors']))
    if not out_rows:
        raise RuntimeError('deepseek rows 为空: ' + '; '.join(payload.get('errors', [])))
    return out_rows


def fetch_mimo():
    """MiMo 套餐用量：账期 Credits 用量 + 当日 tokens/请求数（纯 HTTP，cookie 鉴权）。

    鉴权链路（2026-10-13 踩坑记录）：
      - platform.xiaomimimo.com 的会话 cookie（api-platform_serviceToken 等）是
        session 级、不落盘；由持久化 profile 里的长效 passToken（.account.xiaomi.com）
        经 /sts 握手签发。scripts/mimo_refresh_session.js 负责握手并导出
        data/mimo-session.json，过期后 refresh_auth() 会自动重新签发。
      - POST /api/v1/usage/token-plan/list 必须带 ?api-platform_ph=<值>，且该值要
        用 cookie 原值「去掉字面引号」后再 URL 编码——cookie 里存的值自带双引号，
        直接拼 URL 会稳定 401（loginUrl 里能看到 %2522 的痕迹）。

    返回 rows：
      [
        {'site': 'mimo', 'window': 'billing', 'used_pct': 账期已用%, 'used': 已用 credits,
         'total': 套餐月总量, 'remaining': 剩余, 'reset_at': 账期结束时间,
         'meta': {'plan': ..., 'auto_renew': ..., 'month_pct': 当月已用%}},
        {'site': 'mimo', 'window': 'today', 'used': 当日 tokens, 'used_pct': None,
         'meta': {'today_tokens': N, 'today_requests': N, 'models': [...]}},
      ]
    """
    import urllib.parse
    from datetime import timedelta

    sess_path = os.path.join(ROOT, 'data', 'mimo-session.json')
    if not os.path.exists(sess_path):
        raise RuntimeError('mimo-session.json 不存在，请运行: node scripts/mimo_refresh_session.js')
    sess = json.load(open(sess_path))
    cookies = sess.get('cookies', {}).get('xiaomimimo.com', [])
    ck = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
    ph_raw = next((c['value'] for c in cookies if c['name'] == 'api-platform_ph'), None)
    if not ck or ph_raw is None:
        raise RuntimeError('mimo 会话 cookie 缺失（无 serviceToken/ph）')

    base = 'https://platform.xiaomimimo.com'
    h = {'Cookie': ck, 'Accept': 'application/json', 'content-type': 'application/json',
         'x-timezone': 'Asia/Shanghai', 'accept-language': 'zh',
         'referer': f'{base}/console/plan-manage?edition=PERSONAL'}

    def api(method, path, body=None):
        resp = http(method, base + path, h, body)
        if resp.get('code') != 0:
            raise RuntimeError(f"mimo auth failed: {str(resp)[:120]}")
        return resp.get('data')

    usage = api('GET', '/api/v1/tokenPlan/usage')
    detail = api('GET', '/api/v1/tokenPlan/detail') or {}

    out = []
    items = {i['name']: i for i in (usage.get('usage') or {}).get('items', [])}
    plan = items.get('plan_total_token')
    if not plan or not plan.get('limit'):
        raise RuntimeError(f'mimo tokenPlan/usage 缺 plan_total_token: {str(usage)[:120]}')
    used, limit = float(plan['used']), float(plan['limit'])

    reset_at = None
    if detail.get('currentPeriodEnd'):
        # 页面给的是北京时间字符串 "2026-11-13 23:59:59"
        reset_at = detail['currentPeriodEnd'].replace(' ', 'T') + '+08:00'
    month = (usage.get('monthUsage') or {}).get('percent')
    comp = items.get('compensation_total_token') or {}
    out.append({
        'window': 'billing',
        'used_pct': used / limit * 100 if limit else None,
        'used': used, 'total': limit, 'remaining': limit - used,
        'reset_at': reset_at,
        'meta': {'plan': detail.get('planName'),
                 'auto_renew': bool(detail.get('enableAutoRenew')),
                 'month_pct': month * 100 if month is not None else None,
                 'compensation_used': comp.get('used'), 'compensation_limit': comp.get('limit')},
    })

    # 当日用量：POST usage/token-plan/list 按 (date, model) 返回每日明细
    ph = ph_raw.strip('"')
    now_local = datetime.now(timezone(timedelta(hours=8)))
    daily = api('POST', '/api/v1/usage/token-plan/list?' + urllib.parse.urlencode({'api-platform_ph': ph}),
                {'year': now_local.year, 'month': now_local.month}) or []
    today_str = now_local.strftime('%Y-%m-%d')
    todays = [d for d in daily if d.get('date') == today_str]
    out.append({
        'window': 'today',
        'used_pct': None,
        'used': float(sum(d.get('totalToken') or 0 for d in todays)),
        'total': None, 'remaining': None, 'reset_at': None,
        'meta': {'today_tokens': sum(d.get('totalToken') or 0 for d in todays),
                 'today_requests': sum(d.get('requestCount') or 0 for d in todays),
                 'models': sorted({d.get('model') for d in todays if d.get('model')})},
    })
    return out


# ---------- 并发保护 ----------

def _acquire_lock():
    """非阻塞排他锁，防止采集任务重叠执行。

    背景（2026-09-24 踩坑）：SwiftBar 每 10 分钟触发一次 fetch.py，而抓 deepseek /
    刷新 kimi 鉴权都要起 Playwright 实例。若上一轮尚未结束，两个 Chromium 会同时
    读写同一个 browser-profile，导致「请求头丢失、API 响应缺失」，表现为随机站点失败。

    用 fcntl.flock：进程退出（含被 kill / 崩溃）时由内核自动释放，不会留下死锁文件。
    返回值需在调用方作用域内保持引用，否则文件对象被回收会提前释放锁。
    """
    import fcntl
    lock_path = os.path.join(ROOT, 'data', '.fetch.lock')
    fp = open(lock_path, 'w')
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return None
    fp.write(str(os.getpid()))
    fp.flush()
    return fp


# ---------- 存储 ----------

def init_db(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        site TEXT NOT NULL,
        window TEXT NOT NULL,
        used_pct REAL, used REAL, total REAL, remaining REAL, reset_at TEXT,
        unlimited INTEGER DEFAULT 0
    )''')
    # 兼容旧表：如果 unlimited / meta 列不存在则加上
    cols = {r[1] for r in conn.execute('PRAGMA table_info(snapshots)').fetchall()}
    if 'unlimited' not in cols:
        conn.execute('ALTER TABLE snapshots ADD COLUMN unlimited INTEGER DEFAULT 0')
    if 'meta' not in cols:
        conn.execute('ALTER TABLE snapshots ADD COLUMN meta TEXT')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON snapshots(ts)')


def main():
    json_only = '--json' in sys.argv

    # 防并发重叠：拿不到锁说明另一轮采集正在跑，直接跳过本轮。
    lock = _acquire_lock()
    if lock is None:
        if json_only:
            # 注意：--json 模式必须保持 stdout 干净——SwiftBar 插件靠「输出为空」回退到
            # data/latest.json；若这里往 stdout 写字，插件会拿到非 JSON 文本而报错。
            print('另一轮采集正在进行，跳过本次', file=sys.stderr)
        else:
            print('另一轮采集正在进行，跳过本次')
        return

    cookies, reqs = load_auth()
    ts = datetime.now(tz=timezone.utc).isoformat()
    rows, errors = [], {}

    def collect_kimi():
        kimi_rows, code_rows = fetch_kimi(reqs)
        return [{'site': 'kimi', **i} for i in kimi_rows] + \
               [{'site': 'kimi-code', **i} for i in code_rows]

    # 2026-11-10 改造：原实现用单个 `refreshed` 标志位，任一站点失败就对
    # **所有**站点跑一遍全量 refresh_auth()（4 个 Playwright 脚本，~40 秒），
    # 且刷新后不复核结果——于是「会话已彻底失效」时每轮都白跑一遍浏览器，
    # 并把同样失效的旧 token 继续写回，形成永久性失败循环。
    #
    # 新逻辑：
    #   1. 只对失败的那个站点做最小化刷新（见 refresh_auth_for）
    #   2. 用 token 指纹校验刷新是否真的换了新 token
    #   3. 鉴权类错误不重试（重试无意义，直接报「需人工重登」）
    #   4. 连续多轮鉴权失败的站点进入熔断，跳过昂贵的浏览器刷新
    DEAD_AUTH_TTL = 3600          # 熔断状态保存时长
    DEAD_AUTH_THRESHOLD = 3       # 连续失败几轮后熔断
    dead_path = os.path.join(ROOT, 'data', '.dead_auth.json')

    def load_dead():
        try:
            with open(dead_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def save_dead(d):
        try:
            with open(dead_path, 'w') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    dead = load_dead()
    now_ts = time.time()
    # 清理过期熔断记录
    dead = {k: v for k, v in dead.items() if now_ts - v.get('at', 0) < DEAD_AUTH_TTL}

    for site, fn in (('bigmodel', lambda: [{'site': 'bigmodel', **i} for i in fetch_bigmodel(reqs)]),
                     ('kimi', collect_kimi),
                     ('minimax', lambda: [{'site': 'minimax', **i} for i in fetch_minimax(cookies)]),
                     ('mimo', lambda: [{'site': 'mimo', **i} for i in fetch_mimo()]),
                     ('deepseek', lambda: [{'site': 'deepseek', **i} for i in fetch_deepseek(reqs)])):
        rec = dead.get(site)
        # tripped=True 表示已熔断：跳过昂贵的浏览器刷新，直接采一次看是否已恢复
        is_tripped = bool(rec and rec.get('tripped'))
        if is_tripped:
            try:
                rows.extend(fn())
                # 恢复成功 → 解除熔断
                dead.pop(site, None)
                save_dead(dead)
                continue
            except Exception as e:
                # 熔断期内仍然失败：刷新时间戳以延长熔断窗口，计数继续累加
                errors[site] = str(e)
                rec = dead.get(site) or {}
                rec['count'] = rec.get('count', 0) + 1
                rec['at'] = now_ts
                dead[site] = rec
                save_dead(dead)
                continue

        for attempt in range(2):
            try:
                rows.extend(fn())
                # 采集成功 → 清掉该站点的失败计数
                if site in dead:
                    dead.pop(site, None)
                    save_dead(dead)
                break
            except Exception as e:
                auth_bad = is_auth_error(e)
                if attempt == 0 and not auth_bad:
                    # 非鉴权类错误（网络/解析）→ 刷新鉴权后重试一次
                    try:
                        refresh_auth_for(site, reqs)
                        cookies, reqs = load_auth()
                        continue
                    except Exception:
                        pass
                if auth_bad:
                    # 鉴权失效：刷新只对「浏览器会话仍有效但 token 过期」有用。
                    # 这里做一次最小化刷新并用指纹校验，失败则判定需人工重登。
                    try:
                        if refresh_auth_for(site, reqs):
                            cookies, reqs = load_auth()
                            rows.extend(fn())
                            dead.pop(site, None)
                            save_dead(dead)
                            break
                    except Exception as e2:
                        errors[site] = str(e2)
                    if site not in errors:
                        errors[site] = str(e) + '（需人工重登：点菜单「重新登录（浏览器）」）'
                    # 累计失败次数，达到阈值才熔断
                    prev = dead.get(site) or {}
                    cnt = prev.get('count', 0) + 1
                    rec = {'count': cnt, 'at': now_ts, 'reason': str(e)[:120]}
                    if cnt >= DEAD_AUTH_THRESHOLD:
                        rec['tripped'] = True
                        print(f'  ⚠️ {site} 连续 {cnt} 轮鉴权失败，已熔断 1 小时（跳过浏览器刷新）',
                              file=sys.stderr)
                    else:
                        print(f'  ⚠️ {site} 鉴权失败（第 {cnt}/{DEAD_AUTH_THRESHOLD} 轮），'
                              '建议尽快人工重登', file=sys.stderr)
                    dead[site] = rec
                    save_dead(dead)
                else:
                    errors[site] = str(e)
                break

    save_dead(dead)

    conn = sqlite3.connect(DB)
    init_db(conn)
    for r in rows:
        conn.execute(
            'INSERT INTO snapshots(ts, site, window, used_pct, used, total, remaining, reset_at, unlimited, meta)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?)',
            (ts, r['site'], r['window'], r['used_pct'], r['used'], r['total'],
             r['remaining'], r['reset_at'], int(r.get('unlimited', False)),
             json.dumps(r.get('meta'), ensure_ascii=False) if r.get('meta') else None))
    conn.commit()

    latest = {'ts': ts, 'rows': rows, 'errors': errors}
    with open(os.path.join(ROOT, 'data', 'latest.json'), 'w') as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)

    # 重新生成仪表盘 HTML：SwiftBar 菜单「打开仪表盘」直接读该文件，
    # 不刷新的话打开的是上一次采集的旧快照。
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import dashboard
        dashboard.generate()
    except Exception as e:
        print(f'  ⚠️ dashboard 生成失败: {e}', file=sys.stderr)

    if json_only:
        print(json.dumps(latest, ensure_ascii=False))
        return

    print(f"采集时间: {ts}")
    for r in rows:
        pct = f"{r['used_pct']:.1f}%" if r['used_pct'] is not None else 'n/a'
        extra = ''
        if r.get('unlimited'):
            extra = ' (∞ 无限制)'
        elif r['total']:
            extra = f" ({r['used']:.0f}/{r['total']:.0f}, 剩 {r['remaining']:.0f})"
        elif r['used'] and r['window'] == 'billing':
            extra = f" (近30天 {r['used']/1e6:.0f}M tokens)"
        # DeepSeek 余额模式：直接显示 余额/总额 + 当日消费金额
        if r['site'] == 'deepseek':
            if r['window'] == 'billing':
                extra = f" (余额 ¥{r['remaining']:.2f} / 总额 ¥{r['total']:.2f}, 已消费 ¥{r['used']:.2f})"
            elif r['window'] == 'today':
                meta = r.get('meta') or {}
                tok = meta.get('today_tokens')
                extra = f" ¥{r['used']:.2f}"
                if tok is not None:
                    extra += f"  ({tok/1e6:.1f}M tokens)"
        if r['site'] == 'mimo':
            if r['window'] == 'billing' and r['total']:
                extra = (f" ({r['used']/1e6:.0f}M/{r['total']/1e6:.0f}M credits"
                         f", 剩 {r['remaining']/1e6:.0f}M)")
            elif r['window'] == 'today':
                meta = r.get('meta') or {}
                tok = meta.get('today_tokens')
                reqn = meta.get('today_requests')
                extra = f" ({tok/1e6:.1f}M tokens, {reqn} 次请求)" if tok is not None else ''
        print(f"  {r['site']:9s} {r['window']:8s} 已用 {pct}{extra}")
    for site, err in errors.items():
        print(f"  {site}: 失败 - {err}")
        if 'auth failed' in err:
            print('    → 鉴权过期，请运行: node scripts/dump_cookies.js && node scripts/discover_api.js')


if __name__ == '__main__':
    main()
