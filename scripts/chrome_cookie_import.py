#!/usr/bin/env python3
"""从用户日常 Chrome 导入 bigmodel 鉴权材料，解决「网页明明已登录但插件取不到数据」。

背景（2026-11-10）：
  智谱 GLM 的 API 鉴权是**双因子**的 ——
    1. `authorization` 头：JWT（来自 bigmodel_token_production cookie）
    2. 配套会话 cookie `605ea18895037d16`（同域、httponly）
  只带其中任何一个，服务端都返回 401 / 1001。

  早期采集脚本只抓 `authorization` 头（靠 Playwright 打开页面被动抓包），
  因此一旦「脚本专用 browser-profile 的会话失效」就再也恢复不了，
  而用户日常 Chrome 里其实一直是登录态 —— 两个 profile 完全割裂。

本脚本直接从用户日常 Chrome 的 Cookie 库读出所需 cookie，落盘到
`data/chrome_auth.json`，供 fetch.py 在常规抓包失败时兜底。

macOS Chrome v10+ 的 cookie 值格式：前 32 字节是 HMAC-SHA256 校验值，
其后才是明文。解密 key 存在 Keychain 的 "Chrome Safe Storage" 项里，
通过 PBKDF2-HMAC-SHA1(saltysalt, 1003 次) 派生。

用法:
  python3 scripts/chrome_cookie_import.py            # 导入并验证
  python3 scripts/chrome_cookie_import.py --check    # 只检查，不写入
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'chrome_auth.json')

CHROME_DIR = os.path.expanduser('~/Library/Application Support/Google/Chrome')

# 智谱鉴权所需 cookie 白名单
WANT_COOKIES = (
    'bigmodel_token_production',   # JWT 本体
    '605ea18895037d16',            # 配套会话标识（httponly，缺了就 401）
    'acw_tc',                      # 阿里云 WAF
    'ssxmod_itna', 'ssxmod_itna2', # 风控
    'HMACCOUNT', '_TDID_CK',
)

# Chrome profile 目录 → 需要扫描的子目录
PROFILES = ('Default', 'Profile 1', 'Profile 2', 'Profile 3')


def _safe_storage_key():
    """从 Keychain 取 Chrome Safe Storage 密码，派生 AES key。"""
    p = subprocess.run(
        ['security', 'find-generic-password', '-w',
         '-s', 'Chrome Safe Storage', '-a', 'Chrome'],
        capture_output=True, text=True)
    pw = p.stdout.strip()
    if not pw:
        raise RuntimeError(
            '读不到 Keychain 的 "Chrome Safe Storage"（需在弹出的钥匙串授权框点允许）')
    # Chrome 的派生参数：PBKDF2-HMAC-SHA1，salt="saltysalt"，1003 轮，16 字节
    return hashlib.pbkdf2_hmac('sha1', pw.encode(), b'saltysalt', 1003, 16)


def _aes_decrypt(raw, key):
    """用 openssl 解密 Chrome 的 AES-128-CBC cookie 值。"""
    if raw[:3] not in (b'v10', b'v11'):
        return raw
    p = subprocess.run(
        ['openssl', 'enc', '-d', '-aes-128-cbc',
         '-K', key.hex(), '-iv', (b' ' * 16).hex()],
        input=raw[3:], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors='replace')[:200])
    return p.stdout


def _extract_jwt(value):
    """从解密后的 cookie 值里取出 JWT 明文。

    Chrome v10+ 的明文前 32 字节是 HMAC 校验值，其后才是真正的 cookie 内容；
    而 bigmodel_token_production 的内容本身又是一个 JWT。用正则直接取最稳。
    """
    m = re.search(rb'eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}',
                  value)
    if m:
        return m.group(0).decode('ascii')
    # 找不到 JWT 时，去掉 32 字节 HMAC 前缀后按 latin-1 兜底
    return value[32:].decode('latin-1', 'replace') if len(value) > 32 else \
        value.decode('latin-1', 'replace')


def _read_profile(profile_dir, key):
    """从一个 Chrome profile 的 Cookie 库里读出白名单 cookie。"""
    db = os.path.join(profile_dir, 'Cookies')
    if not os.path.exists(db):
        return {}
    out = {}
    # Chrome 运行时数据库有锁，复制一份再读
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    try:
        shutil.copy(db, tmp.name)
        con = sqlite3.connect(tmp.name)
        rows = con.execute(
            "SELECT name, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%bigmodel.cn%'").fetchall()
        for name, ev in rows:
            if name not in WANT_COOKIES:
                continue
            try:
                plain = _aes_decrypt(bytes(ev), key)
            except Exception:
                continue
            val = _extract_jwt(plain)
            if val and all(ord(c) < 128 for c in val):
                out[name] = val
        con.close()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return out


def collect():
    """扫描所有 Chrome profile，返回合并后的 bigmodel cookie。"""
    key = _safe_storage_key()
    merged = {}
    for prof in PROFILES:
        pdir = os.path.join(CHROME_DIR, prof)
        if not os.path.isdir(pdir):
            continue
        found = _read_profile(pdir, key)
        if found:
            # 后面的 profile 不覆盖前面的（Default 优先）
            for k, v in found.items():
                merged.setdefault(k, v)
            print(f'  {prof}: 找到 {len(found)} 个 cookie')
    return merged


def verify(cookies):
    """实调一次 bigmodel 接口，确认这组材料真的可用。"""
    import urllib.request
    reqs_path = os.path.join(ROOT, 'data', 'api-requests.json')
    try:
        with open(reqs_path) as f:
            reqs = json.load(f)
        r = reqs['bigmodel'][0]
    except Exception:
        # 没有历史请求头时用固定值
        r = {
            'url': 'https://bigmodel.cn/api/monitor/usage/quota/limit',
            'headers': {
                'bigmodel-organization': 'org-A35241c687Cf44F6B661ad20CD76C5E6',
                'bigmodel-project': 'proj_21eF8262859E4E509eb726B321336C62',
                'referer': 'https://bigmodel.cn/coding-plan/personal/usage',
                'accept': 'application/json, text/plain, */*',
                'accept-language': 'zh',
            },
        }
    headers = {k: v for k, v in r['headers'].items()
               if k in ('bigmodel-organization', 'bigmodel-project', 'referer',
                        'accept', 'accept-language', 'user-agent')}
    headers['authorization'] = cookies['bigmodel_token_production']
    headers['cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
    req = urllib.request.Request(r['url'], headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = json.loads(resp.read().decode())
    if body.get('code') != 200:
        raise RuntimeError(f"接口返回 code={body.get('code')} msg={body.get('msg')}")
    return body


def main():
    check_only = '--check' in sys.argv
    print('从 Chrome 导入 bigmodel 鉴权材料...')
    try:
        cookies = collect()
    except Exception as e:
        print(f'❌ 读取失败: {e}', file=sys.stderr)
        return 1
    if not cookies:
        print('❌ Chrome 里没找到 bigmodel 的登录 cookie', file=sys.stderr)
        return 1

    print('已读取 cookie:', ', '.join(cookies.keys()))
    if 'bigmodel_token_production' not in cookies:
        print('❌ 缺少 bigmodel_token_production（Chrome 里可能未登录 bigmodel）',
              file=sys.stderr)
        return 1

    print('验证中（实调 quota/limit 接口）...')
    try:
        body = verify(cookies)
    except Exception as e:
        print(f'❌ 验证失败: {e}', file=sys.stderr)
        return 1

    print(f'✅ 验证通过，code=200')
    for lim in body.get('data', {}).get('limits', []):
        print(f"   unit={lim.get('unit')} pct={lim.get('percentage')}% "
              f"used={lim.get('currentValue')}/{lim.get('usage')}")

    if check_only:
        print('（--check 模式，未写入文件）')
        return 0

    payload = {'cookies': cookies, 'source': 'chrome', 'verified': True}
    with open(OUT, 'w') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print('已写入', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
