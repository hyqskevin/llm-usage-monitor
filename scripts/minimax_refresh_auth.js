// MiniMax 会话刷新（非交互）：复用已登录的持久化 profile，
// 打开控制台触发用量接口，抓取鉴权头 + cookie 落盘到 data/minimax-auth.json。
//
// 与 minimax_login.js 的区别：login.js 是有头交互、需人工登录；
// 本脚本是全自动，用于「已登录但 cookie 过期」时刷新会话。
// 用法: node scripts/minimax_refresh_auth.js
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const PROFILE_DIR = path.join(ROOT, 'browser-profile');
const CONSOLE_URL = 'https://platform.minimax.cn/console/usage';
const OUT_AUTH = path.join(ROOT, 'data', 'minimax-auth.json');

const TARGETS = [
  /token_plan\/remains_percent/,
  /token_plan\/usage_summary/,
  /token_plan\/usage_overview/,
  /token_plan\/subscriptions/,
];
const INTERESTING = ['authorization', 'api-key', 'x-api-key', 'x-token', 'token',
                      'access-token', 'x-access-token', 'mm-token', 'x-mmx-token'];

(async () => {
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });

  const page = await context.newPage();
  const captured = [];
  const respMap = new Map();

  page.on('request', req => {
    const u = req.url();
    if (TARGETS.some(re => re.test(u))) {
      captured.push({ url: u, method: req.method(), headers: req.headers() });
    }
  });
  page.on('response', async resp => {
    const u = resp.url();
    if (TARGETS.some(re => re.test(u))) {
      let body = null;
      try { body = (await resp.body()).toString().slice(0, 4000); } catch (e) {}
      respMap.set(u, { status: resp.status(), body });
    }
  });

  await page.goto(CONSOLE_URL, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(12000);

  for (const r of captured) {
    const resp = respMap.get(r.url);
    if (resp) { r.status = resp.status; r.body = resp.body; }
  }

  const finalUrl = page.url();
  const loggedIn = !/unified-login|passport/i.test(finalUrl);
  const good = captured.find(r => r.status === 200 && r.body && r.body.includes('"status_code":0'));

  console.log('final url:', finalUrl);
  console.log('登录态:', loggedIn ? '在控制台' : '被重定向到登录页');
  console.log('捕获请求:');
  for (const r of captured) console.log(`   ${r.status} ${r.method} ${r.url.slice(0, 110)}`);

  if (!good) {
    console.log('\n❌ 未取到有效数据 —— profile 内 MiniMax 登录态已失效，需人工登录：');
    console.log('   node scripts/minimax_login.js');
    await context.close();
    process.exit(2);
  }

  const all = await context.cookies();
  const minimaxCookies = all.filter(c => /minimax/i.test(c.domain));

  const authHeaders = {};
  for (const r of captured) {
    for (const k of INTERESTING) {
      const v = r.headers[k] || r.headers[k.toLowerCase()];
      if (v) authHeaders[k] = v;
    }
  }

  fs.writeFileSync(OUT_AUTH, JSON.stringify({
    captured_at: new Date().toISOString(),
    console_url: CONSOLE_URL,
    cookies: minimaxCookies.map(c => ({
      name: c.name, value: c.value, domain: c.domain, path: c.path,
      httpOnly: c.httpOnly, secure: c.secure, expires: c.expires,
    })),
    auth_headers: authHeaders,
    requests: captured.map(r => ({ url: r.url, method: r.method, status: r.status, headers: r.headers, body: r.body })),
  }, null, 2));

  console.log('\n✅ 鉴权信息已保存:', OUT_AUTH);
  console.log('   cookie 数量:', minimaxCookies.length);
  console.log('   鉴权头:', Object.keys(authHeaders).length ? JSON.stringify(authHeaders) : '(无，仅靠 cookie)');

  await context.close();
})().catch(e => { console.error('ERROR:', e); process.exit(1); });
