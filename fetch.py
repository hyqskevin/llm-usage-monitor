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
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'data', 'usage.db')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')

WINDOWS = ('5h', 'week', 'billing')


def http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'User-Agent': UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def load_auth():
    cookies = json.load(open(os.path.join(ROOT, 'data', 'cookies.json')))['cookies']
    reqs = json.load(open(os.path.join(ROOT, 'data', 'api-requests.json')))
    return cookies, reqs


def refresh_auth():
    """鉴权过期时用无头浏览器从持久化 profile 重新导出 cookies 与请求头"""
    import subprocess
    env = dict(os.environ,
               NODE_PATH='/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules')
    node = '/Users/hanamaki_mac_mini/.workbuddy/binaries/node/versions/22.22.2-2/bin/node'
    for s in ('dump_cookies.js', 'discover_api.js'):
        subprocess.run([node, os.path.join(ROOT, 'scripts', s)],
                       cwd=ROOT, env=env, capture_output=True, timeout=120)


def cookie_header(cookies, domain):
    return '; '.join(f"{c['name']}={c['value']}" for c in cookies.get(domain, []))


def pick_headers(req, keys):
    return {k: v for k, v in req['headers'].items() if k in keys}


# ---------- 各站点解析 ----------

def fetch_bigmodel(reqs):
    r = reqs['bigmodel'][0]
    h = pick_headers(r, ('authorization', 'bigmodel-organization', 'bigmodel-project'))
    resp = http('GET', r['url'], h)
    if resp.get('code') != 200:
        raise RuntimeError(f"bigmodel auth failed: {resp.get('msg')}")
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
    code.append({'window': '5h', 'used_pct': rl5['ratio'] * 100,
                 'used': None, 'total': None, 'remaining': None,
                 'reset_at': rl5.get('resetTime')})
    code.append({'window': 'week', 'used_pct': rl7['ratio'] * 100,
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
    h = {'Cookie': cookie_header(cookies, 'minimaxi.com'), 'Accept': 'application/json'}
    resp = http('GET', 'https://www.minimaxi.com/backend/account/token_plan/remains_percent', h)
    if resp.get('base_resp', {}).get('status_code') != 0:
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
        if wpct:
            out.append({
                'window': 'week',
                'used_pct': float(wpct),
                'used': None, 'total': None, 'remaining': None,
                'reset_at': datetime.fromtimestamp(m['weekly_end_time'] / 1000, tz=timezone.utc).isoformat()
                            if m.get('weekly_end_time') else None,
            })
    # 月度：用 usage_summary 的 daily_token_usage 聚合当月消耗
    try:
        s = http('GET', 'https://www.minimaxi.com/backend/account/token_plan/usage_summary', h)
        daily = s.get('daily_token_usage') or []
        if daily:
            month_tokens = sum(daily[-30:])
            out.append({'window': 'billing', 'used_pct': None, 'used': float(month_tokens),
                        'total': None, 'remaining': None, 'reset_at': None})
    except Exception:
        pass
    return out


# ---------- 存储 ----------

def init_db(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        site TEXT NOT NULL,
        window TEXT NOT NULL,
        used_pct REAL, used REAL, total REAL, remaining REAL, reset_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ts ON snapshots(ts)')


def main():
    json_only = '--json' in sys.argv
    cookies, reqs = load_auth()
    ts = datetime.now(tz=timezone.utc).isoformat()
    rows, errors = [], {}
    refreshed = False

    def collect_kimi():
        kimi_rows, code_rows = fetch_kimi(reqs)
        return [{'site': 'kimi', **i} for i in kimi_rows] + \
               [{'site': 'kimi-code', **i} for i in code_rows]

    for site, fn in (('bigmodel', lambda: [{'site': 'bigmodel', **i} for i in fetch_bigmodel(reqs)]),
                     ('kimi', collect_kimi),
                     ('minimax', lambda: [{'site': 'minimax', **i} for i in fetch_minimax(cookies)])):
        for attempt in range(2):
            try:
                rows.extend(fn())
                break
            except Exception as e:
                if attempt == 0 and not refreshed:
                    # 鉴权可能过期，无头浏览器刷新后重试一次
                    try:
                        refresh_auth()
                        cookies, reqs = load_auth()
                        refreshed = True
                        continue
                    except Exception:
                        pass
                errors[site] = str(e)
                break

    conn = sqlite3.connect(DB)
    init_db(conn)
    for r in rows:
        conn.execute(
            'INSERT INTO snapshots(ts, site, window, used_pct, used, total, remaining, reset_at)'
            ' VALUES (?,?,?,?,?,?,?,?)',
            (ts, r['site'], r['window'], r['used_pct'], r['used'], r['total'],
             r['remaining'], r['reset_at']))
    conn.commit()

    latest = {'ts': ts, 'rows': rows, 'errors': errors}
    with open(os.path.join(ROOT, 'data', 'latest.json'), 'w') as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)

    if json_only:
        print(json.dumps(latest, ensure_ascii=False))
        return

    print(f"采集时间: {ts}")
    for r in rows:
        pct = f"{r['used_pct']:.1f}%" if r['used_pct'] is not None else 'n/a'
        extra = ''
        if r['total']:
            extra = f" ({r['used']:.0f}/{r['total']:.0f}, 剩 {r['remaining']:.0f})"
        elif r['used'] and r['window'] == 'billing':
            extra = f" (近30天 {r['used']/1e6:.0f}M tokens)"
        print(f"  {r['site']:9s} {r['window']:8s} 已用 {pct}{extra}")
    for site, err in errors.items():
        print(f"  {site}: 失败 - {err}")
        if 'auth failed' in err:
            print('    → 鉴权过期，请运行: node scripts/dump_cookies.js && node scripts/discover_api.js')


if __name__ == '__main__':
    main()
