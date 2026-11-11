// MiniMax 专用登录 + 鉴权抓取脚本
//
// 背景（2026-10-23 排查）：MiniMax 采集稳定返回 1016 invalid api key，
// 根因是 profile 里完全没有会话 cookie（29 个 cookie 全是埋点/风控）。
// 错误码含义：
//   1016 invalid api key        → remains_percent 还需要额外的 api key 头
//   1004 cookie is missing       → 缺登录会话 cookie
//
// 本脚本：打开登录页 → 人工登录 → 检测登录成功 → 抓真实请求头（含鉴权头）→ 落盘
// 用法: node scripts/minimax_login.js
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const PROFILE_DIR = path.join(ROOT, 'browser-profile');
const CONSOLE_URL = 'https://platform.minimax.cn/console/usage';
const OUT_AUTH = path.join(ROOT, 'data', 'minimax-auth.json');

// 需要抓的接口：用量百分比 + 月度汇总
const TARGETS = [
  /token_plan\/remains_percent/,
  /token_plan\/usage_summary/,
  /token_plan\/usage_overview/,
  /token_plan\/subscriptions/,
];

(async () => {
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });

  const page = await context.newPage();
  const captured = [];

  page.on('request', req => {
    const u = req.url();
    if (TARGETS.some(re => re.test(u))) {
      captured.push({
        url: u,
        method: req.method(),
        headers: req.headers(),
        status: null,
        body: null,
      });
    }
  });

  const respMap = new Map();
  page.on('response', async resp => {
    const u = resp.url();
    if (TARGETS.some(re => re.test(u))) {
      let body = null;
      try { body = (await resp.body()).toString().slice(0, 4000); } catch (e) {}
      respMap.set(u, { status: resp.status(), body });
    }
  });

  console.log('打开 MiniMax 登录页：', CONSOLE_URL);
  await page.goto(CONSOLE_URL, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(e => {
    console.log('goto 提示:', e.message);
  });

  console.log('\n>>> 请在浏览器窗口中完成登录（微信扫码 / 手机号 / 账号密码）。');
  console.log('>>> 登录成功并进入控制台后，本脚本会自动检测并保存。\n');

  // 轮询检测登录成功：URL 回到控制台 且 抓到 usage 相关接口
  let ok = false;
  for (let i = 0; i < 180; i++) {
    await page.waitForTimeout(1000);
    const url = page.url();
    if (/unified-login|passport|login/i.test(url)) continue;

    // 手动触发一次页面刷新，让接口重新发起
    if (i === 20 || i === 45) {
      await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {});
    }

    for (const r of captured) {
      const resp = respMap.get(r.url);
      if (resp) { r.status = resp.status; r.body = resp.body; }
    }
    const good = captured.find(r => r.status === 200 && r.body && r.body.includes('"status_code":0'));
    if (good) {
      console.log('✅ 检测到登录成功，接口已返回有效数据');
      ok = true;
      break;
    }
    if (i % 15 === 0 && i > 0) console.log(`   等待中… 当前 URL: ${url.slice(0, 90)}`);
  }

  for (const r of captured) {
    const resp = respMap.get(r.url);
    if (resp) { r.status = resp.status; r.body = resp.body; }
  }

  if (!ok) {
    console.log('\n⚠️ 未检测到登录成功（3 分钟内）。');
    console.log('   捕获到的请求：');
    for (const r of captured) console.log(`     ${r.status} ${r.method} ${r.url.slice(0, 100)}`);
    console.log('   浏览器保持打开，可继续登录后重跑本脚本。');
    await new Promise(resolve => context.on('close', resolve));
    await context.close();
    return;
  }

  // 抓 cookie（只取 minimax 域）
  const all = await context.cookies();
  const minimaxCookies = all.filter(c => /minimax/i.test(c.domain));

  // 找出带鉴权信息的请求头（排除浏览器自动加的噪声）
  const INTERESTING = ['authorization', 'api-key', 'x-api-key', 'x-token', 'token',
                        'access-token', 'x-access-token', 'mm-token', 'x-mmx-token'];
  const authHeaders = {};
  for (const r of captured) {
    for (const k of INTERESTING) {
      const v = r.headers[k] || r.headers[k.toLowerCase()];
      if (v) authHeaders[k] = v;
    }
  }

  const payload = {
    captured_at: new Date().toISOString(),
    console_url: CONSOLE_URL,
    cookies: minimaxCookies.map(c => ({
      name: c.name, value: c.value, domain: c.domain, path: c.path,
      httpOnly: c.httpOnly, secure: c.secure, expires: c.expires,
    })),
    auth_headers: authHeaders,
    requests: captured.map(r => ({
      url: r.url, method: r.method, status: r.status,
      headers: r.headers, body: r.body,
    })),
  };
  fs.writeFileSync(OUT_AUTH, JSON.stringify(payload, null, 2));

  console.log('\n=== 鉴权头 ===');
  console.log(Object.keys(authHeaders).length
    ? JSON.stringify(authHeaders, null, 1)
    : '（无额外鉴权头，仅靠 cookie）');
  console.log('\n=== cookie 数量 ===', minimaxCookies.length);
  for (const c of minimaxCookies) {
    console.log(`   ${c.name}  (${c.domain})  httpOnly=${c.httpOnly}  expires=${c.expires ? new Date(c.expires * 1000).toISOString() : 'session'}`);
  }
  console.log('\n已保存:', OUT_AUTH);

  await context.close();
})().catch(e => { console.error('ERROR:', e); process.exit(1); });
